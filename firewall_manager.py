"""
firewall_manager.py
Manages Linux firewall (UFW) configuration for Raspberry Pi security hardening.

Presets:
1. 'strict' (Recommended for private networks):
   - Inbound: Default Deny.
   - Outbound: Default Allow.
   - Loopback: Allowed.
   - mDNS (5353/udp): Allowed for raspberrypi.local resolution.
   - SSH (22/tcp): Rate-limited and restricted to private subnets (192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12).
   - Web UI (8082/tcp): Restricted to private subnets (192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12).

2. 'standard':
   - Inbound: Default Deny.
   - Outbound: Default Allow.
   - Loopback: Allowed.
   - mDNS (5353/udp): Allowed.
   - SSH (22/tcp): Rate-limited.
   - Web UI (8082/tcp): Allowed.

3. 'disabled':
   - Disables UFW.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Dict, List, Tuple


PRIVATE_SUBNETS = ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "169.254.0.0/16"]


def is_ufw_installed() -> bool:
    """Check if ufw command is available on the system."""
    return shutil.which("ufw") is not None


def run_ufw_command(args: List[str]) -> Tuple[bool, str]:
    """Run a ufw command with sudo."""
    if not is_ufw_installed():
        return False, "UFW is not installed. Run 'sudo apt-get install -y ufw'."
    try:
        cmd = ["sudo", "ufw"] + args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return result.returncode == 0, output
    except Exception as e:
        return False, str(e)


def get_firewall_status() -> Dict:
    """
    Parse current UFW status.
    Returns:
        {
            "installed": bool,
            "active": bool,
            "rules": list of str,
            "raw": str,
        }
    """
    if not is_ufw_installed():
        return {
            "installed": False,
            "active": False,
            "mode": "not_installed",
            "rules": [],
            "raw": "UFW is not installed.",
        }

    success, output = run_ufw_command(["status", "verbose"])
    is_active = "Status: active" in output or "status: active" in output.lower()

    # Determine mode based on rules
    mode = "disabled"
    if is_active:
        if any(subnet in output for subnet in PRIVATE_SUBNETS):
            mode = "strict"
        else:
            mode = "standard"

    # Extract active rules summary lines
    rules = []
    lines = output.splitlines()
    rule_section = False
    for line in lines:
        if line.startswith("--"):
            rule_section = True
            continue
        if rule_section and line.strip():
            rules.append(line.strip())

    return {
        "installed": True,
        "active": is_active,
        "mode": mode,
        "rules": rules,
        "raw": output,
    }


def apply_firewall_preset(preset: str = "strict") -> Tuple[bool, str]:
    """
    Apply a firewall configuration preset.
    preset options: 'strict', 'standard', 'disable'
    """
    if not is_ufw_installed():
        return False, "UFW is not installed. Please install with: sudo apt-get install -y ufw"

    preset = preset.lower()

    if preset == "disable":
        return run_ufw_command(["disable"])

    # Reset rules to ensure clean slate
    run_ufw_command(["--force", "reset"])
    run_ufw_command(["default", "deny", "incoming"])
    run_ufw_command(["default", "allow", "outgoing"])

    # Allow loopback interface traffic
    run_ufw_command(["allow", "in", "on", "lo"])

    # Allow mDNS (port 5353 UDP) for hostname resolution (raspberrypi.local)
    run_ufw_command(["allow", "5353/udp", "comment", "mDNS hostname resolution"])

    if preset == "strict":
        # Strict mode: Restrict SSH and Web UI exclusively to private RFC 1918 subnets
        for subnet in PRIVATE_SUBNETS:
            run_ufw_command(["allow", "from", subnet, "to", "any", "port", "22", "proto", "tcp", "comment", f"SSH from {subnet}"])
            run_ufw_command(["allow", "from", subnet, "to", "any", "port", "8082", "proto", "tcp", "comment", f"Web UI from {subnet}"])
        # Add rate limiting for SSH
        run_ufw_command(["limit", "22/tcp", "comment", "Rate limit SSH"])
        mode_desc = "Strict LAN-only firewall enabled (SSH and Web UI limited to private subnets)"

    elif preset == "standard":
        # Standard mode: Rate-limited SSH and port 8082 allowed
        run_ufw_command(["limit", "22/tcp", "comment", "Rate limit SSH"])
        run_ufw_command(["allow", "8082/tcp", "comment", "Record Player Web UI"])
        mode_desc = "Standard firewall enabled (SSH rate-limited, Web UI allowed)"

    else:
        return False, f"Unknown firewall preset: {preset}"

    # Enable firewall
    success, enable_out = run_ufw_command(["--force", "enable"])
    if success:
        return True, mode_desc
    return False, f"Failed to enable UFW: {enable_out}"
