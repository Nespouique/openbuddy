import unittest

from c6lcd.buddy_protocol import MAX_FRAME, Decoder, encode, validate_command


class ProtocolTests(unittest.TestCase):
    def test_fragmented_unicode_frame(self):
        original = {"v": 1, "id": 7, "cmd": "state", "state": "thinking", "label": "Prêt"}
        frame = encode(original)
        decoder = Decoder()
        messages = []
        for value in frame:
            messages.extend(decoder.feed(bytes((value,))))
        self.assertEqual(messages, [original])

    def test_multiple_frames(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed(b'{"a":1}\n{"b":2}\n'), [{"a": 1}, {"b": 2}])

    def test_invalid_and_oversize_frames_resynchronize(self):
        decoder = Decoder()
        payload = b"x" * (MAX_FRAME + 10) + b'\n[1]\n\xff\n{"ok":true}\n'
        self.assertEqual(decoder.feed(payload), [{"ok": True}])
        self.assertEqual(decoder.errors, 3)
        self.assertLessEqual(len(decoder.buffer), MAX_FRAME)

    def test_encode_is_bounded(self):
        with self.assertRaises(ValueError):
            encode({"large": "x" * MAX_FRAME})

    def test_commands_are_validated(self):
        self.assertEqual(validate_command({"v": 1, "id": 1, "cmd": "ping"}), "ping")
        for changes in ({"v": 2}, {"id": True}, {"id": -1}, {"cmd": "shell"},
                        {"cmd": "state", "state": "unknown"},
                        {"cmd": "state", "state": "idle", "label": "x" * 25}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_command(dict({"v": 1, "id": 1, "cmd": "ping"}, **changes))


if __name__ == "__main__":
    unittest.main()
