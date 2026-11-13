import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from epaper_display.display import InkyDisplay


class InkyDisplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = SimpleNamespace(
            NORMAL=0x08,
            FAST=0x07,
            speed=0x08,
            resolution=(1600, 1200),
            set_image=Mock(),
            show=Mock(),
        )
        self.auto = Mock(return_value=self.driver)
        modules = patch.dict(
            sys.modules,
            {
                "inky": ModuleType("inky"),
                "inky.auto": SimpleNamespace(auto=self.auto),
            },
        )
        modules.start()
        self.addCleanup(modules.stop)

    def test_normal_refresh_keeps_driver_default(self) -> None:
        display = InkyDisplay()
        self.assertEqual(self.driver.speed, self.driver.NORMAL)
        self.assertEqual(display.resolution, (1600, 1200))

    def test_fast_refresh_uses_driver_speed_and_keeps_blocking_show(self) -> None:
        with self.assertLogs("epaper_display.display", level="WARNING") as logs:
            display = InkyDisplay(fast=True)
        self.assertIn("Experimental FAST", logs.output[0])
        self.assertEqual(self.driver.speed, self.driver.FAST)

        image = Image.new("RGB", (1600, 1200), "red")
        display.show(image)
        self.driver.set_image.assert_called_once_with(image)
        self.driver.show.assert_called_once_with()

    def test_fast_refresh_rejects_unsupported_or_old_driver(self) -> None:
        for driver in (SimpleNamespace(), SimpleNamespace(FAST=0x07)):
            with self.subTest(driver=driver):
                self.auto.return_value = driver
                with self.assertRaisesRegex(ValueError, "Inky 2.6.0"):
                    InkyDisplay(fast=True)
                self.assertFalse(hasattr(driver, "speed"))


if __name__ == "__main__":
    unittest.main()
