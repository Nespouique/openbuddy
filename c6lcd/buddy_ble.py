"""BLE HID keyboard plus an authenticated OpenBuddy GATT service."""

import binascii
import bluetooth
import errno
import json
import os
import struct
import time

from buddy_protocol import (CHUNK_SIZE, DEVICE_NAME, FIRMWARE_VERSION,
                            HEARTBEAT_MS, MAX_FRAME, RX_UUID, SERVICE_UUID,
                            TX_UUID, VERSION, Decoder, encode, validate_command)

PAIR_WINDOW_MS = 60_000
PAIR_CONFIRM_MS = 30_000
HID_ARM_MS = 15_000
BOND_FILE = "openbuddy-bonds.json"
# These documented flags are not exported by every MicroPython build.
READ_ENCRYPTED = 0x0200
READ_AUTHENTICATED = 0x0400
WRITE_ENCRYPTED = 0x1000
WRITE_AUTHENTICATED = 0x2000

# Standard boot-compatible keyboard, report ID 1, eight-byte input report.
REPORT_MAP = bytes((
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, 0x01,
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00,
    0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,
    0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01,
    0x29, 0x05, 0x91, 0x02, 0x95, 0x01, 0x75, 0x03, 0x91, 0x01,
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x73,
    0x05, 0x07, 0x19, 0x00, 0x29, 0x73, 0x81, 0x00, 0xC0,
))


class Bonds:
    """Persist bonding keys on internal flash, never on a removable SD card."""

    def __init__(self, path=BOND_FILE):
        self.path = path
        self.values = {}
        self.dirty = False
        self.revision = 0
        try:
            with open(path) as handle:
                rows = json.load(handle)
        except OSError as error:
            if error.args[0] != 2:
                raise
            rows = []
        for kind, key, value in rows:
            self.values[(kind, binascii.unhexlify(key))] = binascii.unhexlify(value)

    def get(self, kind, index, key):
        if key is not None:
            return self.values.get((kind, bytes(key)))
        values = [value for (stored_kind, _), value in self.values.items()
                  if stored_kind == kind]
        return values[index] if index < len(values) else None

    def set(self, kind, key, value, allow_new):
        entry = (kind, bytes(key))
        if not allow_new and entry not in self.values:
            return False
        if value is None:
            self.values.pop(entry, None)
        else:
            if entry not in self.values and len(self.values) >= 32:
                return False
            self.values[entry] = bytes(value)
        self.dirty = True
        self.revision += 1
        return True

    def save(self):
        if not self.dirty:
            return
        revision = self.revision
        snapshot = self.values.copy()
        rows = [[kind, binascii.hexlify(key).decode(), binascii.hexlify(value).decode()]
                for (kind, key), value in snapshot.items()]
        with open(self.path + ".tmp", "w") as handle:
            json.dump(rows, handle)
            handle.flush()
        os.sync()
        os.rename(self.path + ".tmp", self.path)
        self.dirty = self.revision != revision


class BuddyBLE:
    def __init__(self, asset_status=None):
        self.asset_status = asset_status
        self.bonds = Bonds()
        self.conn = None
        self.disconnecting = set()
        self.advertise_needed = False
        self.diagnostics = []
        self.secure = False
        self.connected_at = 0
        self.pair_until = None
        self.bond_write_until = None
        self.confirm = None
        self.passkey = None
        self.passkey_at = 0
        self.hid_until = None
        self.release_at = None
        self.last_seen = None
        self.state = "idle"
        self.label = ""
        self.info = ""
        self.protocol_mode = 1
        self.suspended = False
        self.decoder = Decoder()
        self.incoming = []
        self.outgoing = []
        self.out_offset = 0
        self.overflow = False
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        # Display + BOOT supports authenticated numeric comparison.
        try:
            self.ble.config(gap_name=DEVICE_NAME, bond=True, mitm=True,
                            le_secure=True, io=1)
            self.ble.irq(self._irq)
            self._register()
            self._advertise()
        except Exception:
            self.ble.active(False)
            raise

    def _register(self):
        uuid = bluetooth.UUID
        read = bluetooth.FLAG_READ
        write = bluetooth.FLAG_WRITE
        no_response = bluetooth.FLAG_WRITE_NO_RESPONSE
        notify = bluetooth.FLAG_NOTIFY
        secure_read = read | READ_ENCRYPTED | READ_AUTHENTICATED
        secure_write = write | WRITE_ENCRYPTED | WRITE_AUTHENTICATED
        report_ref = (uuid(0x2908), read)
        services = (
            (uuid(0x1812), (
                (uuid(0x2A4A), read),  # HID information
                (uuid(0x2A4B), read),  # Report map
                (uuid(0x2A4C), no_response | secure_write),
                (uuid(0x2A4E), secure_read | secure_write | no_response),
                (uuid(0x2A4D), secure_read | notify, (report_ref,)),
                (uuid(0x2A4D), secure_read | secure_write | no_response, (report_ref,)),
                (uuid(0x2A22), secure_read | notify),  # Boot keyboard input
                (uuid(0x2A32), secure_read | secure_write | no_response),
            )),
            (uuid(0x180A), (
                (uuid(0x2A29), read),
                (uuid(0x2A24), read),
                (uuid(0x2A50), read),
            )),
            (uuid(SERVICE_UUID), (
                (uuid(RX_UUID), secure_write),
                (uuid(TX_UUID), secure_read | notify),
            )),
        )
        hid, info, buddy = self.ble.gatts_register_services(services)
        (self.hid_info, self.report_map, self.control, self.mode,
         self.input_report, input_ref, self.output_report, output_ref,
         self.boot_input, self.boot_output) = hid
        self.rx, self.tx = buddy
        for handle, value in (
            (self.hid_info, b"\x11\x01\x00\x02"),
            (self.report_map, REPORT_MAP), (self.mode, b"\x01"),
            (self.input_report, bytes(8)), (input_ref, b"\x01\x01"),
            (self.output_report, b"\x00"), (output_ref, b"\x01\x02"),
            (self.boot_input, bytes(8)), (self.boot_output, b"\x00"),
            (info[0], b"OpenBuddy"), (info[1], b"C6 BLE prototype"),
            # Unassigned development identity, not a third-party vendor identity.
            (info[2], struct.pack("<BHHH", 2, 0xFFFF, 1, 1)),
        ):
            self.ble.gatts_write(handle, value)
        self.ble.gatts_set_buffer(self.rx, MAX_FRAME * 2, True)
        self.ble.gatts_set_buffer(self.tx, MAX_FRAME + 1)

    def _advertise(self):
        def field(kind, value):
            return bytes((len(value) + 1, kind)) + value

        payload = (field(0x01, b"\x06") + field(0x03, struct.pack("<H", 0x1812))
                   + field(0x07, bytes(bluetooth.UUID(SERVICE_UUID)))
                   + field(0x19, struct.pack("<H", 0x03C1)))
        try:
            self.ble.gap_advertise(100_000, payload,
                                   resp_data=field(0x09, DEVICE_NAME.encode()))
        except OSError as error:
            if error.args[0] != errno.EALREADY:
                raise
        self.advertise_needed = False

    def _trace(self, message):
        self.diagnostics.append(message)
        if len(self.diagnostics) > 16:
            self.diagnostics.pop(0)
        print("BLE:", message)

    def _disconnect(self, conn):
        if conn in self.disconnecting:
            return
        self.disconnecting.add(conn)
        self._trace("disconnect requested %d" % conn)
        try:
            self.ble.gap_disconnect(conn)
        except OSError as error:
            if error.args[0] not in (errno.EALREADY, errno.ENOTCONN):
                self.disconnecting.discard(conn)
                raise

    def pairing_open(self, now=None):
        if now is None:
            now = time.ticks_ms()
        return self.pair_until is not None and time.ticks_diff(self.pair_until, now) > 0

    def _irq(self, event, data):
        if event == 29:  # GET_SECRET must return synchronously.
            return self.bonds.get(*data)
        if event == 30:  # Defer flash writes to the main loop.
            finishing_pair = (self.bond_write_until is not None
                              and time.ticks_diff(self.bond_write_until, time.ticks_ms()) > 0)
            return self.bonds.set(*data, self.pairing_open() or finishing_pair)
        if event == 1:  # CENTRAL_CONNECT
            conn, _, _ = data
            self.disconnecting.discard(conn)
            self._trace("connected %d" % conn)
            if self.conn is not None or (not self.bonds.values and not self.pairing_open()):
                self._disconnect(conn)
                return
            self.conn = conn
            self.connected_at = time.ticks_ms()
            self.secure = False
            self.protocol_mode = 1
            self.suspended = False
        elif event == 2:  # CENTRAL_DISCONNECT, including a rejected connection.
            self.disconnecting.discard(data[0])
            self._trace("disconnected %d" % data[0])
            if data[0] != self.conn:
                if self.conn is None:
                    self.advertise_needed = True
                return
            self.conn = None
            self.secure = False
            self.bond_write_until = None
            self.confirm = None
            self.passkey = None
            self.hid_until = None
            self.release_at = None
            self.last_seen = None
            self.decoder = Decoder()
            self.incoming = []
            self.outgoing = []
            self.out_offset = 0
            self.advertise_needed = True
        elif event == 3 and data[0] == self.conn:  # GATTS_WRITE
            _, handle = data
            if handle == self.rx:
                chunk = self.ble.gatts_read(self.rx)
                if self.secure and len(self.incoming) < 16:
                    self.incoming.append(bytes(chunk))
                else:
                    self.overflow = True
            elif handle == self.mode:
                value = self.ble.gatts_read(handle)
                if value in (b"\x00", b"\x01"):
                    self.protocol_mode = value[0]
            elif handle == self.control:
                self.suspended = self.ble.gatts_read(handle) == b"\x00"
        elif event == 28 and data[0] == self.conn:  # ENCRYPTION_UPDATE
            _, encrypted, authenticated, bonded, key_size = data
            self._trace("security enc=%d auth=%d bond=%d size=%d" % (
                encrypted, authenticated, bonded, key_size))
            self.secure = bool(encrypted and authenticated and bonded and key_size >= 16)
            if self.secure:
                if self.pairing_open():
                    self.bond_write_until = time.ticks_add(time.ticks_ms(), 5000)
                self.confirm = None
                self.passkey = None
                self.pair_until = None
            else:
                self.hid_until = None
                self._disconnect(self.conn)
        elif event == 31 and data[0] == self.conn:  # PASSKEY_ACTION
            conn, action, passkey = data
            self._trace("pair action %d" % action)
            if not self.pairing_open():
                self._disconnect(conn)
                return
            self.passkey_at = time.ticks_ms()
            if action == 4:  # Numeric comparison: require an actual BOOT press.
                self.passkey = passkey
                self.confirm = (conn, action, passkey)
            elif action == 3:  # Display a generated passkey for entry on the PC.
                self.passkey = int.from_bytes(os.urandom(4), "little") % 1_000_000
                self.ble.gap_passkey(conn, action, self.passkey)
            else:
                self._disconnect(conn)

    def button(self, long_press=False):
        now = time.ticks_ms()
        if long_press:
            if self.confirm is not None:
                self.ble.gap_passkey(self.confirm[0], self.confirm[1], 0)
                self.confirm = None
            self.pair_until = time.ticks_add(now, PAIR_WINDOW_MS)
            return
        if self.confirm is not None:
            request = self.confirm
            self.confirm = None
            if time.ticks_diff(now, self.passkey_at) < PAIR_CONFIRM_MS:
                self._trace("physical confirmation")
                try:
                    self.ble.gap_passkey(request[0], request[1], 1)
                except OSError as error:
                    if error.args[0] not in (errno.EALREADY, errno.ENOTCONN):
                        raise
                    self._trace("confirmation no longer pending")
            return
        if self.passkey is not None:
            return
        if self.secure:
            self._send({"event": "button", "button": "boot", "press": "short"})
            if (self.hid_until is not None and time.ticks_diff(self.hid_until, now) > 0
                    and not self.suspended):
                self.hid_until = None
                # F13, only following remote arming AND a physical short press.
                self.release_at = time.ticks_add(now, 60)
                try:
                    self._key_report(b"\x00\x00\x68\x00\x00\x00\x00\x00")
                except OSError:
                    self._send({"event": "hid_error", "message": "HID report not accepted"})

    def _key_report(self, report):
        handle = self.input_report if self.protocol_mode else self.boot_input
        self.ble.gatts_notify(self.conn, handle, report)

    def _send(self, message):
        message["v"] = VERSION
        if len(self.outgoing) < 16:
            self.outgoing.append(encode(message))
        else:
            self.overflow = True

    def _command(self, message, now):
        request_id = message.get("id")
        if type(request_id) is not int:
            request_id = None
        try:
            command = validate_command(message)
            if command == "state":
                self.state = message["state"]
                self.label = message.get("label", "")
                self.info = message.get("info", "")
            elif command == "hid_arm":
                if self.suspended:
                    raise ValueError("Keyboard is suspended")
                self.hid_until = time.ticks_add(now, HID_ARM_MS)
            self.last_seen = now
            response = {"event": "ack", "id": request_id, "state": self.state}
            if command == "status":
                response.update({"secure": self.secure, "hid": True,
                                 "orientation": "landscape", "firmware": FIRMWARE_VERSION})
                if self.asset_status is not None:
                    response.update(self.asset_status())
            self._send(response)
        except (ValueError, TypeError) as error:
            self._send({"event": "error", "id": request_id, "message": str(error)[:80]})

    def online(self, now):
        return (self.secure and self.last_seen is not None
                and time.ticks_diff(now, self.last_seen) < HEARTBEAT_MS)

    def tick(self, now):
        self.bonds.save()
        if self.hid_until is not None and time.ticks_diff(now, self.hid_until) >= 0:
            self.hid_until = None
        if self.conn is None:
            if self.advertise_needed:
                self._advertise()
            return
        if self.overflow:
            self.overflow = False
            self._disconnect(self.conn)
            return
        if self.passkey is not None and time.ticks_diff(now, self.passkey_at) >= PAIR_CONFIRM_MS:
            self._disconnect(self.conn)
            return
        if not self.secure:
            if time.ticks_diff(now, self.connected_at) >= PAIR_WINDOW_MS:
                self._disconnect(self.conn)
            return
        if not self.online(now):
            self.hid_until = None
        if self.release_at is not None and time.ticks_diff(now, self.release_at) >= 0:
            try:
                self._key_report(bytes(8))
                self.release_at = None
            except OSError:
                return  # Retry releasing the key before any other notification.
        for _ in range(min(4, len(self.incoming))):
            for message in self.decoder.feed(self.incoming.pop(0)):
                self._command(message, now)
        if self.outgoing:
            frame = self.outgoing[0]
            chunk = frame[self.out_offset:self.out_offset + CHUNK_SIZE]
            try:
                self.ble.gatts_notify(self.conn, self.tx, chunk)
            except OSError:
                return  # Keep the chunk queued while NimBLE buffers are full.
            self.out_offset += len(chunk)
            if self.out_offset == len(frame):
                self.outgoing.pop(0)
                self.out_offset = 0

    def close(self):
        try:
            if self.secure and self.release_at is not None:
                self._key_report(bytes(8))
        finally:
            self.ble.active(False)
