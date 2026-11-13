import tempfile
import unittest
from pathlib import Path

from PIL import Image

from epaper_display.display import PreviewDisplay, show_slide
from epaper_display.render import prepare_image


class RenderTests(unittest.TestCase):
    def test_preview_renders_slide_at_target_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.png"
            output = root / "preview.png"
            Image.new("RGB", (300, 100), "red").save(source)

            show_slide(PreviewDisplay(output), source, "portrait", 90)

            with Image.open(output) as image:
                self.assertEqual(image.size, (1200, 1600))
                self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
                self.assertEqual(image.getpixel((600, 800)), (255, 0, 0))

            hardware_image = prepare_image(source, (1600, 1200), "portrait", 90)
            self.assertEqual(hardware_image.size, (1600, 1200))


if __name__ == "__main__":
    unittest.main()
