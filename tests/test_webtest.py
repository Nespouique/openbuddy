import json
from http.server import ThreadingHTTPServer
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pc.webtest import WebTestHandler


class FakeDevice:
    def __init__(self):
        self.commands = []

    def request(self, command, **fields):
        self.commands.append((command, fields))
        return {"event": "ack", "id": len(self.commands), "cmd": command, **fields}


class WebTestHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device = FakeDevice()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), WebTestHandler)
        cls.server.device = cls.device
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_page_and_status_use_expected_device(self):
        with urlopen(self.base + "/", timeout=2) as response:
            self.assertIn("OpenBuddy", response.read().decode("utf-8"))
        with urlopen(self.base + "/api/status", timeout=2) as response:
            self.assertEqual(json.loads(response.read()), {"event": "ack", "id": 1, "cmd": "status"})
        self.assertEqual(self.device.commands[-1], ("status", {}))

    def test_state_command_is_forwarded_and_invalid_state_rejected(self):
        body = json.dumps({"state": "thinking", "label": "Test"}).encode()
        request = Request(self.base + "/api/state", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=2) as response:
            self.assertEqual(json.loads(response.read())["event"], "ack")
        self.assertEqual(self.device.commands[-1],
                         ("state", {"state": "thinking", "label": "Test"}))

        body = b'{"state":"shell","label":"bad"}'
        request = Request(self.base + "/api/state", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=2)
        self.assertEqual(error.exception.code, 400)

    def test_cross_origin_post_is_rejected(self):
        request = Request(self.base + "/api/state", data=b'{"state":"idle"}',
                          headers={"Content-Type": "application/json",
                                   "Origin": "http://example.invalid"}, method="POST")
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=2)
        self.assertEqual(error.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
