"""BLE diagnostics. Run from the project root: python -m pc.buddy --help."""

import argparse
import asyncio
import json
import sys

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from c6lcd.buddy_protocol import (CHUNK_SIZE, DEVICE_NAME, RX_UUID, SERVICE_UUID,
                                  STATES, TX_UUID, VERSION, Decoder, encode,
                                  validate_command)


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
                future.set_exception(ConnectionError("BLE connection closed"))

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

    async def request(self, command, **fields):
        async with self.lock:
            self.sequence += 1
            message = dict(fields, v=VERSION, id=self.sequence, cmd=command)
            validate_command(message)
            frame = encode(message)
            future = asyncio.get_running_loop().create_future()
            self.pending[self.sequence] = future
            try:
                for offset in range(0, len(frame), CHUNK_SIZE):
                    await self.client.write_gatt_char(
                        RX_UUID, frame[offset:offset + CHUNK_SIZE], response=True
                    )
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


async def windows_known_devices():
    """Use paired devices even while Windows is restoring their HID connection."""
    if sys.platform != "win32":
        return []
    from winrt.windows.devices.bluetooth import BluetoothConnectionStatus, BluetoothLEDevice
    from winrt.windows.devices.enumeration import DeviceInformation

    selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
    result = []
    for info in await DeviceInformation.find_all_async_aqs_filter(selector):
        if info.name != DEVICE_NAME:
            continue
        native = await BluetoothLEDevice.from_id_async(info.id)
        if native is None:
            continue
        try:
            raw_address = "%012X" % native.bluetooth_address
            address = ":".join(raw_address[index:index + 2] for index in range(0, 12, 2))
            # Bleak 3.0.2's WinRT backend uses BLEDevice.address directly.
            connected = native.connection_status == BluetoothConnectionStatus.CONNECTED
            result.append(BLEDevice(address, native.name, {"connected": connected}))
        finally:
            native.close()
    return result


async def scan(seconds):
    matches = await windows_known_devices()
    for device in matches:
        status = "connected" if device.details["connected"] else "paired, currently disconnected"
        print("%s  %s  Windows: %s" % (device.address, device.name, status))
    devices = await BleakScanner.discover(timeout=seconds, return_adv=True)
    seen = {device.address for device in matches}
    for device, advertisement in devices.values():
        if (SERVICE_UUID in advertisement.service_uuids
                or advertisement.local_name == DEVICE_NAME or device.name == DEVICE_NAME):
            if device.address in seen:
                continue
            matches.append(device)
            seen.add(device.address)
            print("%s  %s  RSSI %s" % (
                device.address, advertisement.local_name or device.name, advertisement.rssi
            ))
    return matches


async def find_device(address, timeout):
    if address:
        if sys.platform == "win32":
            return BLEDevice(address, DEVICE_NAME, None)
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
        if device is None:
            raise ConnectionError("OpenBuddy address not found")
        return device
    devices = await windows_known_devices()
    if not devices:
        devices = await scan(timeout)
    if not devices:
        raise ConnectionError("OpenBuddy not found; check power and Bluetooth")
    if len(devices) != 1:
        raise ConnectionError("Several devices found; select one with --address")
    return devices[0]


async def activity(link, args):
    print(json.dumps(await link.request("status")))
    if args.command == "status":
        return
    if args.command == "demo":
        for state in STATES:
            print("State:", state)
            await link.request("state", state=state, label="BLE diagnostic")
            await asyncio.sleep(args.interval)
        await link.request("state", state="idle", label="Demo complete")
    elif args.command == "set":
        print(json.dumps(await link.request("state", state=args.state, label=args.label)))
        await asyncio.sleep(args.duration)
    elif args.command == "hid-test":
        await link.request("hid_arm")
        print("F13 armed for 15s. Focus a keyboard tester, then briefly press BOOT.")
        print("No key is sent without that physical press.")
        await asyncio.sleep(15)
    elif args.command == "watch":
        print("Listening to BOOT events. Ctrl+C to stop.")
        await asyncio.Future()


async def connected(args):
    device = await find_device(args.address, args.scan_timeout)
    link = None

    def disconnected(client):
        if link is not None:
            link.lost(client)

    # Only resolve our custom service. Windows owns HID and can deny app access to it.
    async with BleakClient(device, services=[SERVICE_UUID], pair=args.pair,
                           timeout=90, disconnected_callback=disconnected) as client:
        link = Link(client, lambda event: print("Event:", json.dumps(event)))
        await client.start_notify(TX_UUID, link.notification)
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


async def main_async(args):
    if args.command == "scan":
        if not await scan(args.scan_timeout):
            raise ConnectionError("No OpenBuddy found")
        return
    while True:
        try:
            await connected(args)
            return
        except (BleakError, ConnectionError, OSError, TimeoutError) as error:
            if args.command != "watch":
                raise
            print("Connection lost:", error, "- retrying in 3s", file=sys.stderr)
            await asyncio.sleep(3)


def parser():
    result = argparse.ArgumentParser(description="OpenBuddy BLE diagnostic bridge")
    result.add_argument("--address", help="Select a Bluetooth address")
    result.add_argument("--pair", action="store_true", help="Request OS pairing; open the BOOT pairing window first")
    result.add_argument("--scan-timeout", type=float, default=5)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="List advertising or Windows-paired OpenBuddy devices")
    commands.add_parser("status", help="Connect and read firmware status")
    commands.add_parser("watch", help="Listen to button events, with reconnection")
    demo = commands.add_parser("demo", help="Cycle through all display/halo states")
    demo.add_argument("--interval", type=float, default=3)
    state = commands.add_parser("set", help="Display a state while keeping the bridge alive")
    state.add_argument("state", choices=STATES)
    state.add_argument("--label", default="", help="At most 24 characters")
    state.add_argument("--duration", type=float, default=30)
    commands.add_parser("hid-test", help="Arm one physical BOOT press to send F13")
    return result


def main():
    args = parser().parse_args()
    if args.scan_timeout <= 0 or getattr(args, "interval", 1) <= 0 or getattr(args, "duration", 1) <= 0:
        parser().error("Timeouts and durations must be positive")
    if len(getattr(args, "label", "")) > 24:
        parser().error("Label must contain at most 24 characters")
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass
    except (BleakError, ConnectionError, OSError, TimeoutError, ValueError) as error:
        print("Error:", error, file=sys.stderr)
        print("Pair OpenBuddy in Windows Bluetooth settings with the BOOT window open.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
