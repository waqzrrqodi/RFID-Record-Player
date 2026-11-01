"""
web_server.py
Lightweight Flask web server providing a REST API and serving the management UI.
Runs in a background thread alongside the main record player loop.

RFID map format (rfid.json):
  Legacy:  "rfid_id": "spotify:..."
  New:     "rfid_id": {"type": "spotify", "uri": "..."}
           "rfid_id": {"type": "local",   "path": "/abs/path/to/file.mp3"}
"""
from __future__ import annotations

import json
import os
import threading
try:
    from werkzeug.utils import secure_filename
except ImportError:
    def secure_filename(filename: str) -> str:
        import re
        return re.sub(r'[^a-zA-Z0-9_.-]', '_', os.path.basename(filename))

from flask import Flask, jsonify, request, send_from_directory, abort

from local_media_player import LocalMediaPlayer, SUPPORTED_EXTENSIONS
from file_validator import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE_BYTES,
    validate_upload,
    is_path_within_root,
)
import firewall_manager

# ---------------------------------------------------------------------------
# Shared state injected by main()
# ---------------------------------------------------------------------------

_bluetooth_manager = None
_spotify_controller = None
_local_media_player: LocalMediaPlayer | None = None
_playback_router = None
_record_player = None

RFID_FILE = "rfid.json"
RFID_LABELS_FILE = "rfid_labels.json"
ENV_FILE = ".env"

app = Flask(__name__, static_folder="web/static")

# Max upload size: 100 MB
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE_BYTES


@app.after_request
def apply_security_headers(response):
    """Enforce defense-in-depth HTTP security headers."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://www.gstatic.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data: https: http:; "
        "media-src 'self' blob:;"
    )
    return response


def init(bluetooth_manager, spotify_controller, local_media_player,
         playback_router, record_player):
    """Called once from main() to inject shared state into the web layer."""
    global _bluetooth_manager, _spotify_controller, _local_media_player
    global _playback_router, _record_player
    _bluetooth_manager = bluetooth_manager
    _spotify_controller = spotify_controller
    _local_media_player = local_media_player
    _playback_router = playback_router
    _record_player = record_player


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("web/static", "index.html")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    player = _record_player
    bt_device = _bluetooth_manager.get_connected_device() if _bluetooth_manager else None

    # Resolve what the current RFID maps to (for display in the header)
    current_rfid = player.current_rfid if player else None
    current_entry = None
    if current_rfid and _spotify_controller:
        raw = _spotify_controller.rfid_map.get(str(current_rfid))
        if isinstance(raw, dict):
            current_entry = raw
        elif isinstance(raw, str):
            current_entry = {"type": "spotify", "uri": raw}

    local_status = _local_media_player.get_status() if _local_media_player else {}

    return jsonify({
        "spinning": player.spinning if player else False,
        "current_rfid": current_rfid,
        "current_entry": current_entry,
        "playback_cache": _spotify_controller.playback_cache if _spotify_controller else {},
        "local": local_status,
        "bluetooth": {
            "connected": bt_device is not None,
            "device": bt_device,
        },
    })


# ---------------------------------------------------------------------------
# Bluetooth endpoints
# ---------------------------------------------------------------------------

@app.route("/api/bluetooth/scan", methods=["POST"])
def bt_scan():
    if not _bluetooth_manager:
        return jsonify({"error": "Bluetooth manager not available"}), 503
    duration = int(request.json.get("duration", 8)) if request.is_json else 8
    devices = _bluetooth_manager.scan(duration=duration)
    device_list = [{"mac": mac, "name": name} for mac, name in devices.items()]
    return jsonify({"devices": device_list})


@app.route("/api/bluetooth/pair", methods=["POST"])
def bt_pair():
    if not _bluetooth_manager:
        return jsonify({"error": "Bluetooth manager not available"}), 503
    data = request.get_json(silent=True) or {}
    mac = data.get("mac", "").strip().upper()
    if not mac:
        return jsonify({"error": "mac address is required"}), 400
    result = _bluetooth_manager.pair_and_connect(mac)
    if result["connected"]:
        _bluetooth_manager.set_as_default_sink(mac)
    return jsonify(result)


@app.route("/api/bluetooth/disconnect", methods=["POST"])
def bt_disconnect():
    data = request.get_json(silent=True) or {}
    mac = data.get("mac", "").strip().upper()
    if not mac:
        return jsonify({"error": "mac address is required"}), 400
    success = _bluetooth_manager.disconnect(mac)
    return jsonify({"success": success})


@app.route("/api/bluetooth/remove", methods=["POST"])
def bt_remove():
    data = request.get_json(silent=True) or {}
    mac = data.get("mac", "").strip().upper()
    if not mac:
        return jsonify({"error": "mac address is required"}), 400
    success = _bluetooth_manager.remove(mac)
    return jsonify({"success": success})


@app.route("/api/bluetooth/paired")
def bt_paired():
    if not _bluetooth_manager:
        return jsonify({"devices": []})
    devices = _bluetooth_manager.get_paired_devices()
    return jsonify({"devices": devices})


@app.route("/api/bluetooth/connect", methods=["POST"])
def bt_connect():
    data = request.get_json(silent=True) or {}
    mac = data.get("mac", "").strip().upper()
    if not mac:
        return jsonify({"error": "mac address is required"}), 400
    success = _bluetooth_manager.connect(mac)
    if success:
        _bluetooth_manager.set_as_default_sink(mac)
    return jsonify({"success": success})


# ---------------------------------------------------------------------------
# RFID map endpoints
# ---------------------------------------------------------------------------

def _read_rfid_file() -> dict:
    try:
        with open(RFID_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_rfid_file(data: dict):
    with open(RFID_FILE, "w") as f:
        json.dump(data, f, indent=4)
    # Sync into in-memory map on both SpotifyController and PlaybackRouter
    if _spotify_controller:
        _spotify_controller.rfid_map = data
    if _playback_router:
        _playback_router.rfid_map = data


def _read_labels_file() -> dict:
    try:
        with open(RFID_LABELS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_labels_file(labels: dict):
    with open(RFID_LABELS_FILE, "w") as f:
        json.dump(labels, f, indent=4)


@app.route("/api/rfid")
def rfid_list():
    """Return the full RFID map with labels merged in."""
    rfid_map = _read_rfid_file()
    labels = _read_labels_file()
    result = {}
    for rfid_id, raw in rfid_map.items():
        if isinstance(raw, str):
            entry = {"type": "spotify", "uri": raw}
        else:
            entry = dict(raw)
        entry["label"] = labels.get(rfid_id, "")
        result[rfid_id] = entry
    return jsonify(result)


@app.route("/api/rfid", methods=["POST"])
def rfid_add():
    """
    Add or update an RFID mapping.

    Body (JSON):
      {
        "rfid_id":  "123456789",
        "type":     "spotify" | "local",
        "uri":      "spotify:album:...",     // for type=spotify
        "path":     "/home/pi/Music/x.mp3", // for type=local
        "label":    "Abbey Road"             // optional friendly name
      }
    """
    data = request.get_json(silent=True) or {}
    rfid_id = str(data.get("rfid_id", "")).strip()
    entry_type = str(data.get("type", "spotify")).strip().lower()
    label = str(data.get("label", "")).strip()

    if not rfid_id:
        return jsonify({"error": "rfid_id is required"}), 400

    if entry_type == "spotify":
        uri = str(data.get("uri", "")).strip()
        if not uri:
            return jsonify({"error": "uri is required for type=spotify"}), 400
        parts = uri.split(":")
        if not uri.startswith("spotify:") or len(parts) < 3 or parts[1] not in ("track", "album", "playlist", "artist"):
            return jsonify({"error": "Invalid Spotify URI format (expected spotify:{track|album|playlist|artist}:...)"}), 400
        entry_value = {"type": "spotify", "uri": uri}

    elif entry_type == "local":
        path = os.path.expanduser(str(data.get("path", "")).strip())
        if not path:
            return jsonify({"error": "path is required for type=local"}), 400
        if not os.path.isfile(path):
            return jsonify({"error": f"File not found: {path}"}), 400
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return jsonify({"error": f"Unsupported file type: {ext}"}), 400
        entry_value = {"type": "local", "path": path}

    else:
        return jsonify({"error": "type must be 'spotify' or 'local'"}), 400

    rfid_map = _read_rfid_file()
    rfid_map[rfid_id] = entry_value
    _write_rfid_file(rfid_map)

    labels = _read_labels_file()
    if label:
        labels[rfid_id] = label
    elif rfid_id in labels and not label:
        pass  # preserve existing label if no new one provided
    _write_labels_file(labels)

    return jsonify({"success": True, "rfid_id": rfid_id, "entry": entry_value})


@app.route("/api/rfid/<rfid_id>", methods=["DELETE"])
def rfid_delete(rfid_id):
    rfid_map = _read_rfid_file()
    if rfid_id not in rfid_map:
        return jsonify({"error": "RFID ID not found"}), 404
    del rfid_map[rfid_id]
    _write_rfid_file(rfid_map)
    # Also clean up label
    labels = _read_labels_file()
    labels.pop(rfid_id, None)
    _write_labels_file(labels)
    return jsonify({"success": True})


@app.route("/api/rfid/labels")
def rfid_labels():
    return jsonify(_read_labels_file())


# ---------------------------------------------------------------------------
# Local media file endpoints
# ---------------------------------------------------------------------------

@app.route("/api/media/files")
def media_files():
    """
    List local media files available for mapping.
    Query params:
      dir – directory to scan (default: ~/Music)
    """
    if not _local_media_player:
        return jsonify({"error": "Local media player not available"}), 503

    base_dir = os.path.realpath(os.path.expanduser(_local_media_player.media_dir))
    req_dir = request.args.get("dir", base_dir)
    directory = os.path.realpath(os.path.expanduser(req_dir))

    # Path traversal check: must stay within allowed media root
    if not is_path_within_root(directory, base_dir):
        return jsonify({"files": [], "directory": directory, "error": "Access denied: directory outside allowed media root"}), 403

    if not os.path.isdir(directory):
        return jsonify({"files": [], "directory": directory, "error": "Directory not found"})

    files = LocalMediaPlayer.list_files(directory)
    return jsonify({"files": files, "directory": directory})


@app.route("/api/media/upload", methods=["POST"])
def media_upload():
    """
    Upload a media file to the music directory.
    Validates file extension, magic bytes, double extensions, and prevents directory traversal.
    multipart/form-data field: file
    Optional form field: dir (target directory, defaults to ~/Music)
    """
    if not _local_media_player:
        return jsonify({"error": "Local media player not available"}), 503

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    base_dir = os.path.realpath(os.path.expanduser(_local_media_player.media_dir))
    raw_target = request.form.get("dir", base_dir)
    target_dir = os.path.realpath(os.path.expanduser(raw_target))
    os.makedirs(target_dir, exist_ok=True)

    # Comprehensive security validation:
    # 1. Allowed extension check
    # 2. Dangerous double extensions (e.g. .php.mp3)
    # 3. Path traversal outside music root
    # 4. Magic byte inspection & executable/script blocklist
    # 5. File size limits
    is_valid, msg, dest_path = validate_upload(
        stream=f.stream,
        filename=f.filename,
        target_dir=target_dir,
        base_dir=base_dir,
    )
    if not is_valid:
        return jsonify({"error": f"Security validation failed: {msg}"}), 400

    f.save(dest_path)

    return jsonify({
        "success": True,
        "path": dest_path,
        "filename": os.path.basename(dest_path),
        "size_mb": round(os.path.getsize(dest_path) / (1024 * 1024), 2),
        "message": "File verified and uploaded successfully",
    })


@app.route("/api/media/play", methods=["POST"])
def media_play():
    """Directly play a local file (for testing/manual control)."""
    if not _local_media_player:
        return jsonify({"error": "Local media player not available"}), 503
    data = request.get_json(silent=True) or {}
    path = os.path.expanduser(data.get("path", "").strip())
    if not path or not os.path.isfile(path):
        return jsonify({"error": "Valid path is required"}), 400
    success = _local_media_player.play(path)
    return jsonify({"success": success})


@app.route("/api/media/pause", methods=["POST"])
def media_pause():
    if not _local_media_player:
        return jsonify({"error": "Local media player not available"}), 503
    _local_media_player.pause()
    return jsonify({"success": True})


@app.route("/api/media/resume", methods=["POST"])
def media_resume():
    if not _local_media_player:
        return jsonify({"error": "Local media player not available"}), 503
    _local_media_player.resume()
    return jsonify({"success": True})


@app.route("/api/media/stop", methods=["POST"])
def media_stop():
    if not _local_media_player:
        return jsonify({"error": "Local media player not available"}), 503
    _local_media_player.stop()
    return jsonify({"success": True})


@app.route("/api/media/status")
def media_status():
    if not _local_media_player:
        return jsonify({"error": "Local media player not available"}), 503
    return jsonify(_local_media_player.get_status())


@app.route("/api/media/dir")
def media_dir():
    """Return the configured media directory."""
    if not _local_media_player:
        return jsonify({"dir": os.path.expanduser("~/Music")})
    return jsonify({"dir": _local_media_player.media_dir})


# ---------------------------------------------------------------------------
# Spotify playback control
# ---------------------------------------------------------------------------

@app.route("/api/playback/pause", methods=["POST"])
def playback_pause():
    """Pause whichever backend is active."""
    if _playback_router:
        _playback_router.pause()
        return jsonify({"success": True})
    # Fallback to Spotify only
    if not _spotify_controller:
        return jsonify({"error": "No playback controller available"}), 503
    try:
        _spotify_controller.pause()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/playback/resume", methods=["POST"])
def playback_resume():
    try:
        if _playback_router:
            _playback_router.resume()
        elif _local_media_player and _local_media_player.get_status().get("paused"):
            _local_media_player.resume()
        elif _spotify_controller and _spotify_controller.sp:
            _spotify_controller.sp.start_playback()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/playback/current")
def playback_current():
    """Return currently playing info from either backend."""
    # Check local player first
    if _local_media_player:
        local_st = _local_media_player.get_status()
        if local_st.get("playing") or local_st.get("paused"):
            return jsonify({
                "source": "local",
                "playing": local_st.get("playing", False),
                "paused": local_st.get("paused", False),
                "track_name": local_st.get("filename"),
                "artist_name": None,
                "album_name": None,
                "album_art": None,
                "progress_ms": int((local_st.get("position_s") or 0) * 1000),
                "duration_ms": int((local_st.get("duration_s") or 0) * 1000),
                "path": local_st.get("path"),
            })

    # Fall back to Spotify
    if not _spotify_controller or not _spotify_controller.sp:
        return jsonify({"playing": False})
    try:
        playback = _spotify_controller.sp.current_playback()
        if not playback:
            return jsonify({"playing": False})
        item = playback.get("item") or {}
        artists = item.get("artists") or [{}]
        album = item.get("album") or {}
        images = album.get("images") or [{}]
        return jsonify({
            "source": "spotify",
            "playing": playback.get("is_playing", False),
            "paused": not playback.get("is_playing", True),
            "track_name": item.get("name"),
            "artist_name": ", ".join(a.get("name", "") for a in artists),
            "album_name": album.get("name"),
            "album_art": images[0].get("url") if images else None,
            "progress_ms": playback.get("progress_ms"),
            "duration_ms": item.get("duration_ms"),
            "path": None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Security & Firewall endpoints
# ---------------------------------------------------------------------------

@app.route("/api/security/status")
def security_status():
    """Return security posture: file validation config and UFW firewall status."""
    fw_status = firewall_manager.get_firewall_status()
    return jsonify({
        "upload_security": {
            "allowed_extensions": sorted(list(ALLOWED_EXTENSIONS)),
            "max_size_mb": MAX_FILE_SIZE_BYTES // (1024 * 1024),
            "magic_bytes_check": True,
            "directory_traversal_protection": True,
            "double_extension_protection": True,
        },
        "firewall": fw_status,
    })


@app.route("/api/security/firewall", methods=["POST"])
def security_firewall():
    """Configure firewall preset ('strict', 'standard', 'disable')."""
    data = request.get_json(silent=True) or {}
    preset = str(data.get("preset", "strict")).strip().lower()
    if preset not in ("strict", "standard", "disable"):
        return jsonify({"error": "Invalid preset. Options: 'strict', 'standard', 'disable'"}), 400

    success, msg = firewall_manager.apply_firewall_preset(preset)
    return jsonify({
        "success": success,
        "message": msg,
        "firewall": firewall_manager.get_firewall_status(),
    })


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_server(host: str = "0.0.0.0", port: int = 8080, debug: bool = False):
    """Start Flask in a background daemon thread."""
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=debug, use_reloader=False),
        daemon=True,
        name="web-server",
    )
    thread.start()
    return thread
