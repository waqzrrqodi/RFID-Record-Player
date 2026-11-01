#!/bin/bash
# install/configure_firewall.sh
# Hardens Raspberry Pi security with UFW firewall rules.
#
# Usage:
#   sudo bash install/configure_firewall.sh [strict|standard|disable|status]

set -e

MODE="${1:-strict}"

if ! command -v ufw >/dev/null 2>&1; then
    echo "Installing UFW (Uncomplicated Firewall)..."
    sudo apt-get update -qq
    sudo apt-get install -y ufw >/dev/null
fi

case "$MODE" in
    strict)
        echo "=== Configuring STRICT Firewall (Private LAN Only) ==="
        echo "Incoming: DENY | Outgoing: ALLOW"
        sudo ufw --force reset >/dev/null
        sudo ufw default deny incoming
        sudo ufw default allow outgoing
        sudo ufw allow in on lo

        # mDNS for raspberrypi.local
        sudo ufw allow 5353/udp comment 'mDNS hostname resolution'

        # Rate-limited SSH & Web UI allowed exclusively from RFC 1918 private subnets
        SUBNETS=("192.168.0.0/16" "10.0.0.0/8" "172.16.0.0/12")
        for subnet in "${SUBNETS[@]}"; do
            echo "Allowing access from $subnet..."
            sudo ufw allow from "$subnet" to any port 22 proto tcp comment "SSH from $subnet"
            sudo ufw allow from "$subnet" to any port 8080 proto tcp comment "Web UI from $subnet"
        done

        # Rate limit SSH
        sudo ufw limit 22/tcp comment 'Rate limit SSH'

        sudo ufw --force enable
        echo "✓ Strict firewall active! Only devices on your local network can reach SSH and port 8080."
        ;;

    standard)
        echo "=== Configuring STANDARD Firewall ==="
        sudo ufw --force reset >/dev/null
        sudo ufw default deny incoming
        sudo ufw default allow outgoing
        sudo ufw allow in on lo

        # mDNS for raspberrypi.local
        sudo ufw allow 5353/udp comment 'mDNS hostname resolution'

        # Rate limit SSH
        sudo ufw limit 22/tcp comment 'Rate limit SSH'

        # Allow Web UI
        sudo ufw allow 8080/tcp comment 'Record Player Web UI'

        sudo ufw --force enable
        echo "✓ Standard firewall active! SSH is rate-limited and port 8080 is open."
        ;;

    disable)
        echo "=== Disabling Firewall ==="
        sudo ufw disable
        echo "✓ Firewall disabled."
        ;;

    status)
        sudo ufw status verbose
        ;;

    *)
        echo "Usage: $0 [strict|standard|disable|status]"
        exit 1
        ;;
esac
