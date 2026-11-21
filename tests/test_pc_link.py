import asyncio
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from c6lcd.buddy_protocol import CHUNK_SIZE, Decoder, encode
from pc.buddy import Link

ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    def __init__(self):
        self.link = None
        self.decoder = Decoder()
        self.chunks = []

    async def write_gatt_char(self, uuid, chunk, response):
        self.chunks.append(chunk)
        for request in self.decoder.feed(chunk):
            reply = encode({"v": 1, "id": request["id"], "event": "ack", "state": "idle"})
            for offset in range(0, len(reply), 7):
                self.link.notification(None, reply[offset:offset + 7])


class LinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip_fragments_both_directions(self):
        client = FakeClient()
        link = Link(client)
        client.link = link
        result = await link.request("state", state="thinking", label="Long enough to fragment")
        self.assertEqual(result["event"], "ack")
        self.assertTrue(all(len(chunk) <= CHUNK_SIZE for chunk in client.chunks))
        self.assertEqual(link.pending, {})

    async def test_disconnect_rejects_pending_requests(self):
        link = Link(FakeClient())
        future = asyncio.get_running_loop().create_future()
        link.pending[1] = future
        link.lost(None)
        with self.assertRaises(ConnectionError):
            await future
        self.assertTrue(link.disconnected.is_set())

    async def test_button_notification_is_not_an_ack(self):
        events = []
        link = Link(FakeClient(), events.append)
        link.notification(None, encode({"v": 1, "event": "button", "press": "short"}))
        self.assertEqual(events[0]["event"], "button")

    def test_no_bleak_dependency_in_code_or_requirements(self):
        for rel in ("pc/requirements.txt", "distribution/workplace/requirements-usb.txt"):
            content = (ROOT / rel).read_text(encoding="utf-8").lower()
            self.assertNotIn("bleak", content, rel)

        # Ensure pc.buddy and pc.opencode reload cleanly even when bleak cannot be imported.
        with patch.dict(sys.modules, {"bleak": None, "bleak.exc": None, "bleak.backends.device": None}):
            import pc.buddy
            import pc.opencode
            importlib.reload(pc.buddy)
            importlib.reload(pc.opencode)


if __name__ == "__main__":
    unittest.main()
