# RFID Record Player 

<img src="./docs/images/record_player.png" />

A modern record player that repurposes a record player coaster as an interface for controlling Spotify playback via RFID and a Raspberry Pi, combining the physical experience of vinyl with digital playback.
Features RFID detection of tagged records for Spotify content, a stepper motor–driven spinning platter, tone arm control to start/stop playback, **Bluetooth speaker support**, and a **lightweight web management interface** accessible from any device on the same network.

## Features

- 💿 **RFID-triggered Spotify & Local Media playback** — scan a tagged record to play Spotify URIs or local audio files (.mp3, .flac, .wav, .ogg, .m4a, etc.)
- 📁 **Local Audio File Support** — play local files headlessly using `mpv` without requiring a Spotify subscription or internet connection
- 🔊 **Bluetooth speaker** — pairs and connects to any A2DP Bluetooth speaker; auto-reconnects on boot
- 🌐 **Web management UI** — accessible at `http://raspberrypi.local:8080` without a display, with drag-and-drop file upload, file browser, and tag management
- 🖥 **Interactive TUI setup** — command-line interface for Spotify credentials, tag scanning, local file mapping, tag inspection, and firewall hardening
- 🛡️ **Upload Security Checks** — magic byte audio header validation, script/executable blocklist, double-extension detection, and directory traversal confinement
- 🧱 **Firewall Hardening (UFW)** — presets for Strict LAN-only access (private subnets only) or Standard mode with rate-limited SSH
- 🔄 **Auto-start on boot** — systemd service starts everything headlessly

## Components

- [Raspberry Pi Zero 2W (with headers)](https://amzn.to/4sl4cfk)
- [MicroSD Card](https://amzn.to/3G3Tq9W)
- [Vinyl Record Coasters](https://amzn.to/4pgyVr3)
- [28BYJ-48 ULN2003 5V Stepper Motor & ULN2003 Driver Board](https://amzn.to/4jcpeZa)
- [A3144E Hall Effect Magnetic Sensor Module](https://amzn.to/3MWDGbK)
- [RC522 RFID Reader Module (with headers)](https://amzn.to/4pcugX6)
- [NTAG213 NFC Tags](https://amzn.to/3YfJfVb)
- [5x2mm Magnet](https://amzn.to/4qw3VEw)
- [Female to Female Dupont Jumper Wires (16 ct)](https://amzn.to/4pjjwq0)

**Disclosure:** The links above are affiliate links. I may earn a commission from qualifying purchases made through them, at no extra cost to you, which helps maintain and develop this project.

## 3D Print Files

The .3MF files for printing the enclosure and other parts of the Record Player are available here: [RFID Record Player 3D Files](https://www.patreon.com/posts/148251861)

## Tutorial

See the [YouTube tutorial](https://youtu.be/fBjv4E7mpA4) for a full walkthrough of the components, wiring, assembly, and software setup.

## Wiring Diagram

<img src="./docs/images/wiring_diagram.png" />


### ULN2003 Stepper Motor Driver

| Pi Pin           | ULN2003 Pin |
|------------------|-------------|
| Pin 8 (GPIO 14)   | IN1         |
| Pin 10 (GPIO 15)  | IN2         |
| Pin 12 (GPIO 18) | IN3         |
| Pin 16 (GPIO 23) | IN4         |
| Pin 2 (5V)       | VCC         |
| Pin 6 (GND)      | GND         |

Note: It's usually not recommended to power the stepper motor directly from the Pi’s 5 V pin — use an external battery source or a good power supply with atleast 2A.
The stepper motor may become hot after long periods of use.

### RC522 RFID Reader (SPI)

| Pi Pin               | RC522 Pin |
|----------------------|-----------|
| Pin 24 (GPIO 8)      | SDA       |
| Pin 23 (GPIO 11)     | SCK       |
| Pin 19 (GPIO 10)     | MOSI      |
| Pin 21 (GPIO 9)      | MISO      |
| Pin 22 (GPIO 25)     | RST       |
| Pin 17 (3.3V)        | 3.3V      |
| Pin 25 (GND)         | GND       |

### A3144 Hall Effect Sensor

| Pi Pin           | A3144 Pin |
|------------------|-----------|
| Pin 11 (GPIO 17) | OUT       |
| Pin 1 (3.3V)     | VCC       |
| Pin 9 (GND)      | GND       |

## Installation

1. Clone the repository
    ```bash
    git clone https://github.com/fatihak/RFID-Record-Player.git
    cd RFID-Record-Player
    ```
2. Run the installation script
    This sets up the Python virtual environment, installs python dependencies, and enables required interfaces.
    ```bash
    bash install/install.sh
    ``` 
    Once the installation finishes, you’ll be prompted to reboot your Raspberry Pi for the changes to take effect.
3. Run the setup script
    After rebooting, activate the virtual environment and run the setup script:
    ```bash
    source venv/bin/activate
    python3 install/setup.py
    ```
4. Set up Spotify Credentials
    - Visit [Spotify For Developers](https://developer.spotify.com/dashboard/applications) and Sign In
    - In the Developer Dashboard, click "Create an App"
        - Give your app a Name and Description
        - For the Redirect URIs, enter `http://127.0.0.1:8080`
        - Check `Web API` box
    - In the setup script, enter the `Client ID`, `Client Secret`, and `Redirect URI`
    - Visit the URL to authorize your app and paste the redirected URL back.
5. Write RFID tags
    - In the setup script, choose `Write RFID tags`
    - Scan an RFID tag
    - Paste a Spotify URI for a track, album, playlist, or artist 
        - URIs follow the format `spotify:{track|album|artist|playlist}:...`
        - To copy a Spotify URI, use the desktop app, click the three dots on a song/album/artist, hover over "Share," then hold Alt (Windows) or Option (Mac) and select "Copy Spotify URI"
    - You can also add/remove tags later via the **web interface** (see below)
6. Pair a Bluetooth speaker
    - The Pi's Bluetooth adapter is powered on automatically at startup
    - Open the web interface at `http://raspberrypi.local:8080`
    - Go to the **Bluetooth** tab, click **Scan**, and select your speaker
    - Once paired, the speaker will auto-reconnect on every boot
7. Start the Record Player
    The systemd service installed by `install.sh` will auto-start on every boot. To start it manually:
    ```bash
    sudo systemctl start record-player
    ```
    To follow the logs:
    ```bash
    journalctl -u record-player -f
    ```

After the installation, your Spotify credentials will be stored in a `.env` file and the RFID ID mapping in `rfid.json` in the root of the repository.

## Setup & Tag Configuration (TUI)

You can configure credentials and tags at any time using the interactive terminal setup:
```bash
source venv/bin/activate
python3 install/setup.py
```

Options:
1. **Setup Spotify credentials** — client ID, secret, and OAuth refresh token
2. **Map RFID tag → Spotify URI** — scan physical tag, assign track/album/artist/playlist URI with optional label
3. **Map RFID tag → Local media file** — scan physical tag, browse/paginate local audio files from `~/Music` or enter a file path, with custom label
4. **Read / inspect RFID tags** — scan tag to verify its mapped target and label
5. **List all mappings** — tabular summary of all configured tags
6. **Firewall & Network Security** — enable Strict LAN-only mode, Standard mode, check rules, or disable UFW

## Web Interface

Once the record player is running, open **`http://raspberrypi.local:8080`** in any browser on the same network.

| Tab | What you can do |
|-----|----------------|
| 🔵 **Bluetooth** | Scan for Bluetooth speakers, pair, connect, disconnect, and remove devices |
| 📻 **RFID Tags** | Add, label, and delete RFID mappings for both Spotify URIs and local files; includes file picker browser |
| 🎵 **Local Media** | Browse files in `~/Music`, drag-and-drop file upload with security checks, preview playback, and 1-click assign to an RFID tag |
| 🛡️ **Security** | Check upload validation policies, view UFW status, and toggle firewall presets (Strict LAN-only / Standard) |
| 📋 **Log** | Live activity log from the record player hardware and playback events |

## Security & Firewall Configuration

### Upload File Security
Every uploaded media file undergoes strict validation before being stored on disk:
- **Magic Byte Inspection**: Audio headers are inspected to verify legitimate MP3 (ID3/MPEG sync), FLAC (`fLaC`), WAV (`RIFF....WAVE`), OGG/Opus (`OggS`), and M4A/AAC (`ftyp`/ADTS) files.
- **Dangerous Signature Blocklist**: Instantly rejects Linux ELF binaries, Windows PE/MZ executables, shell scripts (`#!`), PHP, and HTML/script payloads even if renamed with an audio extension.
- **Directory Traversal Protection**: Upload targets and file browsing requests are confined strictly within the configured music directory (`~/Music`).
- **Filename Sanitization**: Rejects dangerous secondary extensions (e.g., `song.php.mp3`, `exploit.sh.wav`) and hidden files.
- **HTTP Security Headers**: Enforces `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, and `Referrer-Policy`.

### Firewall Hardening (UFW)
You can configure the firewall directly from the terminal or web interface:

```bash
# Strict mode: Restricts SSH (22) and Web UI (8080) exclusively to private LAN subnets (192.168.x.x, 10.x.x.x)
sudo bash install/configure_firewall.sh strict

# Standard mode: Allows Web UI on LAN with rate-limited SSH
sudo bash install/configure_firewall.sh standard

# Check status and active rules
sudo bash install/configure_firewall.sh status

# Disable firewall
sudo bash install/configure_firewall.sh disable
```

## Sponsoring

This project is maintained and developed with the help of sponsors. If you enjoy the project or find it useful, consider supporting its continued development.

<p align="center">
<a href="https://github.com/sponsors/fatihak" target="_blank"><img src="https://user-images.githubusercontent.com/345274/133218454-014a4101-b36a-48c6-a1f6-342881974938.png" alt="Become a Patreon" height="35" width="auto"></a>
<a href="https://www.patreon.com/akzdev" target="_blank"><img src="https://c5.patreon.com/external/logo/become_a_patron_button.png" alt="Become a Patreon" height="35" width="auto"></a>
<a href="https://www.buymeacoffee.com/akzdev" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="35" width="auto"></a>
</p>
