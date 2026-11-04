import asyncio
import queue
import sys
import threading
import types
import unittest
from unittest.mock import patch

from c6lcd.buddy_protocol import Decoder, encode
from pc.buddy import SERIAL_VID, UsbLink, find_serial_port


class FakeSerial:
    """Behaves like a pyserial handle connected to a trivial echo firmware."""

    def __init__(self):
        self.decoder = Decoder()
        self.inbox = queue.Queue()
        self.writes = []
        self.closed = False
        self.fail_read = False

    @property
    def in_waiting(self):
        return self.inbox.qsize() and len(self.inbox.queue[0])

    def read(self, size):
        if self.fail_read:
            raise OSError(5, "device disconnected")
        try:
            return self.inbox.get(timeout=0.02)
        except queue.Empty:
            if self.closed:
                raise OSError("closed")
            return b""

    def write(self, data):
        self.writes.append(data)
        for request in self.decoder.feed(data):
            self.inbox.put(b"BLE: boot noise\r\n")
            self.inbox.put(encode({"v": 1, "id": request["id"], "event": "ack", "state": "idle"}))
        return len(data)

    def close(self):
        self.closed = True


def fake_serial_modules(ports):
    tools = types.ModuleType("serial.tools")
    list_ports = types.ModuleType("serial.tools.list_ports")
    list_ports.comports = lambda: ports
    tools.list_ports = list_ports
    package = types.ModuleType("serial")
    package.tools = tools
    return {"serial": package, "serial.tools": tools, "serial.tools.list_ports": list_ports}


def port(device, vid):
    return types.SimpleNamespace(device=device, vid=vid, description="USB serial")


class UsbLinkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handle = FakeSerial()
        with patch("pc.buddy.open_serial", return_value=self.handle):
            self.events = []
            self.link = await UsbLink.open("COM9", self.events.append)
        self.addAsyncCleanup(asyncio.to_thread, self.link.close)

    async def test_request_round_trip_ignores_console_noise(self):
        result = await self.link.request("state", state="thinking", label="Thinking about the task")
        self.assertEqual(result["event"], "ack")
        self.assertEqual(len(self.handle.writes), 1)
        self.assertEqual(self.link.pending, {})

    async def test_unsolicited_event_reaches_the_callback(self):
        self.handle.inbox.put(encode({"v": 1, "event": "button", "press": "short"}))
        for _ in range(50):
            if self.events:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(self.events[0]["event"], "button")

    async def test_unplugging_the_board_rejects_pending_requests(self):
        future = asyncio.get_running_loop().create_future()
        self.link.pending[1] = future
        self.handle.fail_read = True
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(future, 2)
        self.assertTrue(self.link.disconnected.is_set())

    async def test_write_failure_is_a_connection_error(self):
        def broken(_data):
            raise OSError(5, "write failed")

        self.handle.write = broken
        with self.assertRaises(ConnectionError):
            await self.link.request("ping")
        self.assertTrue(self.link.disconnected.is_set())

    async def test_partial_write_is_rejected_without_waiting_for_an_ack(self):
        self.handle.write = lambda data: len(data) - 1
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(self.link.request("ping"), 1)
        self.assertTrue(self.link.disconnected.is_set())
        self.assertEqual(self.link.pending, {})

    async def test_close_does_not_report_a_lost_connection(self):
        await asyncio.to_thread(self.link.close)
        await asyncio.sleep(0.1)
        self.assertFalse(self.link.disconnected.is_set())

    async def test_cancelled_open_closes_the_late_serial_handle(self):
        entered, release = threading.Event(), threading.Event()
        handle = FakeSerial()

        def delayed_open(_port):
            entered.set()
            release.wait(2)
            return handle

        with patch("pc.buddy.open_serial", side_effect=delayed_open):
            opening = asyncio.create_task(UsbLink.open("COM9"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                opening.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await opening
            finally:
                release.set()
            for _ in range(100):
                if handle.closed:
                    break
                await asyncio.sleep(0.01)
        self.assertTrue(handle.closed)

    async def test_reader_start_failure_closes_the_serial_handle(self):
        handle = FakeSerial()
        with patch("pc.buddy.open_serial", return_value=handle), \
                patch("pc.buddy.threading.Thread.start", side_effect=RuntimeError("Cannot start thread")):
            with self.assertRaises(RuntimeError):
                await UsbLink.open("COM9")
        self.assertTrue(handle.closed)


class SerialConfigurationTests(unittest.TestCase):
    def test_control_lines_are_set_before_opening_the_port(self):
        from pc.buddy import open_serial

        observed = []
        handle = types.SimpleNamespace()
        handle.open = lambda: observed.append((handle.port, handle.dtr, handle.rts))
        with patch("serial.Serial", return_value=handle):
            self.assertIs(open_serial("COM7"), handle)
        self.assertEqual(observed, [("COM7", False, False)])
        self.assertEqual((handle.timeout, handle.write_timeout), (0.2, 3))


class PortSelectionTests(unittest.TestCase):
    def test_explicit_port_is_used_as_is(self):
        self.assertEqual(find_serial_port("COM7"), "COM7")

    def test_auto_selects_the_single_espressif_port(self):
        ports = [port("COM3", 0x8086), port("COM6", SERIAL_VID)]
        with patch.dict(sys.modules, fake_serial_modules(ports)):
            self.assertEqual(find_serial_port("auto"), "COM6")
            self.assertEqual(find_serial_port(None), "COM6")

    def test_auto_refuses_zero_or_several_ports(self):
        with patch.dict(sys.modules, fake_serial_modules([port("COM3", 0x8086)])):
            with self.assertRaises(ConnectionError):
                find_serial_port("auto")
        both = [port("COM6", SERIAL_VID), port("COM8", SERIAL_VID)]
        with patch.dict(sys.modules, fake_serial_modules(both)):
            with self.assertRaises(ConnectionError):
                find_serial_port("auto")


if __name__ == "__main__":
    unittest.main()
