"""
bluetooth_manager.py
Lightweight Bluetooth speaker management using bluetoothctl subprocess.
No PyBlueZ required — works with the built-in BlueZ stack on Raspberry Pi OS Lite.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time


class BluetoothManager:
    """
    Manages Bluetooth speaker pairing and connection using bluetoothctl.
    All commands run via subprocess so there's no dependency on PyBlueZ.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._scan_results: dict[str, str] = {}  # {mac: name}
        self._scanning = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, *args: str, timeout: int = 10) -> str:
        """Run a bluetoothctl command and return stdout."""
        try:
            result = subprocess.run(
                ["bluetoothctl"] + list(args),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            return ""
        except FileNotFoundError:
            return ""

    def _send_interactive(self, commands: list[str], read_delay: float = 1.5) -> str:
        """
        Send multiple commands to an interactive bluetoothctl session.
        Needed for multi-step flows (scan on → wait → scan off → devices).
        """
        try:
            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for cmd in commands:
                proc.stdin.write(cmd + "\n")
                proc.stdin.flush()
                time.sleep(read_delay if cmd.startswith("scan") else 0.3)
            proc.stdin.write("quit\n")
            proc.stdin.flush()
            stdout, _ = proc.communicate(timeout=30)
            return stdout
        except Exception:
            return ""

    @staticmethod
    def _parse_devices(output: str) -> dict[str, str]:
        """Extract {MAC: name} pairs from bluetoothctl 'devices' output."""
        devices: dict[str, str] = {}
        for line in output.splitlines():
            # Lines look like: "Device AA:BB:CC:DD:EE:FF My Speaker"
            match = re.match(r"Device\s+([0-9A-F:]{17})\s+(.+)", line.strip(), re.IGNORECASE)
            if match:
                mac, name = match.group(1).upper(), match.group(2).strip()
                devices[mac] = name
        return devices

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def power_on(self) -> bool:
        """Ensure the Bluetooth adapter is powered on."""
        output = self._run("power", "on")
        return "succeeded" in output.lower() or "yes" in output.lower() or "power" in output.lower()

    def scan(self, duration: int = 8) -> dict[str, str]:
        """
        Scan for nearby Bluetooth devices for `duration` seconds.
        Returns {mac_address: device_name}.
        """
        with self._lock:
            self._scanning = True
            self._scan_results = {}

        output = self._send_interactive(
            ["power on", "agent NoInputNoOutput", "default-agent", "scan on"],
            read_delay=duration,
        )
        self._run("scan", "off")

        # Also pull already-known devices
        devices_out = self._run("devices")
        paired_out = self._run("paired-devices")

        found = {}
        for blob in (output, devices_out, paired_out):
            found.update(self._parse_devices(blob))

        with self._lock:
            self._scan_results = found
            self._scanning = False

        return found

    def pair(self, mac: str) -> bool:
        """Pair with a device by MAC address."""
        output = self._run("pair", mac, timeout=20)
        return "successful" in output.lower() or "already paired" in output.lower()

    def trust(self, mac: str) -> bool:
        """Trust a device so it auto-reconnects."""
        output = self._run("trust", mac)
        return "successful" in output.lower() or "already trusted" in output.lower() or "trust" in output.lower()

    def connect(self, mac: str) -> bool:
        """Connect to a paired/trusted Bluetooth device."""
        output = self._run("connect", mac, timeout=15)
        return "successful" in output.lower() or "already connected" in output.lower()

    def disconnect(self, mac: str) -> bool:
        """Disconnect a Bluetooth device."""
        output = self._run("disconnect", mac)
        return "successful" in output.lower()

    def remove(self, mac: str) -> bool:
        """Remove/unpair a Bluetooth device."""
        output = self._run("remove", mac)
        return "device has been removed" in output.lower() or "successful" in output.lower()

    def get_connected_device(self) -> dict | None:
        """
        Return info about the currently connected Bluetooth audio device, or None.
        Checks paired devices and queries each for Connected status.
        """
        paired_out = self._run("paired-devices")
        devices = self._parse_devices(paired_out)

        for mac, name in devices.items():
            info = self._run("info", mac)
            connected = re.search(r"Connected:\s*yes", info, re.IGNORECASE)
            trusted = re.search(r"Trusted:\s*yes", info, re.IGNORECASE)
            if connected:
                return {
                    "mac": mac,
                    "name": name,
                    "trusted": bool(trusted),
                    "connected": True,
                }
        return None

    def get_paired_devices(self) -> list[dict]:
        """Return list of all paired devices with their connection status."""
        paired_out = self._run("paired-devices")
        devices = self._parse_devices(paired_out)
        result = []
        for mac, name in devices.items():
            info = self._run("info", mac)
            connected = bool(re.search(r"Connected:\s*yes", info, re.IGNORECASE))
            trusted = bool(re.search(r"Trusted:\s*yes", info, re.IGNORECASE))
            result.append({"mac": mac, "name": name, "connected": connected, "trusted": trusted})
        return result

    def pair_and_connect(self, mac: str) -> dict:
        """
        Full flow: pair → trust → connect.
        Returns status dict with per-step success flags.
        """
        self.power_on()
        paired = self.pair(mac)
        trusted = self.trust(mac)
        connected = self.connect(mac)
        return {
            "mac": mac,
            "paired": paired,
            "trusted": trusted,
            "connected": connected,
            "success": connected,
        }

    def set_as_default_sink(self, mac: str) -> bool:
        """
        Set the connected Bluetooth device as the default PulseAudio/PipeWire sink.
        The sink name is derived from the MAC address (colons replaced by underscores).
        """
        sink_mac = mac.replace(":", "_").lower()
        # Try common sink name patterns
        patterns = [
            f"bluez_sink.{sink_mac}.a2dp_sink",
            f"bluez_output.{sink_mac}.1",
        ]
        for sink in patterns:
            try:
                result = subprocess.run(
                    ["pactl", "set-default-sink", sink],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return True
            except Exception:
                pass
        return False
