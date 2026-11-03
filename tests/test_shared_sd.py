import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "c6lcd"))
from sdcard_spi import ReadOnlySDCard, crc7


class SDTests(unittest.TestCase):
    def test_command_crc_matches_sd_initialization_vectors(self):
        self.assertEqual(crc7(bytes((0x40, 0, 0, 0, 0))) | 1, 0x95)
        self.assertEqual(crc7(bytes((0x48, 0, 0, 1, 0xAA))) | 1, 0x87)

    def test_block_device_is_read_only(self):
        self.assertFalse(hasattr(ReadOnlySDCard, "writeblocks"))

    def test_read_failure_releases_chip_select(self):
        card = object.__new__(ReadOnlySDCard)
        card.sectors = 100
        card.address_multiplier = 1
        card.baudrate = 8_000_000
        card.bus = Mock()
        card.spi = Mock()
        card.cs = Mock()
        card.command = Mock(return_value=0)
        card.read_data = Mock(side_effect=OSError("removed"))
        with self.assertRaises(OSError):
            card.readblocks(0, bytearray(512))
        card.cs.assert_called_with(1)
        card.bus.configure.assert_called_with(8_000_000)

    def test_read_rejects_out_of_range_before_io(self):
        card = object.__new__(ReadOnlySDCard)
        card.sectors = 100
        with self.assertRaises(ValueError):
            card.readblocks(100, bytearray(512))

    def test_speed_switching_deselects_both_devices_before_spi_init(self):
        with patch.dict(sys.modules, {"machine": types.SimpleNamespace(Pin=Mock, SPI=Mock)}):
            shared = importlib.import_module("shared_spi")
        levels = {}

        class FakePin:
            OUT = 1

            def __init__(self, number, mode=None, value=None):
                self.number = number
                if value is not None:
                    levels[number] = value

            def __call__(self, value):
                levels[self.number] = value

        class FakeSPI:
            def __init__(self, *args, **kwargs):
                self.changes = []

            def init(self, **kwargs):
                if levels[shared.cfg.LCD_CS] != 1 or levels[shared.cfg.SD_CS] != 1:
                    raise AssertionError("SPI reconfigured while a device is selected")
                self.changes.append(kwargs["baudrate"])

        with patch.object(shared, "Pin", FakePin), patch.object(shared, "SPI", FakeSPI):
            bus = shared.SharedSPI()
            bus.lcd_cs(0)
            bus.for_sd()
            bus.sd_cs(0)
            bus.for_lcd()
            self.assertEqual(bus.spi.changes, [8_000_000, 40_000_000])
