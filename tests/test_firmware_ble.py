"""Exercise firmware security and HID gating without a Bluetooth adapter."""

import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "c6lcd"))


class FakeUUID:
    def __init__(self, value):
        self.value = value

    def __bytes__(self):
        import uuid
        return uuid.UUID(self.value).bytes[::-1]


class FakeBLE:
    def __init__(self):
        self.values = {}
        self.notifications = []
        self.disconnects = []
        self.passkeys = []
        self.append = set()
        self.advertisements = 0

    def active(self, value):
        pass

    def config(self, **kwargs):
        self.settings = kwargs

    def irq(self, callback):
        self.callback = callback

    def gatts_register_services(self, services):
        result = []
        handle = 1
        for _, characteristics in services:
            handles = []
            for characteristic in characteristics:
                handles.append(handle)
                handle += 1
                for _ in characteristic[2] if len(characteristic) > 2 else ():
                    handles.append(handle)
                    handle += 1
            result.append(tuple(handles))
        return tuple(result)

    def gatts_write(self, handle, value):
        self.values[handle] = value

    def gatts_read(self, handle):
        value = self.values.get(handle, b"")
        if handle in self.append:
            self.values[handle] = b""
        return value

    def gatts_set_buffer(self, handle, size, append=False):
        if append:
            self.append.add(handle)

    def gatts_notify(self, conn, handle, data):
        self.notifications.append((conn, handle, data))

    def gap_advertise(self, interval, payload, resp_data):
        self.advertisements += 1
        if len(payload) > 31 or len(resp_data) > 31:
            raise ValueError("Advertising payload too large")

    def gap_disconnect(self, conn):
        self.disconnects.append(conn)

    def gap_passkey(self, *args):
        self.passkeys.append(args)


fake_bluetooth = types.SimpleNamespace(UUID=FakeUUID, BLE=FakeBLE)
for index, name in enumerate(("READ", "WRITE", "WRITE_NO_RESPONSE", "NOTIFY",
                              "READ_ENCRYPTED", "READ_AUTHENTICATED",
                              "WRITE_ENCRYPTED", "WRITE_AUTHENTICATED")):
    setattr(fake_bluetooth, "FLAG_" + name, 1 << index)
with patch.dict(sys.modules, {"bluetooth": fake_bluetooth}):
    firmware = importlib.import_module("buddy_ble")


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1000
        output = patch("builtins.print")
        output.start()
        self.addCleanup(output.stop)
        for name, value in (
            ("ticks_ms", lambda: self.now),
            ("ticks_diff", lambda a, b: (a - b + (1 << 29)) % (1 << 30) - (1 << 29)),
            ("ticks_add", lambda a, b: (a + b) % (1 << 30)),
        ):
            active = patch.object(firmware.time, name, value, create=True)
            active.start()
            self.addCleanup(active.stop)
        sync = patch.object(firmware.os, "sync", lambda: None, create=True)
        sync.start()
        self.addCleanup(sync.stop)
        self.store = firmware.Bonds(str(Path(self.directory.name) / "bonds.json"))
        store_patch = patch.object(firmware, "Bonds", return_value=self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        self.buddy = firmware.BuddyBLE()

    def connect(self):
        self.buddy.button(long_press=True)
        self.buddy._irq(1, (4, 0, bytes(6)))
        self.buddy._irq(28, (4, True, True, True, 16))

    def command(self, command, **fields):
        message = dict(fields, v=1, id=1, cmd=command)
        self.buddy._command(message, self.now)

    def test_first_connection_requires_physical_pairing_window(self):
        self.buddy._irq(1, (4, 0, bytes(6)))
        self.assertEqual(self.buddy.ble.disconnects, [4])
        self.assertIsNone(self.buddy.conn)

    def test_rejected_connection_restarts_advertising_after_disconnect(self):
        count = self.buddy.ble.advertisements
        self.buddy._irq(1, (4, 0, bytes(6)))
        self.buddy._irq(2, (4, 0, bytes(6)))
        self.buddy.tick(self.now)
        self.assertEqual(self.buddy.ble.advertisements, count + 1)

    def test_disconnect_is_idempotent_while_waiting_for_callback(self):
        self.connect()
        self.buddy._disconnect(4)
        self.buddy._disconnect(4)
        self.assertEqual(self.buddy.ble.disconnects, [4])

    def test_disconnect_already_in_progress_does_not_crash(self):
        self.connect()
        with patch.object(self.buddy.ble, "gap_disconnect", side_effect=OSError(firmware.errno.EALREADY)):
            self.buddy._disconnect(4)

    def test_pairing_requires_numeric_confirmation(self):
        self.buddy.button(long_press=True)
        self.buddy._irq(1, (4, 0, bytes(6)))
        self.buddy._irq(31, (4, 4, 123456))
        self.assertEqual(self.buddy.ble.passkeys, [])
        self.buddy.button()
        self.assertEqual(self.buddy.ble.passkeys, [(4, 4, 1)])
        self.assertEqual(self.buddy.ble.notifications, [])

    def test_insecure_pairing_is_rejected(self):
        self.buddy.button(long_press=True)
        self.buddy._irq(1, (4, 0, bytes(6)))
        self.buddy._irq(28, (4, True, False, True, 16))
        self.assertFalse(self.buddy.secure)
        self.assertIn(4, self.buddy.ble.disconnects)

    def test_only_armed_physical_press_sends_one_key_and_release(self):
        self.connect()
        self.command("ping")
        self.buddy.button()
        self.assertEqual(self.buddy.ble.notifications, [])
        self.command("hid_arm")
        self.assertEqual(self.buddy.ble.notifications, [])
        self.buddy.button()
        self.assertEqual(self.buddy.ble.notifications[-1][2], b"\0\0\x68\0\0\0\0\0")
        self.now += 70
        self.buddy.tick(self.now)
        reports = [data for _, handle, data in self.buddy.ble.notifications
                   if handle == self.buddy.input_report]
        self.assertEqual(reports, [b"\0\0\x68\0\0\0\0\0", bytes(8)])
        self.buddy.button()
        self.assertIsNone(self.buddy.hid_until)

    def test_bridge_lease_expires_without_ping_even_if_ble_stays_connected(self):
        self.connect()
        self.command("state", state="working")
        self.assertTrue(self.buddy.online(self.now))
        self.now += 15001
        self.assertFalse(self.buddy.online(self.now))
        self.assertIsNotNone(self.buddy.conn)

    def test_asset_status_stays_within_protocol_frame_limit(self):
        self.connect()
        self.buddy.state = "permission"
        self.buddy.asset_status = lambda: {"sd": True, "pet": "a" * 64}
        self.buddy._command({"v": 1, "id": 2147483647, "cmd": "status"}, self.now)
        self.assertLessEqual(len(self.buddy.outgoing[-1]), 257)

    def test_reconnect_clears_hid_arming_and_stale_frames(self):
        self.connect()
        self.command("hid_arm")
        self.buddy._irq(2, (4, 0, bytes(6)))
        self.assertIsNone(self.buddy.hid_until)
        self.assertEqual(self.buddy.outgoing, [])
        self.assertEqual(self.buddy.ble.notifications, [])

    def test_bond_keys_cannot_be_added_outside_pairing_window(self):
        self.assertFalse(self.buddy._irq(30, (1, b"key", b"value")))
        self.buddy.button(long_press=True)
        self.assertTrue(self.buddy._irq(30, (1, b"key", b"value")))
        self.assertEqual(self.buddy._irq(29, (1, 0, b"key")), b"value")

    def test_bonds_persist_and_replace_existing_file(self):
        self.store.set(1, b"key", b"first", True)
        self.store.save()
        self.store.set(1, b"key", b"second", True)
        # Windows os.rename does not replace; MicroPython's littlefs rename does.
        with patch.object(firmware.os, "rename", firmware.os.replace):
            self.store.save()
        loaded = type(self.store)(self.store.path)
        self.assertEqual(loaded.get(1, 0, b"key"), b"second")

    def test_key_arriving_during_save_is_not_lost(self):
        self.store.set(1, b"first", b"one", True)
        replace = firmware.os.replace

        def finish_save(source, target):
            self.store.set(1, b"second", b"two", True)
            replace(source, target)

        with patch.object(firmware.os, "rename", finish_save):
            self.store.save()
        self.assertTrue(self.store.dirty)
        with patch.object(firmware.os, "rename", replace):
            self.store.save()
        loaded = type(self.store)(self.store.path)
        self.assertEqual(loaded.get(1, 0, b"second"), b"two")


if __name__ == "__main__":
    unittest.main()
