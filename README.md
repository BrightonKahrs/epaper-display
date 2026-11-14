# E-paper display

A persistent slide carousel for a Raspberry Pi Zero 2 W and the Pimoroni Inky
Impression Spectra 13.3-inch display.

The project turns a collection of images into an independently running display.
Slides are stored locally on the microSD card, so playback does not depend on
an internet connection. Optional JSON API sources refresh content in the
background without interrupting the carousel.

## What it does

- Displays portrait artwork on a 1200 x 1600 logical canvas.
- Rotates slides every 30 minutes by default, with persistent pause and resume.
- Supports manual selection and gallery management over SSH.
- Keeps slide images, metadata, and API source settings across reboots.
- Offers a desktop preview mode and opt-in experimental fast panel refresh.

The project is written in Python and uses Pillow for image preparation,
Questionary for interactive menus, SQLite for metadata, and Pimoroni's Inky
driver for the physical display. Example artwork includes Microsoft, GitHub,
and a Philadelphia-themed slide.

## Documentation

| Guide | Contents |
| --- | --- |
| [First-time setup](first_time_setup.md) | Prepare the Pi, install the app, display your first image, and optionally start it at boot. |
| [CLI instructions](cli_instructions.md) | Add and manage slides, control playback, configure API sources, and update the app. |
| [Software architecture](architecture.md) | Components, persistent storage, rendering, and API source contracts. |
| [Hardware architecture](hardware_architecture.md) | Hardware connections, a system diagram, parts, and purchasing links. |
