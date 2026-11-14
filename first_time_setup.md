# First-time setup

This guide covers a Raspberry Pi installation and an optional desktop preview.
See the [hardware architecture](hardware_architecture.md) for connections and parts,
[CLI instructions](cli_instructions.md) for day-to-day controls, and
[project overview](README.md) for a summary.

## 1. Prepare the Pi

Use Raspberry Pi OS with Python 3.11 or later. Configure Wi-Fi and SSH during
OS setup, then connect from your computer using the username and hostname you
chose:

```sh
ssh pi@raspberrypi.local
```

Replace those example SSH details throughout these guides. If hostname
resolution fails, use the Pi's current IP address. Your computer must be able to
reach the Pi's network.

Power off and disconnect power before attaching the display according to
Pimoroni's hardware instructions. Once booted, enable SPI:

```sh
sudo raspi-config
```

Choose **Interface Options > SPI**, enable it, and reboot if prompted. Ensure
the account running the display has the required GPIO/SPI device permissions.

## 2. Install the project

On the Pi:

```sh
sudo apt update
sudo apt install -y git python3-venv python3-pip
git clone https://github.com/BrightonKahrs/e-paper-display.git
cd e-paper-display
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[hardware]'
```

The hardware extra installs Pimoroni's Inky driver. The editable install ensures
future code pulls update the CLI's source rather than leaving an older installed
copy. Run commands inside this virtual environment.

Both `epaper-display` and the shorter `epd` executable run the same CLI.

## 3. Display your first image

Import one of the checked-in portrait examples:

```sh
epaper-display add examples/slides/microsoft-logo.png
epaper-display list
epaper-display run
```

The foreground process displays the first eligible slide and rotates every
30 minutes. Use Ctrl+C to stop it. Do not run two hardware display processes at
the same time.

To control it while it runs, open a second SSH terminal, activate the same
virtual environment, and select a slide:

```sh
cd ~/e-paper-display
source .venv/bin/activate
epaper-display display --pick
```

For custom artwork, start with a 1200 x 1600 portrait PNG.
[CLI instructions](cli_instructions.md) explain uploading, importing, replacing,
disabling, deleting, and pausing.

## 4. Understand configuration and storage

Without a configuration file or environment overrides, the app uses:

| Setting             | Default                         |
| ------------------- | ------------------------------- |
| Data directory      | `~/.local/share/epaper-display` |
| Slide interval      | 1800 seconds (30 minutes)       |
| API refresh polling | 30 seconds                      |
| Orientation         | Portrait                        |
| Portrait rotation   | 90 degrees                      |

`catalog.sqlite3`, `api-specs.sqlite3`, and `gallery/` live inside the data
directory. They persist across restarts. Confirm the paths with
`epaper-display status`.

For a per-user configuration, copy the example and edit it:

```sh
cp config.example.json config.json
```

F

```json
{
  "interval_seconds": 1800,
  "refresh_poll_seconds": 30,
  "display_orientation": "portrait",
  "portrait_rotation_degrees": 90
}
```

Select it explicitly with `epaper-display --config config.json run`, or set
`EPAPER_CONFIG` to its absolute path for both the service and control terminals.
The app does not load a checkout's `config.json` automatically. Its default
configuration path is `/etc/epaper-display/config.json`.

Change portrait rotation to `270` if needed for your mounting direction.
Orientation can also be `landscape`. An optional `data_dir` field selects
another storage directory; `EPAPER_DATA_DIR` takes precedence over that field.
Use the same settings for the running carousel and all control commands.

## 5. Optional: start automatically with systemd

The supplied unit assumes:

- The checkout and virtual environment are at `/opt/epaper-display`.
- The service account is `pi` with appropriate hardware access.
- Configuration is `/etc/epaper-display/config.json`.
- Persistent data is `/var/lib/epaper-display`.

Stop any foreground carousel first. Prepare the checkout at your chosen
installation location and install it using step 2. If you keep the checkout
under your home directory instead, edit the installed unit's `User`,
`WorkingDirectory`, and `ExecStart` to match your account, checkout, and virtual
environment. Review `SupplementaryGroups` for your Pi's device permissions.

Install the configuration and unit from the checkout:

```sh
sudo install -D -m 0644 config.example.json /etc/epaper-display/config.json
sudo install -D -m 0644 deploy/epaper-display.service /etc/systemd/system/epaper-display.service
sudoedit /etc/systemd/system/epaper-display.service
sudo systemctl daemon-reload
sudo systemctl enable --now epaper-display
sudo systemctl status epaper-display
```

Use the configuration-copy command only for a new installation; preserve any
existing configuration. `StateDirectory` creates the service data directory.
The service's explicit `EPAPER_DATA_DIR` overrides the configuration file.

**The service gallery is separate from the default per-user gallery.** Images
imported in step 3 will not appear in it automatically. After starting the unit,
import your images again using the service's user and environment, for example:

```sh
sudo -u pi env EPAPER_CONFIG=/etc/epaper-display/config.json \
  EPAPER_DATA_DIR=/var/lib/epaper-display \
  /opt/epaper-display/.venv/bin/epaper-display add \
  /opt/epaper-display/examples/slides/microsoft-logo.png
```

Adjust the account and paths if you customized the unit. Use that same command
prefix for other controls, as explained in [CLI instructions](cli_instructions.md).
Alternatively, deliberately configure the unit to use your existing gallery,
ensuring the service account has access; do not accidentally manage two galleries.

View service logs or restart after updating:

```sh
sudo journalctl -u epaper-display -n 50 --no-pager
sudo systemctl restart epaper-display
```

API tokens and experimental fast refresh are optional; their configuration and
limitations are covered in [CLI instructions](cli_instructions.md).

## Optional: develop or preview without a Pi

Python 3.11 or later is required; hardware drivers are not needed:

```sh
python -m venv .venv
```

Activate with `source .venv/bin/activate` on Linux/macOS, or
`.\.venv\Scripts\Activate.ps1` in Windows PowerShell, then:

```sh
python -m pip install -e .
python scripts/generate_demo_slides.py
epaper-display add examples/slides/microsoft-logo.png
epaper-display run --preview preview.png
```

The generated examples include Microsoft, GitHub, and an original Philadelphia
LOVE cityscape. The preview uses the same carousel and image preparation as the
hardware path, saving an upright portrait PNG. Use a second terminal for CLI
controls.

Run the existing test suite from the checkout:

```sh
python -m unittest discover -s tests -v
```
