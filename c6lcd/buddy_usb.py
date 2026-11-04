"""USB serial transport for the OpenBuddy JSON-line protocol, without Bluetooth."""

import sys
import time

from buddy_protocol import (FIRMWARE_VERSION, HEARTBEAT_MS, VERSION, Decoder,
                            encode, validate_command)

READ_LIMIT = 128


class Console:
    """Non-blocking access to the MicroPython USB serial console."""

    def __init__(self):
        import select

        self.poll = select.poll()
        self.readable = select.POLLIN
        self.poll.register(sys.stdin, select.POLLIN)

    def read(self):
        data = bytearray()
        while len(data) < READ_LIMIT:
            events = self.poll.poll(0)
            if not events or not events[0][1] & self.readable:
                break  # POLLHUP/POLLERR do not mean stdin is readable.
            byte = sys.stdin.buffer.read(1)
            if not byte:
                break
            data += byte
        return bytes(data)

    def write(self, data):
        sys.stdout.buffer.write(data)


class BuddyUSB:
    """Same state model as BuddyBLE, driven by a host bridge over USB serial."""

    kind = "usb"

    def __init__(self, asset_status=None, console=None):
        self.asset_status = asset_status
        self.console = console if console is not None else Console()
        self.decoder = Decoder()
        self.state = "idle"
        self.label = ""
        self.info = ""
        self.last_seen = None
        # Attributes read by BuddyUI; pairing and HID do not exist over USB.
        self.passkey = None
        self.confirm = None
        self.hid_until = None
        self.pair_until = None

    def pairing_open(self, now=None):
        return False

    def online(self, now):
        return (self.last_seen is not None
                and time.ticks_diff(now, self.last_seen) < HEARTBEAT_MS)

    def _send(self, message):
        message["v"] = VERSION
        try:
            self.console.write(encode(message))
        except OSError:
            pass  # The host closed the port; the lease will expire.

    def _command(self, message, now):
        request_id = message.get("id")
        if type(request_id) is not int:
            request_id = None
        try:
            command = validate_command(message)
            if command == "hid_arm":
                raise ValueError("Keyboard is not available over USB")
            if command == "state":
                self.state = message["state"]
                self.label = message.get("label", "")
                self.info = message.get("info", "")
            self.last_seen = now
            response = {"event": "ack", "id": request_id, "state": self.state}
            if command == "status":
                response.update({"secure": True, "hid": False, "transport": "usb",
                                 "orientation": "landscape", "firmware": FIRMWARE_VERSION})
                if self.asset_status is not None:
                    response.update(self.asset_status())
            self._send(response)
        except (ValueError, TypeError) as error:
            self._send({"event": "error", "id": request_id, "message": str(error)[:80]})

    def tick(self, now):
        try:
            chunk = self.console.read()
        except OSError:
            return
        if chunk:
            for message in self.decoder.feed(chunk):
                self._command(message, now)
