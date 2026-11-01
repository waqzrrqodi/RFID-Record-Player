"""
record_player.py
Main entry point for the RFID Record Player.

RFID map entry formats (rfid.json):
  - Legacy bare string (Spotify URI):
      "123456": "spotify:album:..."
  - New object format:
      "123456": {"type": "spotify", "uri": "spotify:album:..."}
      "123456": {"type": "local",   "path": "/home/pi/Music/album.mp3"}
"""
from __future__ import annotations

import json
import os
import sys
import RPi.GPIO as GPIO
import threading
import time
import spotipy
from dotenv import load_dotenv
from gpiozero import DigitalInputDevice, DigitalOutputDevice
from gpiozero.pins.lgpio import LGPIOFactory
from mfrc522 import SimpleMFRC522
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyOAuth

from bluetooth_manager import BluetoothManager
from local_media_player import LocalMediaPlayer
import web_server

HALL_SENSOR_PIN = 17
STEPPER_PINS = [14, 15, 18, 23]
RFID_FILE = "rfid.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_rfid_entry(raw) -> dict | None:
    """
    Normalise an rfid.json value to {"type": ..., ...}.

    Accepts:
      - dict  {"type": "spotify", "uri": "..."} or {"type": "local", "path": "..."}
      - str   "spotify:..." → {"type": "spotify", "uri": "..."}
      - None  → None
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.startswith("spotify:"):
        return {"type": "spotify", "uri": raw}
    return None


# ---------------------------------------------------------------------------
# SpotifyController
# ---------------------------------------------------------------------------

class SpotifyController:
    def __init__(self):
        self.playback_cache = {}
        self.rfid_map: dict = {}
        self._init_spotify_client()
        self._init_rfid_map()

    def _init_spotify_client(self):
        load_dotenv()

        client_id = os.getenv("SPOTIFY_CLIENT_ID")
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN")
        redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI")

        if not all([client_id, client_secret, refresh_token, redirect_uri]):
            print("Note: Spotify credentials not configured in .env. Local media playback is active.")
            self.sp = None
            return

        try:
            auth = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope="user-read-playback-state,user-modify-playback-state",
                open_browser=False,
                cache_handler=MemoryCacheHandler(),
            )
            auth.refresh_access_token(refresh_token)
            self.sp = spotipy.Spotify(auth_manager=auth)
            print("Spotify client initialized successfully.")
        except Exception as e:
            print(f"Warning: Failed to initialize Spotify client ({e}). Local playback available.")
            self.sp = None

    def _init_rfid_map(self):
        rfid_map = {}
        try:
            with open(RFID_FILE, "r") as file:
                rfid_map = json.load(file)
            if not rfid_map:
                print("Warning: RFID map is empty.")
        except FileNotFoundError:
            print(f"Warning: RFID map file {RFID_FILE} not found.")
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON: {e}")
        self.rfid_map = rfid_map

    def play_uri(self, uri: str):
        """Play a Spotify URI directly (called by PlaybackRouter)."""
        if not self.sp:
            print(f"[Spotify] Cannot play {uri}: Spotify credentials not configured. Configure via setup.py.")
            return

        spotify_args = {}
        if uri.startswith("spotify:track:"):
            spotify_args["uris"] = [uri]
            if uri == self.playback_cache.get("track_uri"):
                print("Track URI same, resuming")
                spotify_args["position_ms"] = self.playback_cache.get("progress_ms")
        else:
            spotify_args["context_uri"] = uri
            if uri == self.playback_cache.get("context_uri"):
                if uri.startswith("spotify:artist:"):
                    print("Context URI is artist, unable to resume playback")
                else:
                    print("Context URI same, resuming")
                    spotify_args["offset"] = {"uri": self.playback_cache.get("track_uri")}
                    spotify_args["position_ms"] = self.playback_cache.get("progress_ms")
        print(f"[Spotify] start_playback args: {spotify_args}")
        try:
            self.sp.start_playback(**spotify_args)
        except Exception as e:
            print(f"[Spotify] Failed to start playback: {e}")

    def pause(self):
        if not self.sp:
            return
        try:
            self.sp.pause_playback()
        except Exception as e:
            print(f"[Spotify] Failed to pause: {e}")
        self._store_playback()

    def _store_playback(self):
        if not self.sp:
            return
        try:
            playback = self.sp.current_playback()
            if not playback:
                return
            context = playback.get("context") or {}
            self.playback_cache = {
                "type": context.get("type"),
                "context_uri": context.get("uri"),
                "progress_ms": playback.get("progress_ms") or 0,
                "track_uri": playback.get("item", {}).get("uri"),
            }
            print(f"[Spotify] Stored playback: {self.playback_cache}")
        except Exception as e:
            print(f"[Spotify] Failed to store playback: {e}")


# ---------------------------------------------------------------------------
# PlaybackRouter – routes to Spotify or local player based on entry type
# ---------------------------------------------------------------------------

class PlaybackRouter:
    """
    Single point of control for all playback.
    Reads the rfid_map (shared with web_server), resolves whether each RFID
    tag maps to Spotify or a local file, and delegates accordingly.
    """

    def __init__(self, spotify: SpotifyController, local: LocalMediaPlayer):
        self.spotify = spotify
        self.local = local
        # Shared map reference — updated live by web_server writes
        self.rfid_map: dict = spotify.rfid_map

        self._last_type: str | None = None  # "spotify" | "local"

    def play(self, rfid_id: str | int):
        rfid_id = str(rfid_id)
        raw = self.rfid_map.get(rfid_id)
        entry = parse_rfid_entry(raw)

        if not entry:
            print(f"[Router] No mapping for RFID {rfid_id}")
            return

        entry_type = entry.get("type")
        print(f"[Router] RFID {rfid_id} → {entry_type}")

        if entry_type == "spotify":
            # Stop local player if it was active
            self.local.stop()
            self._last_type = "spotify"
            self.spotify.play_uri(entry["uri"])

        elif entry_type == "local":
            # Pause Spotify if it was playing
            try:
                self.spotify.sp.pause_playback()
            except Exception:
                pass
            self._last_type = "local"
            self.local.play(entry["path"])

        else:
            print(f"[Router] Unknown entry type: {entry_type}")

    def pause(self):
        """Pause whichever source is currently active."""
        if self._last_type == "local":
            self.local.pause()
        else:
            self.spotify.pause()

    def resume(self):
        """Resume whichever source was previously active."""
        if self._last_type == "local":
            self.local.resume()
        else:
            try:
                self.spotify.sp.start_playback()
            except Exception:
                pass

    def stop_all(self):
        """Hard stop — called on shutdown."""
        self.local.stop()
        try:
            self.spotify.sp.pause_playback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# StepperMotor
# ---------------------------------------------------------------------------

class StepperMotor:
    STEP_SEQUENCE = [
        [1, 0, 0, 1],
        [1, 0, 0, 0],
        [1, 1, 0, 0],
        [0, 1, 0, 0],
        [0, 1, 1, 0],
        [0, 0, 1, 0],
        [0, 0, 1, 1],
        [0, 0, 0, 1],
    ]
    STEP_DELAY = 0.002

    def __init__(self):
        self.pins = [DigitalOutputDevice(pin) for pin in STEPPER_PINS]
        self._running = False
        self._thread = None

    def _run(self):
        print("Stepper motor thread started")
        while self._running:
            for step in self.STEP_SEQUENCE:
                for pin, value in zip(self.pins, step):
                    pin.value = value
                time.sleep(self.STEP_DELAY)
        self._stop_pins()
        print("Stepper motor thread stopped")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _stop_pins(self):
        for pin in self.pins:
            pin.off()


# ---------------------------------------------------------------------------
# RecordPlayer
# ---------------------------------------------------------------------------

class RecordPlayer:
    def __init__(self, router: PlaybackRouter, motor: StepperMotor, rfid, hall_sensor):
        self.router = router
        self.motor = motor
        self.rfid = rfid
        self.hall_sensor = hall_sensor

        self.current_rfid = None
        self.spinning = False

    def update(self):
        magnet_detected = self.hall_sensor.value

        if magnet_detected and not self.spinning:
            print("Magnet detected → start")
            self.spinning = True
            self.motor.start()

        elif not magnet_detected and self.spinning:
            print("Magnet lost → stop")
            self.spinning = False
            self.current_rfid = None
            self.motor.stop()
            self.router.pause()

        if self.spinning:
            rfid_id = self.rfid.read_id_no_block()
            if rfid_id and rfid_id != self.current_rfid:
                print(f"RFID changed: {rfid_id}")
                self.current_rfid = rfid_id
                self.router.play(rfid_id)


def get_lan_ips() -> list[str]:
    """Detect LAN IP addresses for display."""
    import socket
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return ips


def main():
    print("=== Starting RFID Record Player ===")

    # 1. Playback controllers (local & Spotify)
    spotify = SpotifyController()
    local = LocalMediaPlayer()
    router = PlaybackRouter(spotify=spotify, local=local)

    # 2. Bluetooth manager
    bt = BluetoothManager()

    # 3. Start Web Management Server IMMEDIATELY (port 8080)
    web_server.init(
        bluetooth_manager=bt,
        spotify_controller=spotify,
        local_media_player=local,
        playback_router=router,
        record_player=None,
    )
    web_server.run_server(host="0.0.0.0", port=8082)

    lan_ips = get_lan_ips()
    print("--------------------------------------------------")
    print("✓ Web management interface is LIVE:")
    print("  http://record-player.local:8082")
    for ip in lan_ips:
        print(f"  http://{ip}:8082")
    print("--------------------------------------------------")

    # 4. Bluetooth auto-reconnect in background thread (non-blocking)
    def bt_startup():
        try:
            bt.power_on()
            print("[Bluetooth] Adapter powered on")
            paired = bt.get_paired_devices()
            for device in paired:
                if device.get("trusted") and not device.get("connected"):
                    print(f"[Bluetooth] Reconnecting: {device['name']} ({device['mac']})")
                    if bt.connect(device["mac"]):
                        bt.set_as_default_sink(device["mac"])
                        print(f"[Bluetooth] Connected to {device['name']}")
                        break
        except Exception as e:
            print(f"[Bluetooth] Warning during startup: {e}")

    threading.Thread(target=bt_startup, daemon=True, name="bt-init").start()

    # 5. Initialize hardware (Stepper, RFID, Hall Effect)
    motor = None
    player = None
    try:
        motor = StepperMotor()
        rfid = SimpleMFRC522()
        hall_sensor = DigitalInputDevice(HALL_SENSOR_PIN, pull_up=True, pin_factory=LGPIOFactory())

        player = RecordPlayer(
            router=router,
            motor=motor,
            rfid=rfid,
            hall_sensor=hall_sensor,
        )
        web_server._record_player = player
        print("✓ Hardware components initialized successfully")
    except Exception as e:
        print(f"Warning: Hardware (GPIO/RFID/Hall) initialization failed: {e}")
        print("Record player hardware loop disabled, but web server remains fully operational.")

    # 6. Main loop
    try:
        while True:
            if player:
                player.update()
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        router.stop_all()
        if motor:
            motor.stop()
        try:
            GPIO.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    main()