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

`list` shows enabled, non-expired slides with their IDs, titles, sources, and
logical folder paths (`/` means no folder).
`list --all` also includes disabled and expired entries and labels their state.
`list --scoped` limits either listing to the current playback scope.
`status` reports the total and scoped active slide counts, scope, selected slide,
pause state, next transition, storage paths, and API refresh status. It also
reports the service heartbeat, last successful display refresh, current refresh,
and display error separately from the selected slide.

## Terminal dashboard

Install the optional dashboard extra in the project's virtual environment, then
launch it in a second terminal while the carousel continues running:

```sh
python -m pip install -e '.[hardware,tui]'
epd dashboard
```

For a preview-only installation use `python -m pip install -e '.[tui]'`.
After updating an existing installation, restart the carousel once so it begins
reporting its display state. Use the same user, configuration, and data-directory
environment as the service, including the systemd command prefix shown above.
The dashboard never starts a second renderer.

Mouse clicks and scrolling work in terminals that support mouse reporting,
including Windows Terminal over SSH. Use `ssh -t` to allocate a terminal.
An 80-column by 24-row terminal or larger is recommended.
All operations also have keyboard controls:

| Key | Action |
| --- | --- |
| Tab / Shift+Tab | Move between panes and controls. |
| Arrows | Browse slides or folders. |
| Enter | Display the highlighted slide when the slide table has focus; otherwise activate the focused control. |
| Space | Pause or resume automatic rotation when browsing slides. |
| n / p | Select the next or previous scheduled slide. |
| s | Apply the browsed folder as the playback scope. |
| a | Add an image, with an optional carousel folder path. |
| / / Escape | Focus search / return to the slide table. |
| m / e | Move the highlighted slide / enable or disable it. |
| r / Delete | Replace a local image / open its delete confirmation. |
| ? | Show help. |
| q / Ctrl+Q | Close the dashboard; Ctrl+Q also works while searching. |

Single-clicking a row or browsing folders does **not** change playback.
Double-click a slide, click **Display**, or press Enter in the slide table to
request that slide. Disabled, expired, and out-of-scope slides remain ineligible.
Click **Scope folder** or
press `s` to apply the browsed folder; select **All folders** to restore the
whole-gallery scope. Search and folder browsing filter only the table, which
includes disabled, expired, and out-of-scope slides for management. Only enabled,
unexpired slides inside the active playback scope can be shown.

Click **Add** or press `a` to import an image from a path on the Pi. The dialog
includes a folder field, initially set to the browsed folder; enter any existing
or new nested path, or leave it blank (or `/`) for an unfiled slide. Adding does
not explicitly change playback or scope. Search and folder filters still apply
to the new entry. Invalid paths or images leave the dialog open for correction.

The move dialog offers existing folders or a new nested path; `/` removes the
folder assignment. Replacement paths refer to files **on the Pi**, not the
computer running your SSH client. Local deletion requires confirmation. API
slides can be moved or enabled/disabled, but not replaced or deleted locally.
API-provided folder assignments may overwrite local moves on refresh.

**Selected** is a request; **Last displayed** is the last image whose display
driver refresh completed successfully, not physical verification of the panel.
Preview runs instead report **Preview saved**. A refresh in progress or failure
is shown separately, and the last successful image remains reported even if it
is subsequently disabled or deleted. **Unknown** means this catalog has no
service heartbeat; **Stopped** is reported on clean shutdown. After 90 seconds
without a heartbeat the dashboard warns that the service may be offline or
blocked; a very long panel refresh can also cause that warning.

The upcoming list is a **read-only projection** of the folder-balanced scheduler.
It never advances the carousel's saved selection or folder cursors. It assumes
current eligibility and scope: expiry, API imports, folder changes, or manual
controls can change the eventual sequence. Pausing holds timed playback but
still allows explicit selections. No terminal image preview is rendered.

Closing the dashboard or disconnecting SSH leaves the separately running
carousel untouched. The dashboard can edit selections while the service is
stopped; playback waits for that service to start.

## Add images

```sh
epaper-display add /tmp/my-slide.png
epaper-display add /tmp/first.png /tmp/second.png
epd add /tmp/jupiter.png --folder space/planets
```

Each import copies the image into the persistent gallery and prints its ID and
title. The temporary upload is no longer needed after a successful import.
Adding the same file again creates another entry; use `replace` to update one.
`--folder` is optional and applies to every image in that import. Without it,
new slides have no folder, regardless of the current playback scope.

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

## Bulk import ZIP files or directories

Upload a ZIP to the Pi with `scp`, then import it directly without manually
unzipping:

```powershell
scp "C:\Users\YourName\Downloads\epaper.zip" pi@raspberrypi.local:/tmp/epaper.zip
```

In the Pi terminal, using the carousel's same configuration and data directory:

```sh
epd bulk-add /tmp/epaper.zip
epd bulk-add /tmp/epaper.zip --preserve-folders
epd bulk-add /tmp/epaper.zip --preserve-folders --ignore-root
epd bulk-add /tmp/epaper-zip --preserve-folders --ignore-root
```

`bulk-add` recursively imports supported PNG, JPEG, WebP, BMP, GIF, and TIFF
images from a ZIP or an existing directory. Without `--preserve-folders`, all
imported slides are unfiled. With it, image parent paths become logical carousel
folders. The enclosing root folder is **included by default**; `--include-root`
also makes this explicit. `--ignore-root` requires `--preserve-folders`.

For a ZIP entry `epaper-zip/space/planets/venus.png`:

| Options | Carousel folder |
| --- | --- |
| No flags | `/` (unfiled) |
| `--preserve-folders` | `epaper-zip/space/planets` |
| `--preserve-folders --ignore-root` | `space/planets` |

The ZIP's filename does not create a folder. Ignoring its root requires a single
common enclosing folder across all imported images; loose images or multiple
top-level folders cause an explicit error rather than dropping meaningful
path segments. For directory imports, the selected directory's name is the
root, not `/tmp` or its other ancestors. Images directly in the ignored root
become unfiled.

Imports follow path order, use the image filename stem as the slide title,
and print each ID, title, and folder plus a final count. Each run creates new
slides; it does not deduplicate previous imports. Non-image files and macOS
archive metadata are skipped with log messages. Invalid images stop the import
with an error identifying the file and number already imported; those earlier
slides remain available. The batch is not one atomic transaction.

ZIP images are unpacked one at a time into temporary storage, then copied into
the existing persistent gallery. Temporary unpacked files are cleaned up, and
the uploaded ZIP or source directory is left untouched. Unsafe paths,
duplicate archive paths, links, special files, and encrypted image entries are
rejected. A batch is limited to 10,000 entries, 64 MiB per image, and 1 GiB of
total uncompressed image data. Use smaller batches on a Pi with limited free
space; these limits do not reserve disk capacity.

## Select slides

```sh
epaper-display display --pick
epaper-display display <slide-id>
epaper-display next
epaper-display previous
```

In the picker, use Up/Down and Enter; Ctrl+C cancels. It starts on the current
slide when available and includes IDs to distinguish duplicate titles.
Use either a slide ID or `--pick`, not both. The older `pick` and `show <slide-id>`
commands remain available as compatibility aliases.

Selections persist in SQLite. The running carousel displays the selection on
its next poll, after any panel refresh already in progress finishes. If stopped,
the carousel restores the selected eligible slide when it starts.

## Organize folders and scope playback

Folders are logical metadata, not directories on disk. A slide belongs to one
folder or to the unfiled root (`/`). Paths use `/`, can have any depth, and are
case-sensitive. Folder names may contain spaces when quoted. Parent folders
are inferred automatically; no separate folder-creation command is needed.
Existing galleries are upgraded automatically with all slides initially unfiled.

```sh
epd add /tmp/jupiter.png --folder space/planets
epd add /tmp/moon.png --folder space/planets/moons
epd move <slide-id> --folder space/planets
epd move --pick --folder "travel/summer photos"
epd move --pick --root
```

`move` changes only folder membership: the ID, image file, title, enabled
state, and import order stay intact. The move picker includes all local and
API slides, including disabled or expired ones, even outside the playback scope.
Folder membership survives image replacement and API refreshes that do not
supply a folder assignment. API-provided folders or source defaults take
precedence on refresh, as described below. Moving the
last slide out of a folder removes that otherwise empty folder from the picker.

```sh
epd scope --pick
epd scope space
epd scope space/planets
epd scope --all
epd scope
epd list --scoped
```

The scope picker offers the whole gallery and every folder, including parents
whose images exist only in descendants. `scope` without arguments reports the
current scope; `scope --all` restores whole-gallery playback.
Changes affect the running carousel on its next poll; no restart is needed.
Scope is shared by controls using the same catalog and persists across service
restarts until changed. It is not a separate scope per SSH terminal.

Within the selected scope, rotation has one turn for **each slide directly in
that scope**, followed by one turn for **each immediate child folder**. Loose
slides follow import order; child folders follow alphabetical, case-sensitive
path order. On each folder turn, the next eligible image is selected from that
folder **and all its descendants**, in import order. Descendant folders do not
get additional turns at this level. Each folder's progress is remembered
separately for each playback scope.

For example, with unfiled slides A and B, an `earth` slide in `space`, and a
`jupiter` slide in `space/planets`, whole-gallery playback is:

```text
A -> B -> earth -> A -> B -> jupiter -> A -> B -> earth -> ...
```

When scoped to `space`, `earth` is a direct slide and `planets` is a child-folder
turn. If scoped to a leaf folder with no child folders, playback is simply its
slides in import order. `next` uses this same rotation; `previous` steps through
the groups and their image cursors in reverse, rather than undoing display history.
Manual `display` selections and its picker are restricted to active slides in
the current scope; change scope first to display an image elsewhere.

Disabled, expired, or deleted slides never participate, and child folders with
no eligible descendants get no turn. A scope with no eligible slides stays empty
rather than falling back to images outside it; the physical panel retains its
last image. New eligible slides in the scope are picked up automatically.
Scope changes and moving a current slide out of scope can select a replacement
even while timed rotation is paused, just like disabling a current slide.
The scope picker can show folders containing only disabled/expired slides.

## Replace or delete slides

```sh
epaper-display replace <slide-id> /tmp/updated-slide.png
epaper-display replace --pick /tmp/updated-slide.png
epaper-display delete <slide-id>
epaper-display delete --pick
```

These commands apply only to local slides. Replacement keeps the ID, title,
position, folder, and enabled state. The running carousel redraws a replaced current
slide. Deletion permanently removes the entry and its gallery image.

Provide either a slide ID or `--pick`, never both. The old positional `pick`
syntax has been replaced by the `--pick` flag for management commands.

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

Pausing stops timed rotation, not the service or API refreshes. Manual `display`,
`next`, and `previous` commands still work. Pause persists across service
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

See [observability](observability.md) for optional OpenTelemetry logs and metrics
for the running carousel and manual source refreshes.

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
ssh -t pi@raspberrypi.local '~/e-paper-display/.venv/bin/epaper-display display --pick'
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
epaper-display display --pick
```

The source ID becomes the slide title. Each successful refresh updates the same
slide, even if the URL or query parameters change. If the selected image changes,
the running service redraws it; unchanged image bytes do not trigger a redraw.
Failed downloads or invalid images retain the cached slide and report an error
in `status`, with the same retry backoff used for JSON feeds.

Direct sources support `--query-param`, `--token-env`, `--timeout-seconds`,
`--folder`, TTL, and source enable/disable. They accept PNG, JPEG, WebP, BMP, GIF, and TIFF
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

### API slide folder metadata

JSON slide objects can include an optional `folder` path. It uses the same
logical paths and nested-folder scheduling as local slides:

```json
{
  "slides": [
    {
      "id": "jupiter",
      "title": "Jupiter",
      "image_url": "https://example.com/images/jupiter.png",
      "folder": "space/planets"
    }
  ]
}
```

By default the app reads `folder` from each slide object. For a differently
shaped feed, set `--folder-key metadata.folder` to map a nested field.
Use `--folder-key ""` to ignore the feed's folder field.

You can also configure a source-level default folder for JSON feeds or
direct-image endpoints (which have no JSON metadata):

```sh
epd sources configure space-feed https://example.com/api/slides \
  --folder space --folder-key metadata.folder
epd sources configure chart https://example.com/chart.png \
  --type image --folder dashboards/charts
epd refresh --force
```

Folder assignment on each successful import follows these rules:

| Input | Result |
| --- | --- |
| A per-slide folder string | Assign that path, overriding the source default. |
| Explicit per-slide `null`, `""`, or `"/"` | Assign the unfiled root, overriding the default. |
| No mapped folder field, but a source `--folder` | Assign the source default. |
| Neither per-slide folder nor source default | Keep an existing slide's folder; new slides start unfiled. |

Supplied metadata and source defaults **update existing slides on refresh**,
so a local `move` can be overwritten by the next refresh. To manage folders
only on the Pi, use `--folder-key ""` and leave off the source `--folder`.
An explicit source `--folder /` forces root assignment rather than preserving
an existing folder. Folder changes do not create a duplicate slide or reset
its enabled state, and they take effect in scoped playback without a restart.

Folders must be strings or `null`; invalid types or paths such as `space/../art`
fail that import and are reported through the usual source error/backoff.
Imports completed earlier in a JSON response are not rolled back if a later
item fails. Images and folder metadata for the failing item remain unchanged.

`sources list` includes `folder` (the default) and `folder_key` (the mapping).
Existing source databases gain these settings automatically without resetting
their refresh state. Defaults are no source folder and a `folder` field mapping;
old feeds without that field keep their current behavior. As with other source
settings, `sources configure` replaces the specification: repeat any default
folder and custom mapping you want to retain when reconfiguring a source.

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
