<p align="center">
  <img src="assets/jujune_icon.png" width="148" alt="Jujune">
</p>

<h1 align="center">Jujune</h1>

<p align="center">
  Live overlay that tells you <em>why</em> the game lagged — not just that ping went up.
</p>

<p align="center">
  <a href="https://github.com/artem-sobolevskyi/jujune-releases/releases/latest"><img src="https://img.shields.io/github/v/release/artem-sobolevskyi/jujune-releases?style=flat-square&label=Windows%20EXE" alt="Latest Windows release"></a>
  <a href="https://github.com/artem-sobolevskyi/jujune-internet-tester/actions/workflows/build-windows.yml"><img src="https://img.shields.io/github/actions/workflow/status/artem-sobolevskyi/jujune-internet-tester/build-windows.yml?style=flat-square&label=Windows%20build" alt="Windows build"></a>
  <img src="https://img.shields.io/badge/python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-111827?style=flat-square" alt="Windows and macOS">
</p>

Jujune sits on top of a game and samples the network plus the machine every second. When a stutter hits, it decides whether the problem is local Wi-Fi, the ISP, disk, CPU, GPU, or thermals — and says so in plain language.

## Features

- Compact, expanded, and mini overlay modes, plus a system tray
- WAN ping (Cloudflare / Google), router RTT, jitter, and packet loss
- Extra hosts so you can watch a game server, not only `1.1.1.1`
- CPU, RAM, disk, GPU, temperature, and CPU clock on the same panel
- Live graphs for CPU, GPU, and game frame time
- Sound alert when lag is actually happening
- In-app update check against GitHub Releases
- Windows EXE built and published by GitHub Actions

## Download

Grab the latest Windows build from **[jujune-releases](https://github.com/artem-sobolevskyi/jujune-releases/releases/latest)** — `Jujune.exe`, no install step.

macOS: run from source or use `build_mac.sh` to make an app bundle.

## Run from source

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Useful flags:

```text
python main.py --test        # one console sample, no window
python main.py --expanded    # start on the full panel
```

Config lives in the app data directory (`config.json`). You can add game-server hosts there:

```json
{
  "extra_hosts": ["11.22.33.44"],
  "wan_host": "1.1.1.1",
  "wan_host_alt": "8.8.8.8",
  "sound": true
}
```

## How it diagnoses

Every tick Jujune compares WAN ping against the gateway, then against CPU, GPU, disk, and temperature. Typical verdicts:

| Signal | What Jujune infers |
| :--- | :--- |
| High gateway RTT | Lag is local — Wi-Fi or the router |
| Gateway is fine, WAN is not | ISP / upstream path |
| Packet loss ≥ 8% | Unstable link, not just latency |
| Disk or CPU spike with a hitch | The machine, not the network |
| Thermal climb | Throttling / heat |

## Stack

Python 3.12 · `psutil` · Pillow · pystray · Tkinter overlay · PyInstaller · GitHub Actions

## License

Personal / source-available. Use it, fork it, build on it.
