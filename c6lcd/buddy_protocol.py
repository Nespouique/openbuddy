"""Small, versioned JSON-line protocol shared by MicroPython and CPython."""

import json

VERSION = 1
FIRMWARE_VERSION = "0.4.0"
DEVICE_NAME = "OpenBuddy"
SERVICE_UUID = "c8ad0001-4f90-4ab8-9976-22401b7861b0"
RX_UUID = "c8ad0002-4f90-4ab8-9976-22401b7861b0"
TX_UUID = "c8ad0003-4f90-4ab8-9976-22401b7861b0"
MAX_FRAME = 256
CHUNK_SIZE = 20  # Works even with the default ATT MTU of 23.
HEARTBEAT_MS = 15_000
STATES = ("idle", "thinking", "working", "permission", "question",
          "success", "error", "retry")


def encode(message):
    data = json.dumps(message).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ValueError("Frame exceeds 256 bytes")
    return data + b"\n"


def validate_command(message):
    if message.get("v") != VERSION:
        raise ValueError("Unsupported protocol version")
    request_id = message.get("id")
    if type(request_id) is not int or not 0 <= request_id <= 2_147_483_647:
        raise ValueError("Invalid request id")
    command = message.get("cmd")
    if command not in ("ping", "status", "state", "hid_arm"):
        raise ValueError("Unknown command")
    if command == "state":
        if message.get("state") not in STATES:
            raise ValueError("Unknown state")
        label = message.get("label", "")
        if not isinstance(label, str) or len(label) > 24:
            raise ValueError("Label must contain at most 24 characters")
        info = message.get("info", "")
        if not isinstance(info, str) or len(info) > 24:
            raise ValueError("Info must contain at most 24 characters")
    return command


class Decoder:
    """Reassemble ATT chunks, with bounded memory and newline resynchronization."""

    def __init__(self):
        self.buffer = bytearray()
        self.dropping = False
        self.errors = 0

    def feed(self, chunk):
        messages = []
        for value in chunk:
            if value == 10:
                if self.buffer and not self.dropping:
                    try:
                        message = json.loads(self.buffer.decode("utf-8"))
                        if not isinstance(message, dict):
                            raise ValueError("Expected an object")
                        messages.append(message)
                    except (ValueError, UnicodeError):
                        self.errors += 1
                self.buffer = bytearray()
                self.dropping = False
            elif not self.dropping:
                if len(self.buffer) >= MAX_FRAME:
                    self.buffer = bytearray()
                    self.dropping = True
                    self.errors += 1
                else:
                    self.buffer.append(value)
        return messages
