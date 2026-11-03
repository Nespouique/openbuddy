import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pc.pet import CLIPS, STATE_CLIPS, convert, rgb565, valid_id


class ConversionTests(unittest.TestCase):
    def test_rgb565_high_byte_first(self):
        image = Image.new("RGB", (4, 1))
        image.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])
        self.assertEqual(rgb565(image), b"\xf8\x00\x07\xe0\x00\x1f\xff\xff")

    def test_generated_pack_dimensions_sizes_and_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sheet = root / "sheet.png"
            Image.new("RGBA", (1536, 2288), (255, 0, 0, 255)).save(sheet)
            output = root / "pack"
            manifest = convert(sheet, output, "test-pet", {"author": "test"})
            self.assertEqual((manifest["width"], manifest["height"]), (96, 104))
            self.assertEqual(manifest["states"], STATE_CLIPS)
            self.assertEqual(sum(value["frames"] for value in manifest["animations"].values()), 41)
            for name, (_, count) in CLIPS.items():
                animation = manifest["animations"][name]
                data = (output / animation["file"]).read_bytes()
                self.assertEqual(len(data), count * 96 * 104 * 2)
                self.assertEqual(data[:2], b"\xf8\x00")
                self.assertEqual(hashlib.sha256(data).hexdigest(), animation["sha256"])
            self.assertEqual(json.loads((output / "manifest.json").read_text())["id"], "test-pet")
            with self.assertRaises(ValueError):
                convert(sheet, output, "test-pet", {})

    def test_invalid_ids_cannot_escape_pack_directory(self):
        for value in ("../escape", "a/b", "", "UPPER", "a\\b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                valid_id(value)
