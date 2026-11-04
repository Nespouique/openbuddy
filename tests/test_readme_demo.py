"""Validate the README renderer with synthetic pixels, never the private pet pack."""

import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from c6lcd.buddy_protocol import STATES
from tools.build_readme_demo import (Display, SCENES, SIZE, Scene, decode_frame,
                                    generate, load_font, rgb565)


class DemoTests(unittest.TestCase):
    def test_font_and_rgb565_decode_match_the_display_format(self):
        font = load_font()
        self.assertEqual(len(font), 96 * 8)
        image = decode_frame(b"\xf8\x00\x07\xe0\x00\x1f", 3, 1)
        self.assertEqual([image.getpixel((x, 0)) for x in range(3)],
                         [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
        with self.assertRaises(ValueError):
            decode_frame(b"\x00", 1, 1)

    def test_bitmap_text_is_scaled_without_smoothing(self):
        display = Display(load_font())
        display.text("A", 0, 0, rgb565(255, 255, 255), 0, scale=2)
        self.assertEqual(display.image.getpixel((2, 4)), (255, 255, 255))
        self.assertEqual(display.image.getpixel((3, 5)), (255, 255, 255))
        self.assertEqual(display.image.getpixel((0, 0)), (0, 0, 0))

    def test_storyboard_covers_all_states_and_both_transports(self):
        self.assertEqual({scene.state for scene in SCENES}, set(STATES) | {"offline"})
        self.assertEqual({scene.mode for scene in SCENES}, {"usb", "ble"})
        self.assertTrue(all(scene.duration % 100 == 0 for scene in SCENES))

    def test_missing_pack_is_not_silently_replaced_by_a_fake_pet(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "Local pet pack missing"):
                generate(root / "missing", root / "demo.gif", root / "poster.png")

    def test_gif_generation_uses_the_firmware_with_an_artwork_free_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pet = root / "test-pet"
            pet.mkdir()
            (pet / "idle.rgb565").write_bytes(b"\xf8\x00" * 4 + b"\x07\xe0" * 4)
            manifest = {
                "version": 1, "id": "test-pet", "format": "RGB565-BE",
                "width": 2, "height": 2, "background": [8, 12, 20],
                "states": {state: "idle" for state in STATES + ("offline", "pairing")},
                "animations": {"idle": {"file": "idle.rgb565", "frames": 2,
                                        "frame_ms": 100, "bytes": 16}},
            }
            (pet / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            scenes = (Scene("idle", "Ready", "A test", "idle", "OpenCode ready", "usb", 300),
                      Scene("question", "Question", "A test", "form.created", "Waiting", "ble", 300))
            gif, poster = root / "demo.gif", root / "poster.png"
            generate(pet, gif, poster, scenes=scenes)
            with Image.open(gif) as animation:
                self.assertEqual(animation.size, SIZE)
                self.assertGreater(animation.n_frames, 1)
                self.assertEqual(animation.info["loop"], 0)
                duration = 0
                for index in range(animation.n_frames):
                    animation.seek(index)
                    duration += animation.info["duration"]
                self.assertEqual(duration, 600)
            with Image.open(poster) as image:
                self.assertEqual(image.size, SIZE)
            self.assertNotIn(str(root).encode(), gif.read_bytes())
