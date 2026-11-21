"""USB serial diagnostics. Run from the project root: python -m pc.buddy --help."""

import argparse
import asyncio
import json
import sys
import threading

from c6lcd.buddy_protocol import (CHUNK_SIZE, RX_UUID, STATES, VERSION,
                                  Decoder, encode, validate_command)

SERIAL_VID = 0x303A  # Espressif USB-Serial-JTAG


class Link:
    def __init__(self, client, on_event=print):
        self.client = client
        self.on_event = on_event
        self.decoder = Decoder()
        self.pending = {}
        self.sequence = 0
        self.lock = asyncio.Lock()
        self.disconnected = asyncio.Event()

    def lost(self, _client):
        self.disconnected.set()
        for future in tuple(self.pending.values()):
            if not future.done():
                future.set_exception(ConnectionError("Device connection closed"))

    def notification(self, _characteristic, data):
        for message in self.decoder.feed(data):
            if message.get("v") != VERSION:
                continue
            request_id = message.get("id")
            if type(request_id) is not int:
                self.on_event(message)
                continue
            future = self.pending.get(request_id)
            if future is not None and not future.done():
                if message.get("event") == "error":
                    future.set_exception(ValueError(message.get("message", "Device rejected command")))
                elif message.get("event") == "ack":
                    future.set_result(message)

    async def write(self, frame):
        for offset in range(0, len(frame), CHUNK_SIZE):
            await self.client.write_gatt_char(
                RX_UUID, frame[offset:offset + CHUNK_SIZE], response=True
            )

    async def request(self, command, **fields):
        async with self.lock:
            self.sequence += 1
            message = dict(fields, v=VERSION, id=self.sequence, cmd=command)
            validate_command(message)
            frame = encode(message)
            future = asyncio.get_running_loop().create_future()
            self.pending[self.sequence] = future
            try:
                await self.write(frame)
                return await asyncio.wait_for(future, timeout=8)
            finally:
                self.pending.pop(self.sequence, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()  # Retrieve an error if writing also failed.

    async def heartbeat(self):
        while True:
            await asyncio.sleep(4)
            await self.request("ping")


def find_serial_port(port):
    """Return the requested COM port, or the single Espressif USB serial port."""
    if port and port.lower() != "auto":
        return port
    from serial.tools import list_ports

    matches = [item.device for item in list_ports.comports() if item.vid == SERIAL_VID]
    if not matches:
        raise ConnectionError("OpenBuddy USB serial port not found; check the cable")
    if len(matches) != 1:
        raise ConnectionError("Several USB serial ports found; select one with --serial COMx")
    return matches[0]


def open_serial(port):
    import serial

    handle = serial.Serial()
    handle.port = port
    handle.baudrate = 115200
    handle.timeout = 0.2
    handle.write_timeout = 3
    # Keep DTR/RTS low: toggling them can reset the ESP32-C6 USB-Serial-JTAG.
    handle.dtr = False
    handle.rts = False
    handle.open()
    return handle


class UsbLink(Link):
    """Same protocol and requests as Link, over the board's USB serial port."""

    def __init__(self, handle, on_event=print):
        super().__init__(handle, on_event)
        self.loop = asyncio.get_running_loop()
        self.closing = False
        self.thread = threading.Thread(target=self.read_loop, name="openbuddy-usb", daemon=True)

    @classmethod
    async def open(cls, port, on_event=print):
        opening = asyncio.create_task(asyncio.to_thread(open_serial, port))
        try:
            handle = await asyncio.shield(opening)
        except asyncio.CancelledError:
            # Cancelling to_thread cannot stop the OS open; close its late result.
            def close_unclaimed(result):
                try:
                    result.result().close()
                except (asyncio.CancelledError, OSError, ValueError, TypeError):
                    pass

            opening.add_done_callback(close_unclaimed)
            raise
        try:
            link = cls(handle, on_event)
            link.thread.start()
        except Exception:
            handle.close()
            raise
        return link

    def deliver(self, call, *arguments):
        try:
            self.loop.call_soon_threadsafe(call, *arguments)
        except RuntimeError:
            self.closing = True  # The event loop is already closed.

    def read_loop(self):
        try:
            while not self.closing:
                data = self.client.read(min(self.client.in_waiting or 1, 4096))
                if data:
                    self.deliver(self.notification, None, data)
        except (OSError, ValueError, TypeError):
            pass
        if not self.closing:
            self.deliver(self.lost, None)

    async def write(self, frame):
        try:
            written = await asyncio.to_thread(self.client.write, frame)
            if written != len(frame):
                raise ConnectionError("Incomplete USB serial write")
        except (OSError, ValueError, TypeError) as error:
            self.lost(None)
            raise ConnectionError("USB serial connection closed") from error

    def close(self):
        self.closing = True
        try:
            self.client.close()
        except OSError:
            pass
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(1)


async def activity(link, args):
    print(json.dumps(await link.request("status")))
    if args.command == "status":
        return
    if args.command == "demo":
        for state in STATES:
            print("State:", state)
            await link.request("state", state=state, label="USB diagnostic")
            await asyncio.sleep(args.interval)
        await link.request("state", state="idle", label="Demo complete")
    elif args.command == "set":
        print(json.dumps(await link.request("state", state=args.state, label=args.label)))
        await asyncio.sleep(args.duration)
    elif args.command == "watch":
        print("Listening to device events. Ctrl+C to stop.")
        await asyncio.Future()


async def supervise(link, args):
    job = asyncio.create_task(activity(link, args))
    heartbeat = asyncio.create_task(link.heartbeat())
    lost = asyncio.create_task(link.disconnected.wait())
    tasks = (job, heartbeat, lost)
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if lost in done:
            raise ConnectionError("OpenBuddy disconnected")
        if heartbeat in done:
            await heartbeat
        await job
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def usb_connected(args):
    port = await asyncio.to_thread(find_serial_port, args.serial)
    link = await UsbLink.open(port, lambda event: print("Event:", json.dumps(event)))
    try:
        await supervise(link, args)
    finally:
        link.close()


async def main_async(args):
    if args.command == "scan":
        from serial.tools import list_ports

        ports = [item for item in list_ports.comports() if item.vid == SERIAL_VID]
        for item in ports:
            print("%s  %s" % (item.device, item.description))
        if not ports:
            raise ConnectionError("No OpenBuddy USB serial port found")
        return
    while True:
        try:
            await usb_connected(args)
            return
        except (ConnectionError, OSError, TimeoutError) as error:
            if args.command != "watch":
                raise
            print("Connection lost:", error, "- retrying in 3s", file=sys.stderr)
            await asyncio.sleep(3)


def parser():
    result = argparse.ArgumentParser(description="OpenBuddy USB serial diagnostic bridge")
    result.add_argument("--serial", metavar="PORT", default="auto",
                        help="USB serial port (COMx, or auto; default: auto)")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="List detected Espressif USB serial ports")
    commands.add_parser("status", help="Connect and read firmware status")
    commands.add_parser("watch", help="Listen to device events, with reconnection")
    demo = commands.add_parser("demo", help="Cycle through all display/halo states")
    demo.add_argument("--interval", type=float, default=3)
    state = commands.add_parser("set", help="Display a state while keeping the bridge alive")
    state.add_argument("state", choices=STATES)
    state.add_argument("--label", default="", help="At most 24 characters")
    state.add_argument("--duration", type=float, default=30)
    return result


def main():
    args = parser().parse_args()
    if getattr(args, "interval", 1) <= 0 or getattr(args, "duration", 1) <= 0:
        parser().error("Durations must be positive")
    if len(getattr(args, "label", "")) > 24:
        parser().error("Label must contain at most 24 characters")
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass
    except (ConnectionError, OSError, TimeoutError, ValueError) as error:
        print("Error:", error, file=sys.stderr)
        print("Check the USB cable, the COM port (--serial COMx) and that no other "
              "program (bridge, mpremote) is using it.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
