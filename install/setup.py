"""
install/setup.py
Interactive TUI for initial setup:
  1. Configure Spotify credentials
  2. Map RFID tags → Spotify URIs
  3. Map RFID tags → local media files
  4. Read / inspect RFID tags
"""
from __future__ import annotations

import sys
import os
import glob
import json

from dotenv import set_key
from mfrc522 import SimpleMFRC522

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import MemoryCacheHandler

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import firewall_manager

ENV_FILE = ".env"
RFID_FILE = "rfid.json"
RFID_LABELS_FILE = "rfid_labels.json"

# Default directory to look for local audio files
DEFAULT_MEDIA_DIR = os.path.expanduser("~/Music")

SUPPORTED_EXTENSIONS = (
    ".mp3", ".flac", ".ogg", ".wav", ".aac",
    ".m4a", ".opus", ".wma", ".aiff",
)

# ---------------------------------------------------------------------------
# RFID file helpers
# ---------------------------------------------------------------------------

def read_rfid_file() -> dict:
    try:
        with open(RFID_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Note: rfid.json not found, starting fresh.")
        return {}
    except json.JSONDecodeError as e:
        print(f"Error decoding rfid.json: {e}")
        return {}


def write_rfid_file(rfid_map: dict):
    with open(RFID_FILE, "w") as f:
        json.dump(rfid_map, f, indent=4)


def read_labels_file() -> dict:
    try:
        with open(RFID_LABELS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def write_labels_file(labels: dict):
    with open(RFID_LABELS_FILE, "w") as f:
        json.dump(labels, f, indent=4)


def describe_entry(raw) -> str:
    """Return a human-readable description of an rfid.json value."""
    if isinstance(raw, str):
        return f"[Spotify] {raw}"
    if isinstance(raw, dict):
        t = raw.get("type", "?")
        if t == "spotify":
            return f"[Spotify] {raw.get('uri', '?')}"
        if t == "local":
            return f"[Local]   {raw.get('path', '?')}"
    return str(raw)


# ---------------------------------------------------------------------------
# 1. Spotify credentials
# ---------------------------------------------------------------------------

def set_up_spotify_credentials():
    print("\n--- Spotify Credentials Setup ---")
    client_id = input("Enter Spotify Client ID: ").strip()
    client_secret = input("Enter Spotify Client Secret: ").strip()
    redirect_uri = input("Enter Redirect URI (e.g. http://127.0.0.1:8080): ").strip()

    cache_handler = MemoryCacheHandler()
    auth = SpotifyOAuth(
        scope="user-read-playback-state,user-modify-playback-state,user-read-recently-played",
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        open_browser=False,
        cache_handler=cache_handler,
    )
    auth.get_access_token(as_dict=False)
    token_info = cache_handler.get_cached_token()
    if not token_info or not token_info.get("refresh_token"):
        sys.exit("Error: Unable to get Spotify refresh token.")
    refresh_token = token_info["refresh_token"]

    set_key(ENV_FILE, "SPOTIFY_CLIENT_ID", client_id)
    set_key(ENV_FILE, "SPOTIFY_CLIENT_SECRET", client_secret)
    set_key(ENV_FILE, "SPOTIFY_REFRESH_TOKEN", refresh_token)
    set_key(ENV_FILE, "SPOTIFY_REDIRECT_URI", redirect_uri)
    print("✓ Spotify credentials saved.")


# ---------------------------------------------------------------------------
# 2. Map RFID → Spotify URI
# ---------------------------------------------------------------------------

def write_rfid_spotify():
    print("\n--- Map RFID Tags to Spotify ---")
    rfid = SimpleMFRC522()
    rfid_map = read_rfid_file()
    labels = read_labels_file()

    while True:
        print("\nPlace an RFID tag on the reader and press Enter…")
        input()
        print("Scanning…")
        rfid_id = str(rfid.read_id())
        print(f"  Tag ID: {rfid_id}")

        existing = rfid_map.get(rfid_id)
        if existing:
            print(f"  Currently mapped to: {describe_entry(existing)}")

        uri = input("  Spotify URI (spotify:track/album/playlist/artist:...): ").strip()
        parts = uri.split(":")
        if not uri.startswith("spotify:") or len(parts) < 3 or parts[1] not in ("track", "album", "playlist", "artist"):
            print("  ✗ Invalid URI format. Must be spotify:{track|album|playlist|artist}:...")
        else:
            lbl = input("  Label (optional, e.g. 'Abbey Road'): ").strip()
            rfid_map[rfid_id] = {"type": "spotify", "uri": uri}
            if lbl:
                labels[rfid_id] = lbl
            write_rfid_file(rfid_map)
            write_labels_file(labels)
            print(f"  ✓ Saved: {rfid_id} → {uri}")

        choice = input("\n  [1] Add another  [2] Back to menu: ").strip()
        if choice == "2":
            break


# ---------------------------------------------------------------------------
# 3. Map RFID → local media file
# ---------------------------------------------------------------------------

def _discover_media_files(directory: str) -> list[str]:
    """Return sorted list of supported audio file paths under directory."""
    found = []
    for root, _dirs, files in os.walk(directory):
        for fname in files:
            if os.path.splitext(fname)[1].lower() in SUPPORTED_EXTENSIONS:
                found.append(os.path.join(root, fname))
    return sorted(found)


def _pick_file_from_list(files: list[str]) -> str | None:
    """Interactive numbered picker for a list of files. Returns chosen path or None."""
    if not files:
        print("  No media files found.")
        return None

    # Paginate at 20 entries
    page_size = 20
    offset = 0

    while True:
        page = files[offset:offset + page_size]
        print(f"\n  Files ({offset + 1}–{offset + len(page)} of {len(files)}):")
        for i, path in enumerate(page, start=1):
            size_mb = os.path.getsize(path) / (1024 * 1024)
            fname = os.path.basename(path)
            rel = os.path.relpath(path, DEFAULT_MEDIA_DIR)
            print(f"    {offset + i:>3}. {rel:<50} {size_mb:.1f} MB")

        print()
        options = ["Enter file number to select"]
        if offset + page_size < len(files):
            options.append("[n] next page")
        if offset > 0:
            options.append("[p] prev page")
        options.append("[m] enter path manually")
        options.append("[0] cancel")
        print("  " + " | ".join(options))

        choice = input("  > ").strip().lower()

        if choice == "0":
            return None
        if choice == "n" and offset + page_size < len(files):
            offset += page_size
            continue
        if choice == "p" and offset > 0:
            offset -= page_size
            continue
        if choice == "m":
            path = os.path.expanduser(input("  Enter file path: ").strip())
            if os.path.isfile(path):
                return path
            print("  File not found.")
            continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(files):
                return files[idx]
        except ValueError:
            pass
        print("  Invalid choice.")


def write_rfid_local():
    print("\n--- Map RFID Tags to Local Media Files ---")

    media_dir = input(f"  Media directory [{DEFAULT_MEDIA_DIR}]: ").strip() or DEFAULT_MEDIA_DIR
    media_dir = os.path.expanduser(media_dir)

    if not os.path.isdir(media_dir):
        print(f"  Directory not found: {media_dir}")
        create = input("  Create it? [y/N]: ").strip().lower()
        if create == "y":
            os.makedirs(media_dir, exist_ok=True)
            print(f"  ✓ Created {media_dir}")
        else:
            return

    print(f"  Scanning {media_dir} for media files…")
    files = _discover_media_files(media_dir)
    print(f"  Found {len(files)} file(s).")

    rfid = SimpleMFRC522()
    rfid_map = read_rfid_file()
    labels = read_labels_file()

    while True:
        print("\nPlace an RFID tag on the reader and press Enter…")
        input()
        print("Scanning…")
        rfid_id = str(rfid.read_id())
        print(f"  Tag ID: {rfid_id}")

        existing = rfid_map.get(rfid_id)
        if existing:
            print(f"  Currently mapped to: {describe_entry(existing)}")

        print("\n  Select a file to map to this tag:")
        chosen = _pick_file_from_list(files)

        if chosen:
            lbl = input(f"  Label (optional, default=filename): ").strip()
            if not lbl:
                lbl = os.path.basename(chosen)
            rfid_map[rfid_id] = {"type": "local", "path": chosen}
            labels[rfid_id] = lbl
            write_rfid_file(rfid_map)
            write_labels_file(labels)
            print(f"  ✓ Saved: {rfid_id} → {chosen}")

        choice = input("\n  [1] Add another  [2] Back to menu: ").strip()
        if choice == "2":
            break


# ---------------------------------------------------------------------------
# 4. Read / inspect RFID tags
# ---------------------------------------------------------------------------

def read_rfid_tags():
    print("\n--- Read RFID Tags ---")
    rfid = SimpleMFRC522()
    rfid_map = read_rfid_file()
    labels = read_labels_file()

    while True:
        print("\nPlace an RFID tag on the reader and press Enter…")
        input()
        print("Scanning…")
        rfid_id = str(rfid.read_id())

        label = labels.get(rfid_id, "")
        raw = rfid_map.get(rfid_id)

        print(f"  Tag ID : {rfid_id}")
        if label:
            print(f"  Label  : {label}")
        if raw:
            print(f"  Mapped : {describe_entry(raw)}")
        else:
            print("  Status : Not configured")

        choice = input("\n  [1] Scan another  [2] Back to menu: ").strip()
        if choice == "2":
            break


# ---------------------------------------------------------------------------
# 5. List all mappings
# ---------------------------------------------------------------------------

def list_all_mappings():
    print("\n--- All RFID Mappings ---")
    rfid_map = read_rfid_file()
    labels = read_labels_file()

    if not rfid_map:
        print("  No mappings found.")
        return

    col_w = 20
    print(f"\n  {'ID':<20} {'Label':<25} {'Type':<8} Target")
    print("  " + "-" * 80)
    for rfid_id, raw in rfid_map.items():
        label = labels.get(rfid_id, "")
        if isinstance(raw, str):
            t, target = "spotify", raw
        elif isinstance(raw, dict):
            t = raw.get("type", "?")
            target = raw.get("uri") or raw.get("path") or "?"
        else:
            t, target = "?", str(raw)
        print(f"  {rfid_id:<20} {label:<25} {t:<8} {target}")

    input("\n  Press Enter to return to menu…")


# ---------------------------------------------------------------------------
# 6. Firewall & Network Security
# ---------------------------------------------------------------------------

def configure_firewall_menu():
    print("\n--- Firewall & Network Security Hardening ---")
    st = firewall_manager.get_firewall_status()
    print(f"  UFW Installed : {'Yes' if st['installed'] else 'No'}")
    print(f"  Status        : {'Active (' + st['mode'] + ' mode)' if st['active'] else 'Disabled/Inactive'}")

    if not st["installed"]:
        print("\n  UFW is not installed. Run 'sudo apt-get install -y ufw' first.")
        input("  Press Enter to return…")
        return

    print("\n  Presets:")
    print("  1. Enable Strict Mode   (Allow SSH & Web UI ONLY from private subnets: 192.168.x.x, 10.x.x.x)")
    print("  2. Enable Standard Mode (Rate-limit SSH, allow Web UI from anywhere on LAN)")
    print("  3. View Current Firewall Rules")
    print("  4. Disable Firewall")
    print("  5. Back to Main Menu")

    c = input("\n  Choose an option [1-5]: ").strip()
    if c == "1":
        print("  Applying Strict Mode rules…")
        ok, msg = firewall_manager.apply_firewall_preset("strict")
        print(f"  {'✓' if ok else '✗'} {msg}")
    elif c == "2":
        print("  Applying Standard Mode rules…")
        ok, msg = firewall_manager.apply_firewall_preset("standard")
        print(f"  {'✓' if ok else '✗'} {msg}")
    elif c == "3":
        print("\n  --- Active Rules ---")
        for r in st.get("rules", []):
            print(f"    {r}")
        if not st.get("rules"):
            print("    No rules configured or firewall is inactive.")
    elif c == "4":
        ok, msg = firewall_manager.apply_firewall_preset("disable")
        print(f"  {'✓' if ok else '✗'} {msg}")
    elif c == "5":
        return
    else:
        print("  Invalid choice.")

    input("\n  Press Enter to continue…")


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def get_user_choice():
    actions = {
        "1": ("Setup Spotify credentials",       set_up_spotify_credentials),
        "2": ("Map RFID tag → Spotify URI",       write_rfid_spotify),
        "3": ("Map RFID tag → Local media file",  write_rfid_local),
        "4": ("Read / inspect RFID tags",          read_rfid_tags),
        "5": ("List all mappings",                 list_all_mappings),
        "6": ("Firewall & Network Security",       configure_firewall_menu),
    }

    exit_key = str(len(actions) + 1)

    while True:
        print("\n┌─────────────────────────────────────────┐")
        print("│        RFID Record Player Setup         │")
        print("└─────────────────────────────────────────┘")
        for key, (label, _) in actions.items():
            print(f"  {key}. {label}")
        print(f"  {exit_key}. Exit")
        print()

        choice = input("Enter your choice: ").strip()

        if choice == exit_key:
            print("Goodbye.")
            break

        action = actions.get(choice)
        if not action:
            print("Invalid choice. Please try again.")
            continue

        label, func = action
        print(f"\n→ {label}")
        try:
            func()
        except KeyboardInterrupt:
            print("\n  Interrupted.")


if __name__ == "__main__":
    get_user_choice()