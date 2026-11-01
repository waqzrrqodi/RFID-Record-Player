"""
local_media_player.py
Lightweight local audio/media file player using mpv via subprocess and its IPC socket.

mpv is available on Raspberry Pi OS via: apt install mpv
No Python bindings required — all control goes through mpv's JSON IPC protocol.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time

# Default directory to look for media files when no path is configured
DEFAULT_MEDIA_DIR = os.path.expanduser("~/Music")

# Supported audio/video extensions for file browsing
SUPPORTED_EXTENSIONS = {
    ".mp3", ".flac", ".ogg", ".wav", ".aac", ".m4a",
    ".opus", ".wma", ".aiff", ".mp4", ".mkv", ".webm",
}

MPV_IPC_SOCKET = "/tmp/mpv-record-player.sock"


class LocalMediaPlayer:
    """
    Controls mpv for local file playback.

    mpv is started headlessly with an IPC socket so we can query and control
    playback without restarting the process for every new track.
    """

    def __init__(self, media_dir: str = DEFAULT_MEDIA_DIR):
        self.media_dir = media_dir
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._current_path: str | None = None
        self._paused = False

    # ------------------------------------------------------------------
    # IPC helpers
    # ------------------------------------------------------------------

    def _send_ipc(self, command: list, timeout: float = 1.0) -> dict | None:
        """Send a JSON command to the mpv IPC socket and return the response."""
        sock_path = MPV_IPC_SOCKET
        if not os.path.exists(sock_path):
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(sock_path)
                payload = json.dumps({"command": command}) + "\n"
                s.sendall(payload.encode())
                raw = s.recv(4096)
                if raw:
                    # mpv may send multiple lines; take the last complete JSON obj
                    lines = [l for l in raw.decode(errors="replace").splitlines() if l.strip()]
                    for line in reversed(lines):
                        try:
                            return json.loads(line)
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        return None

    def _mpv_is_running(self) -> bool:
        """True if the mpv process we started is still alive."""
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------

    def play(self, path: str) -> bool:
        """
        Start playing a local file. If mpv is already running the same file,
        seek back to 0 and resume. Otherwise restart mpv with the new file.
        Returns True on success.
        """
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            print(f"[LocalMediaPlayer] File not found: {path}")
            return False

        with self._lock:
            if self._mpv_is_running() and path == self._current_path:
                # Same file — seek to start and resume
                self._send_ipc(["seek", 0, "absolute"])
                self._send_ipc(["set_property", "pause", False])
                self._paused = False
                print(f"[LocalMediaPlayer] Resuming: {path}")
                return True

            # New file — kill any existing process and start fresh
            self._kill_mpv()
            self._current_path = path
            self._paused = False

            cmd = [
                "mpv",
                "--no-video",
                "--quiet",
                f"--input-ipc-server={MPV_IPC_SOCKET}",
                path,
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Give mpv a moment to open the socket before callers query it
                time.sleep(0.4)
                print(f"[LocalMediaPlayer] Playing: {path}")
                return True
            except FileNotFoundError:
                print("[LocalMediaPlayer] mpv not found. Install it with: sudo apt install mpv")
                return False

    def pause(self):
        """Pause playback."""
        with self._lock:
            if self._mpv_is_running():
                self._send_ipc(["set_property", "pause", True])
                self._paused = True
                print("[LocalMediaPlayer] Paused")

    def resume(self):
        """Resume paused playback."""
        with self._lock:
            if self._mpv_is_running():
                self._send_ipc(["set_property", "pause", False])
                self._paused = False
                print("[LocalMediaPlayer] Resumed")

    def stop(self):
        """Stop playback and kill mpv."""
        with self._lock:
            self._kill_mpv()
            self._current_path = None
            self._paused = False
            print("[LocalMediaPlayer] Stopped")

    def seek(self, seconds: float):
        """Seek to an absolute position in seconds."""
        self._send_ipc(["seek", seconds, "absolute"])

    def set_volume(self, volume: int):
        """Set volume (0–100)."""
        self._send_ipc(["set_property", "volume", max(0, min(100, volume))])

    def _kill_mpv(self):
        """Terminate the mpv process and remove the socket."""
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        # Clean up stale socket
        try:
            os.unlink(MPV_IPC_SOCKET)
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """
        Return current playback status.
        {playing, paused, path, position_s, duration_s, filename}
        """
        if not self._mpv_is_running():
            return {
                "playing": False,
                "paused": False,
                "path": None,
                "position_s": 0,
                "duration_s": 0,
                "filename": None,
            }

        pos_resp = self._send_ipc(["get_property", "time-pos"])
        dur_resp = self._send_ipc(["get_property", "duration"])
        pause_resp = self._send_ipc(["get_property", "pause"])

        position = (pos_resp or {}).get("data") or 0
        duration = (dur_resp or {}).get("data") or 0
        paused = (pause_resp or {}).get("data", False)

        fname = os.path.basename(self._current_path) if self._current_path else None

        return {
            "playing": not paused,
            "paused": paused,
            "path": self._current_path,
            "position_s": round(position, 1),
            "duration_s": round(duration, 1),
            "filename": fname,
        }

    # ------------------------------------------------------------------
    # Media file discovery
    # ------------------------------------------------------------------

    @staticmethod
    def list_files(directory: str) -> list[dict]:
        """
        Recursively list supported audio files under `directory`.
        Returns [{"path": str, "filename": str, "size_mb": float, "ext": str}]
        """
        results = []
        if not os.path.isdir(directory):
            return results
        for root, _dirs, files in os.walk(directory):
            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    full = os.path.join(root, fname)
                    try:
                        size = os.path.getsize(full) / (1024 * 1024)
                    except OSError:
                        size = 0.0
                    results.append({
                        "path": full,
                        "filename": fname,
                        "rel_path": os.path.relpath(full, directory),
                        "size_mb": round(size, 2),
                        "ext": ext.lstrip("."),
                    })
        return results
