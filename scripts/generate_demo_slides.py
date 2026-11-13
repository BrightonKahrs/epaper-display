from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "examples" / "slides"
ASSET_DIR = ROOT / "examples" / "assets"
SIZE = (1200, 1600)
COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (242, 80, 34),
    "green": (127, 186, 0),
    "blue": (0, 164, 239),
    "yellow": (255, 185, 0),
    "navy": (20, 45, 80),
}


def new_slide(accent: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", SIZE, COLORS["white"])
    ImageDraw.Draw(image).rectangle((0, 0, SIZE[0], 38), fill=accent)
    return image


def centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    draw.text(((SIZE[0] - width) // 2, y), text, font=font, fill=fill)


def make_microsoft() -> None:
    image = new_slide(COLORS["blue"])
    draw = ImageDraw.Draw(image)
    pane_size, gap = 245, 24
    left = (SIZE[0] - pane_size * 2 - gap) // 2
    top = 300
    panes = (
        (left, top, COLORS["red"]),
        (left + pane_size + gap, top, COLORS["green"]),
        (left, top + pane_size + gap, COLORS["blue"]),
        (left + pane_size + gap, top + pane_size + gap, COLORS["yellow"]),
    )
    for x, y, color in panes:
        draw.rectangle((x, y, x + pane_size, y + pane_size), fill=color)
    centered_text(draw, "Microsoft", 940, ImageFont.load_default(size=112), COLORS["black"])
    centered_text(
        draw,
        "TEST SLIDE 01",
        1100,
        ImageFont.load_default(size=40),
        COLORS["navy"],
    )
    image.save(OUTPUT_DIR / "01-microsoft.png")


def make_github() -> None:
    with Image.open(ASSET_DIR / "github.png") as icon:
        icon = icon.convert("RGBA")
        icon = icon.resize((560, 560), Image.Resampling.LANCZOS)

    image = new_slide((24, 23, 23))
    image.paste(icon, ((SIZE[0] - icon.width) // 2, 255), icon)
    draw = ImageDraw.Draw(image)
    centered_text(draw, "GitHub", 940, ImageFont.load_default(size=112), COLORS["black"])
    centered_text(
        draw,
        "TEST SLIDE 02",
        1100,
        ImageFont.load_default(size=40),
        COLORS["navy"],
    )
    image.save(OUTPUT_DIR / "02-github.png")


def make_philly_love() -> None:
    image = new_slide(COLORS["yellow"])
    draw = ImageDraw.Draw(image)

    skyline = [
        (90, 490, 180, 680),
        (205, 420, 310, 680),
        (335, 520, 425, 680),
        (450, 365, 555, 680),
        (580, 465, 690, 680),
        (715, 400, 820, 680),
        (845, 500, 950, 680),
        (975, 445, 1110, 680),
    ]
    for index, bounds in enumerate(skyline):
        draw.rectangle(bounds, fill=COLORS["navy"] if index % 2 == 0 else COLORS["blue"])
    draw.rectangle((0, 680, SIZE[0], 710), fill=COLORS["navy"])

    centered_text(draw, "LOVE", 790, ImageFont.load_default(size=220), COLORS["red"])
    draw.rounded_rectangle((245, 1110, 955, 1124), radius=7, fill=COLORS["navy"])
    centered_text(draw, "PHILLY", 1160, ImageFont.load_default(size=90), COLORS["navy"])
    centered_text(
        draw,
        "TEST SLIDE 03",
        1310,
        ImageFont.load_default(size=40),
        COLORS["navy"],
    )
    image.save(OUTPUT_DIR / "03-philly-love.png")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    make_microsoft()
    make_github()
    make_philly_love()


if __name__ == "__main__":
    main()
