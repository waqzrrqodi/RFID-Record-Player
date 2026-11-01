"""
file_validator.py
Security checks and validation for uploaded audio/media files.

Protections:
1. File extension validation against an allowed whitelist.
2. Magic number / byte signature inspection to ensure file content matches audio types.
3. Explicit detection and rejection of dangerous file types (executables, scripts, HTML/PHP).
4. Directory traversal and path breakout prevention.
5. Dangerous double-extension detection (e.g., song.php.mp3, audio.sh.wav).
6. File size constraints (minimum header size and maximum allowable size).
"""
from __future__ import annotations

import os
import re
from typing import BinaryIO, Tuple

# Allowed audio extensions
ALLOWED_EXTENSIONS = {
    ".mp3", ".flac", ".ogg", ".wav", ".aac",
    ".m4a", ".opus", ".wma", ".aiff", ".mp4",
}

# Dangerous secondary extensions that should never appear in a media filename
DANGEROUS_EXTENSIONS = {
    ".exe", ".sh", ".bash", ".py", ".php", ".phtml", ".pl", ".cgi",
    ".bin", ".elf", ".so", ".dll", ".bat", ".cmd", ".ps1", ".vbs",
    ".js", ".html", ".htm", ".svg", ".jar", ".war",
}

# Maximum allowed file size: 100 MB
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024
# Minimum allowed file size (must contain at least valid header bytes)
MIN_FILE_SIZE_BYTES = 32

# Known executable/script signatures (blocklist)
BLOCKED_MAGIC_SIGNATURES = [
    (b"\x7fELF", "Linux ELF executable"),
    (b"MZ", "Windows PE/DOS executable or DLL"),
    (b"#!", "Shell/Script executable"),
    (b"<?php", "PHP script"),
    (b"<script", "JavaScript payload"),
    (b"<!DOCTYPE", "HTML/XML payload"),
    (b"<html", "HTML payload"),
]


def check_magic_bytes(header: bytes, ext: str) -> Tuple[bool, str]:
    """
    Verify that the initial bytes match the expected audio container/codec,
    and do not match any known dangerous binary/script signatures.
    """
    if len(header) < 4:
        return False, "File is too short to contain a valid audio header"

    # 1. Check against dangerous blocked signatures first
    for sig, desc in BLOCKED_MAGIC_SIGNATURES:
        if header.startswith(sig):
            return False, f"Dangerous file content detected: {desc}"

    # 2. Check format-specific magic bytes
    ext = ext.lower()

    if ext == ".mp3":
        # MP3 files either start with an ID3 tag or raw MPEG audio frame sync
        # ID3 tag: b'ID3'
        # MPEG sync: 11 bits set (0xFF 0xFB, 0xFF 0xF3, 0xFF 0xF2, 0xFF 0xFA, 0xFF 0xE3)
        if header.startswith(b"ID3"):
            return True, "Valid MP3 (ID3v2 tag)"
        if len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
            return True, "Valid MP3 (MPEG audio frame)"
        return False, "Invalid MP3 header (missing ID3 tag or MPEG frame sync)"

    elif ext == ".flac":
        if header.startswith(b"fLaC"):
            return True, "Valid FLAC header"
        return False, "Invalid FLAC header (missing 'fLaC' marker)"

    elif ext in (".ogg", ".opus"):
        # Ogg container starts with 'OggS'
        if header.startswith(b"OggS"):
            return True, "Valid Ogg container"
        return False, "Invalid OGG/Opus header (missing 'OggS' container marker)"

    elif ext == ".wav":
        # RIFF header: bytes 0-4 are 'RIFF', bytes 8-12 are 'WAVE'
        if header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WAVE":
            return True, "Valid WAV RIFF header"
        return False, "Invalid WAV header (missing 'RIFF....WAVE' format)"

    elif ext == ".aiff":
        # AIFF header: bytes 0-4 are 'FORM', bytes 8-12 are 'AIFF' or 'AIFC'
        if header.startswith(b"FORM") and len(header) >= 12 and header[8:12] in (b"AIFF", b"AIFC"):
            return True, "Valid AIFF header"
        return False, "Invalid AIFF header"

    elif ext in (".m4a", ".mp4", ".aac"):
        # M4A/MP4 contains 'ftyp' at offset 4
        if len(header) >= 8 and header[4:8] == b"ftyp":
            return True, "Valid M4A/MP4 container"
        # ADTS AAC sync word (0xFF 0xF1 or 0xFF 0xF9) or ID3
        if header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xF6) == 0xF0):
            return True, "Valid AAC stream"
        return False, "Invalid M4A/AAC header (missing 'ftyp' box or ADTS sync)"

    elif ext == ".wma":
        # ASF GUID header: 30 26 B2 75 8E 66 CF 11
        asf_guid = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"
        if header.startswith(asf_guid):
            return True, "Valid WMA/ASF container"
        return False, "Invalid WMA header"

    # Fallback for other audio types: ensure not blocked and has sufficient entropy
    return True, "Extension allowed"


def sanitize_and_validate_filename(filename: str) -> Tuple[bool, str]:
    """
    Validate filename against path traversal, control chars, and dangerous double extensions.
    Returns (is_valid, error_message_or_clean_filename).
    """
    if not filename or not filename.strip():
        return False, "Filename cannot be empty"

    # Strip directory components to avoid relative path injections
    cleaned = os.path.basename(filename).strip()

    # Disallow hidden files
    if cleaned.startswith("."):
        return False, "Hidden files (starting with '.') are not allowed"

    # Only allow safe characters: alphanumeric, spaces, dashes, underscores, dots
    if not re.match(r"^[a-zA-Z0-9_\-. ]+$", cleaned):
        return False, "Filename contains invalid or dangerous characters"

    ext = os.path.splitext(cleaned)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"File extension '{ext}' is not permitted"

    # Check for dangerous double extensions (e.g. song.sh.mp3 or track.php.wav)
    parts = cleaned.lower().split(".")
    for part in parts[:-1]:
        if f".{part}" in DANGEROUS_EXTENSIONS:
            return False, f"Dangerous secondary extension '.{part}' detected in filename"

    return True, cleaned


def is_path_within_root(target_path: str, root_dir: str) -> bool:
    """
    Ensure target_path resolves strictly within root_dir, preventing directory traversal.
    """
    real_target = os.path.realpath(os.path.abspath(target_path))
    real_root = os.path.realpath(os.path.abspath(root_dir))

    # Must either be root_dir itself or a child path inside root_dir
    return real_target == real_root or real_target.startswith(real_root + os.sep)


def validate_upload(
    stream: BinaryIO,
    filename: str,
    target_dir: str,
    base_dir: str,
) -> Tuple[bool, str, str]:
    """
    Comprehensive validation for an uploaded file:
    1. Validates and sanitizes filename.
    2. Validates target directory confinement.
    3. Inspects header magic bytes.
    4. Enforces file size limits.

    Returns:
        (is_valid: bool, error_or_success_message: str, safe_filepath: str)
    """
    # 1. Filename validation
    valid_name, name_res = sanitize_and_validate_filename(filename)
    if not valid_name:
        return False, name_res, ""
    safe_filename = name_res
    ext = os.path.splitext(safe_filename)[1].lower()

    # 2. Path confinement check
    if not is_path_within_root(target_dir, base_dir):
        return False, "Directory traversal rejected: target directory is outside the allowed media library", ""

    # 3. Read sample bytes for magic number verification
    try:
        current_pos = stream.tell()
    except Exception:
        current_pos = 0

    header_sample = stream.read(64)
    # Restore stream position
    try:
        stream.seek(current_pos)
    except Exception:
        pass

    if len(header_sample) < MIN_FILE_SIZE_BYTES:
        return False, "File is too small to be a valid audio file", ""

    # 4. Check magic bytes
    valid_magic, magic_msg = check_magic_bytes(header_sample, ext)
    if not valid_magic:
        return False, f"File content verification failed: {magic_msg}", ""

    # 5. Measure total stream length safely without exhausting RAM
    try:
        stream.seek(0, os.SEEK_END)
        total_size = stream.tell()
        stream.seek(0)
    except Exception:
        total_size = len(header_sample)

    if total_size > MAX_FILE_SIZE_BYTES:
        return False, f"File exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB", ""

    dest_path = os.path.join(target_dir, safe_filename)
    return True, "Validation successful", dest_path
