from pathlib import Path
from typing import Protocol

from PIL import Image

from epaper_display.render import prepare_image


class Display(Protocol):
    @property
    def resolution(self) -> tuple[int, int]: ...

    def show(self, image: Image.Image) -> None: ...


class InkyDisplay:
    def __init__(self) -> None:
        try:
            from inky.auto import auto
        except ImportError as exc:
            raise RuntimeError(
                "The Inky hardware package is required. Install with "
                "'pip install epaper-display[hardware]'."
            ) from exc
        self._display = auto()

    @property
    def resolution(self) -> tuple[int, int]:
        return tuple(self._display.resolution)

    def show(self, image: Image.Image) -> None:
        self._display.set_image(image)
        self._display.show()


class PreviewDisplay:
    """Write the prepared slide to disk instead of driving physical hardware."""

    def __init__(
        self,
        output_path: Path,
        orientation: str = "portrait",
        portrait_rotation_degrees: int = 90,
    ):
        self.output_path = output_path
        self._resolution = (1600, 1200)
        self.orientation = orientation
        self.portrait_rotation_degrees = portrait_rotation_degrees

    @property
    def resolution(self) -> tuple[int, int]:
        return self._resolution

    def show(self, image: Image.Image) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.orientation == "portrait":
            image = image.rotate(-self.portrait_rotation_degrees, expand=True)
        image.save(self.output_path, format="PNG")


def show_slide(
    display: Display,
    image_path: Path,
    orientation: str = "portrait",
    portrait_rotation_degrees: int = 90,
) -> None:
    display.show(
        prepare_image(
            image_path,
            display.resolution,
            orientation,
            portrait_rotation_degrees,
        )
    )
