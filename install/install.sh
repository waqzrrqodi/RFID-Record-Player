#!/bin/bash

set -e

if ! command -v python3 > /dev/null 2>&1; then
    echo "Python3 is not installed. Please install it first."
    exit 1
fi

echo "Installing system dependencies"
sudo apt-get update -qq
sudo apt-get install -y swig liblgpio-dev bluez bluez-tools pulseaudio pulseaudio-module-bluetooth mpv ufw > /dev/null

mkdir -p "$HOME/Music"

echo "Creating python virtual environment"
python3 -m venv venv
source venv/bin/activate

echo "Installing python dependencies"
pip install -r install/requirements.txt -qq > /dev/null

echo "Enabling SPI Interface"
sudo sed -i 's/^dtparam=spi=.*/dtparam=spi=on/' /boot/config.txt
sudo sed -i 's/^#dtparam=spi=.*/dtparam=spi=on/' /boot/config.txt
sudo raspi-config nonint do_spi 0

echo "Enabling Bluetooth service"
sudo systemctl enable bluetooth
sudo systemctl start bluetooth

echo "Configuring Bluetooth for headless auto-pairing"
# Allow the pi user to manage Bluetooth without sudo
sudo usermod -aG bluetooth "$USER" 2>/dev/null || true

# Patch /etc/bluetooth/main.conf so the adapter is always discoverable/pairable
BT_CONF="/etc/bluetooth/main.conf"
if ! grep -q "^AlwaysPairable" "$BT_CONF" 2>/dev/null; then
    sudo sed -i '/^\[General\]/a AlwaysPairable = true\nDiscoverableTimeout = 0' "$BT_CONF"
fi

echo "Installing systemd service for record player"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cat > /tmp/record-player.service <<EOF
[Unit]
Description=RFID Record Player
After=network.target bluetooth.target sound.target
Wants=bluetooth.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/venv/bin/python3 $SCRIPT_DIR/record_player.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/record-player.service /etc/systemd/system/record-player.service
sudo systemctl daemon-reload
sudo systemctl enable record-player.service
echo "Systemd service installed. It will auto-start on next boot."
echo "Start now with:  sudo systemctl start record-player"
echo "View logs with:  journalctl -u record-player -f"
echo ""
echo "--- Security & Firewall Hardening ---"
read -p "Enable UFW firewall now? [strict (LAN-only) / standard / N]: " fwChoice
fwChoice="${fwChoice,,}"
if [[ "$fwChoice" == "strict" ]]; then
    bash "$SCRIPT_DIR/install/configure_firewall.sh" strict
elif [[ "$fwChoice" == "standard" ]]; then
    bash "$SCRIPT_DIR/install/configure_firewall.sh" standard
else
    echo "Firewall setup skipped. You can configure it anytime via 'bash install/configure_firewall.sh strict' or the setup TUI."
fi
echo ""
echo "Web interface will be available at:  http://raspberrypi.local:8080"

read -p "Would you like to restart your Raspberry Pi now? [Y/N] " userInput
userInput="${userInput^^}"

if [[ "${userInput,,}" == "y" ]]; then
    echo "You entered 'Y', rebooting now..."
    sleep 2
    sudo reboot now
elif [[ "${userInput,,}" == "n" ]]; then
    echo "Please restart your Raspberry Pi later to apply changes by running 'sudo reboot now'."
    exit
else
    echo "Unknown input, please restart your Raspberry Pi later to apply changes by running 'sudo reboot now'."
    sleep 1
fi