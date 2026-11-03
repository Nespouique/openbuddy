import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "c6lcd"))
with patch.dict(sys.modules, {
    "machine": types.SimpleNamespace(Pin=object, SPI=object, PWM=object),
    "framebuf": types.SimpleNamespace(),
}):
    display = importlib.import_module("st7789")


class DisplayTests(unittest.TestCase):
    def test_empty_text_does_not_create_zero_width_framebuffer(self):
        lcd = object.__new__(display.LCD)
        lcd.text("", 0, 0, 0xFFFF, 0)


if __name__ == "__main__":
    unittest.main()
