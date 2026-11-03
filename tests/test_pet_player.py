import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "c6lcd"))
import pet_player
from c6lcd.buddy_protocol import STATES


class FakeLCD:
    def __init__(self):
        self.frames = []

    def blit(self, buffer, x, y, width, height):
        self.frames.append((bytes(buffer), x, y, width, height))


class PlayerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pack = self.root / "test-pet"
        self.pack.mkdir()
        self.selection = self.root / "active.json"
        self.selection.write_text('{"pet":"test-pet"}', encoding="utf-8")
        self.manifest = {
            "version": 1, "id": "test-pet", "format": "RGB565-BE",
            "width": 2, "height": 2, "background": [8, 12, 20],
            "states": {state: "idle" for state in STATES + ("offline", "pairing")},
            "animations": {"idle": {"file": "idle.rgb565", "frames": 2,
                                    "frame_ms": 260, "bytes": 16}},
        }
        self.save_manifest()
        (self.pack / "idle.rgb565").write_bytes(b"\xf8\x00" * 4 + b"\x00\x1f" * 4)
        self.lcd = FakeLCD()
        for name, function in (
            ("ticks_us", lambda: 0),
            ("ticks_add", lambda a, b: (a + b) % (1 << 30)),
            ("ticks_diff", lambda a, b: (a - b + (1 << 29)) % (1 << 30) - (1 << 29)),
        ):
            active = patch.object(pet_player.time, name, function, create=True)
            active.start()
            self.addCleanup(active.stop)
        quiet = patch("builtins.print")
        quiet.start()
        self.addCleanup(quiet.stop)

    def save_manifest(self):
        (self.pack / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def player(self):
        player = pet_player.PetPlayer(self.lcd, str(self.root), str(self.selection))
        self.addCleanup(player.close)
        return player

    def test_streams_and_loops_one_frame_at_a_time(self):
        player = self.player()
        self.assertTrue(player.available)
        self.assertEqual(len(player.buffer), 8)
        for now in (0, 260, 520):
            self.assertTrue(player.draw("idle", now))
        self.assertEqual([frame[0] for frame in self.lcd.frames],
                         [b"\xf8\x00" * 4, b"\x00\x1f" * 4, b"\xf8\x00" * 4])

    def test_force_redraw_reuses_cached_frame_without_advancing(self):
        player = self.player()
        player.draw("idle", 0)
        player.draw("thinking", 50, force=True)
        self.assertEqual(player.reads, 1)
        self.assertEqual(player.draws, 2)
        self.assertEqual(player.index, 1)

    def test_frames_are_not_read_before_deadline(self):
        player = self.player()
        player.draw("offline", 0)
        player.draw("offline", 200)
        self.assertEqual(player.reads, 1)

    def test_truncated_frame_disables_player_without_crashing(self):
        player = self.player()
        player.draw("idle", 0)
        player.close()
        player.handle = io.BytesIO(b"short")
        self.assertFalse(player.draw("idle", 260))
        self.assertFalse(player.available)
        self.assertIsNone(player.buffer)

    def test_unsafe_filename_and_oversize_dimensions_are_rejected(self):
        self.manifest["animations"]["idle"]["file"] = "../idle.rgb565"
        self.save_manifest()
        self.assertFalse(self.player().available)
        self.manifest["width"] = 320
        self.save_manifest()
        self.assertFalse(self.player().available)

    def test_incomplete_pack_and_missing_selection_fall_back(self):
        (self.pack / "idle.rgb565").write_bytes(b"short")
        self.assertFalse(self.player().available)
        self.selection.unlink()
        self.assertFalse(self.player().available)

    def test_deadlines_survive_ticks_wraparound(self):
        player = self.player()
        player.draw("idle", (1 << 30) - 100)
        player.draw("idle", 50)
        self.assertEqual(player.reads, 1)
        player.draw("idle", 170)
        self.assertEqual(player.reads, 2)
