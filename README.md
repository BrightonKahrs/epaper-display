# E-paper display

A Raspberry Pi Zero 2 W slide carousel for the Pimoroni Inky Impression Spectra
13.3-inch display. It keeps a persistent local gallery on the microSD card,
cycles through slides every 30 minutes, accepts new images and slide controls
over SSH, and can refresh configured JSON API sources in the background.

See [architecture.md](architecture.md) for the software design and API source
contract, and [hardware_spec.md](hardware_spec.md) for the parts list.

## Develop and preview

Requires Python 3.11 or later and Pillow:

```sh
python -m pip install -e .
python scripts/generate_demo_slides.py
epaper-display add examples/slides/*.png
epaper-display list
epaper-display run --preview preview.png
```

The checked-in examples are portrait slides: Microsoft logo and name, GitHub
logo and name, and an original Philly LOVE cityscape with "PHILLY" below it.
`run --preview` uses the real carousel and writes an upright 1200x1600 preview
to `preview.png` rather than requiring the panel.

## Install on the Pi

Install the hardware extra so the Pimoroni `inky` driver is available:

```sh
cd /opt/epaper-display
python3 -m venv .venv
.venv/bin/python -m pip install '.[hardware]'
```

Enable SPI using `sudo raspi-config`, then copy `config.example.json` to
`/etc/epaper-display/config.json`. Install the service unit and start it:

```sh
sudo install -D -m 0644 deploy/epaper-display.service /etc/systemd/system/epaper-display.service
sudo systemctl daemon-reload
sudo systemctl enable --now epaper-display
```

The unit expects the checkout at `/opt/epaper-display`; adjust its `User`,
installation path, and GPIO access for your Pi. `StateDirectory` ensures the
two SQLite databases and image gallery are created at
`/var/lib/epaper-display` on the microSD card.

## Control over SSH

Once installed and running, invoke the CLI on the Pi using SSH:

```sh
ssh pi@raspberrypi 'epaper-display status'
ssh pi@raspberrypi 'epaper-display list'
ssh pi@raspberrypi 'epaper-display next'
ssh pi@raspberrypi 'epaper-display previous'
ssh pi@raspberrypi 'epaper-display show <slide-id>'
```

To push an image from your computer, copy it to the Pi and add it to the
gallery:

```sh
scp ./my-slide.png pi@raspberrypi:/tmp/my-slide.png
ssh pi@raspberrypi 'epaper-display add /tmp/my-slide.png'
```

The `show`, `next`, and `previous` commands persist the selection. The running
service draws it on its next poll; if the service is stopped, it displays that
selection when it starts.

## API sources

API source specifications and refresh TTL state live in a separate
`api-specs.sqlite3` database; slide metadata and carousel state remain in
`catalog.sqlite3`. Configure sources over SSH, including request parameters,
TTL, and response mappings:

```sh
ssh pi@raspberrypi 'epaper-display sources configure weather https://example.com/api/slides --ttl-seconds 3600 --query-param city=Philadelphia --token-env WEATHER_TOKEN --slides-key data.slides'
ssh pi@raspberrypi 'epaper-display sources list'
ssh pi@raspberrypi 'epaper-display refresh --force'
```

Put API bearer tokens in `/etc/epaper-display/environment` (for example
`WEATHER_TOKEN=your-token`) and restrict that file to root (`chmod 600`). The
token value is never saved in the source database. Each enabled source has its
own persisted TTL and retries failures with bounded backoff. Refresh requests
run in the background, independently from slide changes. See
[architecture.md](architecture.md) for all source fields and module
responsibilities.

The systemd environment file is available to the running service, not to
separate SSH CLI processes. For a manual refresh of an authenticated source,
the token environment variable must also be exported in that CLI process.

## Tests

```sh
python -m unittest discover -s tests -v
```
