# CLI instructions

See [first-time setup](first_time_setup.md) for installation, or return to the
[project overview](README.md).

## Before running commands

The examples below run in a Pi terminal with the project's virtual environment
activated:

```sh
cd ~/e-paper-display
source .venv/bin/activate
epaper-display --help
```

`epd` is an equivalent installed command, not a shell-specific alias. Every
command and option works with either name, for example `epd status`,
`epd delete --pick`, or `epd run --fast`. Reinstall the package after pulling
this update to create the new executable, even with an editable installation:

```sh
python -m pip install -e '.[hardware]'
```

For preview-only installations, use `python -m pip install -e .` instead.

Use the **same user, configuration, and data directory** as the running carousel.
Otherwise, your commands may control a different gallery. `epaper-display status`
shows the paths in use. By default, data lives in
`~/.local/share/epaper-display`; the supplied systemd unit instead uses
`/var/lib/epaper-display`.

For the supplied systemd installation, use its configured service user and
environment. For example, if its `User` is `pi`:

```sh
sudo -u pi env EPAPER_CONFIG=/etc/epaper-display/config.json \
  EPAPER_DATA_DIR=/var/lib/epaper-display \
  /opt/epaper-display/.venv/bin/epaper-display status
```

Substitute other subcommands for `status` as needed. Do not mix this setup with
the default per-user gallery.

## Run and inspect the carousel

```sh
epaper-display run
epaper-display status
epaper-display list
epaper-display list --all
```

`run` stays in the foreground until Ctrl+C. Use a second terminal for controls.
Only one process should use the physical panel at a time; stop the systemd
service before starting a foreground hardware process.

`list` shows enabled, non-expired slides with their IDs, titles, and sources.
`list --all` also includes disabled and expired entries and labels their state.
`status` reports the active slide count, selected slide, pause state, next
transition, storage paths, and API refresh status.

## Add images

```sh
epaper-display add /tmp/my-slide.png
epaper-display add /tmp/first.png /tmp/second.png
```

Each import copies the image into the persistent gallery and prints its ID and
title. The temporary upload is no longer needed after a successful import.
Adding the same file again creates another entry; use `replace` to update one.

For the default portrait setup, create PNG artwork at **1200 x 1600 pixels**.
Other sizes are fitted onto a white canvas without stretching. The renderer
handles rotation, and the Inky driver handles panel colour conversion.

To transfer an image from Windows PowerShell, replace the username and hostname
with your Pi's SSH details:

```powershell
scp "C:\Users\YourName\Downloads\my-slide.png" pi@raspberrypi.local:/tmp/my-slide.png
```

Then run `epaper-display add /tmp/my-slide.png` on the Pi. If the hostname does
not resolve, use the Pi's current IP address.

## Select slides

```sh
epaper-display pick
epaper-display show <slide-id>
epaper-display next
epaper-display previous
```

In the picker, use Up/Down and Enter; Ctrl+C cancels. It starts on the current
slide when available and includes IDs to distinguish duplicate titles.

Selections persist in SQLite. The running carousel displays the selection on
its next poll, after any panel refresh already in progress finishes. If stopped,
the carousel restores the selected eligible slide when it starts.

## Replace or delete slides

```sh
epaper-display replace <slide-id> /tmp/updated-slide.png
epaper-display replace --pick /tmp/updated-slide.png
epaper-display delete <slide-id>
epaper-display delete --pick
```

These commands apply only to local slides. Replacement keeps the ID, title,
position, and enabled state. The running carousel redraws a replaced current
slide. Deletion permanently removes the entry and its gallery image.

Provide either a slide ID or `--pick`, never both. The old positional `pick`
syntax has been replaced by the `--pick` flag for management commands;
the standalone `epaper-display pick` command still selects a slide for display.

The management pickers include disabled and expired local slides. `delete --pick`
asks for confirmation; deletion by ID does not. Cancelling leaves the entry
unchanged. If the current slide is deleted, the service chooses another available
slide. If none are available, the physical panel retains its last image.

## Enable or disable individual slides

```sh
epaper-display disable <slide-id>
epaper-display disable --pick
epaper-display enable <slide-id>
epaper-display enable --pick
epaper-display list --all
```

Disabling excludes a slide from playback without deleting its image. The enable
menu shows disabled slides; the disable menu shows enabled slides. This works
for both local and API-sourced slides, independently of API source settings.
Disabled slides stay disabled across refreshes and local image replacements.

An enabled but expired slide remains ineligible until its expiry is updated.
If the current slide is disabled, the carousel chooses another available slide;
if none are available, the last image remains on the panel.

## Pause or resume automatic rotation

```sh
epaper-display pause
epaper-display resume
epaper-display status
```

Pausing stops timed rotation, not the service or API refreshes. Manual `pick`,
`show`, `next`, and `previous` commands still work. Pause persists across service
restarts and reboots. While paused, `status` reports `rotation_paused: true` and
no next transition.

Resume starts a fresh full interval (30 minutes by default), rather than
catching up on missed transitions. Pausing does not cancel a panel refresh in
progress. Replacing the current image still updates it; deleting, disabling,
or expiring the current slide still makes the service select another available
slide.

## Preview without hardware

```sh
epaper-display run --preview preview.png
```

This runs the carousel but writes the rendered image to a PNG rather than the
panel. The default preview is upright portrait at 1200 x 1600 pixels.

## Experimental fast refresh

```sh
epaper-display run --fast
```

Normal refresh remains the default. FAST requires Inky 2.6.0 or newer and a
supported Spectra 6 panel. It cannot be combined with `--preview`; unsupported
drivers or displays report an error.

[Pimoroni's Inky 2.6.0 release notes](https://github.com/pimoroni/inky/releases/tag/v2.6.0)
report roughly 19-20 seconds for a normal 13.3-inch refresh versus 8-9 seconds
in FAST mode, plus driver overhead and image preparation. FAST is experimental:
colours can shift, especially blue and green, and long-term effects on ghosting,
image retention, and panel lifetime have not been evaluated. It changes refresh
speed, not the carousel interval.

For systemd, append `--fast` to the installed unit's `ExecStart`, then reload and
restart the service:

```sh
sudo systemctl daemon-reload
sudo systemctl restart epaper-display
```

## Control over SSH

For a checkout installed under the remote user's home directory:

```sh
ssh pi@raspberrypi.local '~/e-paper-display/.venv/bin/epaper-display status'
ssh -t pi@raspberrypi.local '~/e-paper-display/.venv/bin/epaper-display pick'
```

Use `ssh -t` for all interactive pickers. Calling the virtual environment's
executable directly avoids depending on shell activation in a one-shot SSH
command. For a systemd installation, use the service paths and environment
described above instead.

## Manage API sources

Source specifications and refresh TTL state live in `api-specs.sqlite3`,
separate from slide metadata and carousel state in `catalog.sqlite3`.

### Direct image URLs

Use `--type image` for an endpoint that returns an image directly, with no JSON
wrapper. For example, to test with a 1200 x 1600 PNG:

```sh
epaper-display sources configure image-test \
  'https://placehold.co/1200x1600/png?text=E-paper+API+test' \
  --type image --ttl-seconds 3600
epaper-display refresh --force
epaper-display pick
```

The source ID becomes the slide title. Each successful refresh updates the same
slide, even if the URL or query parameters change. If the selected image changes,
the running service redraws it; unchanged image bytes do not trigger a redraw.
Failed downloads or invalid images retain the cached slide and report an error
in `status`, with the same retry backoff used for JSON feeds.

Direct sources support `--query-param`, `--token-env`, `--timeout-seconds`,
TTL, and source enable/disable. They accept PNG, JPEG, WebP, BMP, GIF, and TIFF
images supported by the gallery, not SVG or HTML pages. Images need not match
the panel dimensions; the renderer fits them. JSON mapping options do not apply.

For QuickChart, specify `width=1200`, `height=1600`, `devicePixelRatio=1`,
`format=png`, and your chart configuration in the URL or query parameters.
Only send data you intend to share with the external image provider.

### JSON feeds

The default is `--type json`, preserving existing sources. Use it when an
endpoint returns a list of slide IDs and image URLs:

```sh
epaper-display sources configure weather https://example.com/api/slides \
  --ttl-seconds 3600 \
  --query-param city=Philadelphia \
  --token-env WEATHER_TOKEN \
  --slides-key data.slides \
  --id-key id \
  --title-key title \
  --image-url-key image_url \
  --expires-at-key expires_at \
  --timeout-seconds 20
epaper-display sources list
epaper-display sources disable weather
epaper-display sources enable weather
epaper-display sources remove weather
epaper-display refresh
epaper-display refresh --force
```

Replace the example endpoint with your provider. Configure creates or replaces a
source specification, preserving an existing source's enabled state. Repeat
`--query-param KEY=VALUE` for multiple parameters. Field mappings accept
dot-separated paths. `refresh` fetches due sources; `--force` refreshes all
enabled sources regardless of TTL.

`sources list` includes each source's `type`. Existing databases are upgraded
automatically, with older sources treated as JSON feeds. When reconfiguring an
image source, include `--type image` again; omitting it selects JSON mode.
If you change a source between JSON and image modes, previously imported slides
are retained and can be individually disabled if no longer needed.

### Source state and authentication

Source enable/disable controls future refreshes, not playback of already imported
slides. Removing a source removes its specification, not its imported images.
Use individual slide enable/disable to control playback.

Put bearer tokens in environment variables, never in CLI arguments or JSON
configuration. For systemd, store them in `/etc/epaper-display/environment` and
restrict the file to root:

```sh
sudo touch /etc/epaper-display/environment
sudo chmod 600 /etc/epaper-display/environment
sudoedit /etc/epaper-display/environment
```

Use lines such as `WEATHER_TOKEN=your-token`. Restart the service after changing
its environment file. That file is not automatically loaded by separate CLI
processes; a manual authenticated refresh needs the token in its own process
environment too. Only the variable's name is persisted in SQLite.

See [software architecture](software_architecture.md) for the API response contract and storage
boundaries.

## Update an existing installation

From your checkout, with its virtual environment active:

```sh
git pull origin main
python -m pip install -e '.[hardware]'
```

For preview-only installations, use `python -m pip install -e .` instead.
Restart the running carousel after updating. With an editable installation
(`-e`), future Python-only code changes take effect after pulling and restarting;
dependency changes still require reinstalling.

If a new command is reported as an invalid choice, confirm you pulled the latest
code and reinstalled into the virtual environment supplying `epaper-display`.

## Configuration and help

```sh
epaper-display --config /path/to/config.json status
epaper-display sources configure --help
```

`--config` is a global option and goes before the subcommand. Configuration
defaults to `/etc/epaper-display/config.json`, or `EPAPER_CONFIG` if set.
`EPAPER_DATA_DIR` overrides the data directory in configuration. See
[first-time setup](first_time_setup.md) for configuration fields and service setup.
