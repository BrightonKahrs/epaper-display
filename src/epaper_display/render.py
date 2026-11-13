from pathlib import Path

from PIL import Image, ImageOps


def prepare_image(
    path: Path,
    resolution: tuple[int, int],
    orientation: str = "portrait",
    portrait_rotation_degrees: int = 90,
) -> Image.Image:
    if orientation not in ("portrait", "landscape"):
        raise ValueError("orientation must be 'portrait' or 'landscape'.")
    if portrait_rotation_degrees not in (90, 270):
        raise ValueError("portrait_rotation_degrees must be 90 or 270.")
    content_resolution = (
        (resolution[1], resolution[0]) if orientation == "portrait" else resolution
    )
    with Image.open(path) as source:
        image = source.convert("RGB")
    fitted = ImageOps.contain(
        image, content_resolution, method=Image.Resampling.LANCZOS
    )
    canvas = Image.new("RGB", content_resolution, "white")
    canvas.paste(
        fitted,
        (
            (content_resolution[0] - fitted.width) // 2,
            (content_resolution[1] - fitted.height) // 2,
        ),
    )
    if orientation == "portrait":
        canvas = canvas.rotate(portrait_rotation_degrees, expand=True)
    return canvas
