"""Bounded, read-only SD-over-SPI block device for a shared LCD bus.

Adapted from micropython-lib's SDCard SPI driver (sdcard.py 0.2.1),
upstream blob 8f43725860572510b27be2a66aa30da526188410.
Changes: read-only API, bounded waits, explicit shared-bus configuration,
masked command bytes and CSD capacity bits. See THIRD_PARTY.md for the MIT notice.
"""

import time


def crc7(data):
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ (0x12 * (crc >> 7))) & 255
    return crc


class ReadOnlySDCard:
    def __init__(self, bus, baudrate):
        self.bus = bus
        self.spi = bus.spi
        self.cs = bus.sd_cs
        self.baudrate = baudrate
        self.command_buffer = bytearray(6)
        self.command_view = memoryview(self.command_buffer)[:5]
        self.token = bytearray(1)
        self.tail = bytearray(4)
        self.checksum = bytearray(2)
        self.bus.configure(100_000)
        try:
            self.spi.write(b"\xff" * 16)
            for _ in range(5):
                if self.command(0, 0) == 1:
                    break
                time.sleep_ms(10)
            else:
                raise OSError("SD card not detected")
            response = self.command(8, 0x1AA, final=4)
            if response == 1:
                if self.tail[2:4] != b"\x01\xaa":
                    raise OSError("Unsupported SD voltage")
                version2 = True
            elif response == 5:
                version2 = False
            else:
                raise OSError("Unknown SD version")
            started = time.ticks_ms()
            while True:
                self.command(55, 0)
                if self.command(41, 0x40000000 if version2 else 0) == 0:
                    break
                if time.ticks_diff(time.ticks_ms(), started) >= 3000:
                    raise OSError("SD initialization timeout")
                time.sleep_ms(10)
            self.address_multiplier = 512
            if version2:
                if self.command(58, 0, final=4) != 0:
                    raise OSError("SD OCR read failed")
                if self.tail[0] & 0x40:
                    self.address_multiplier = 1
            if self.command(9, 0, release=False) != 0:
                raise OSError("SD CSD read failed")
            csd = bytearray(16)
            self.read_data(csd)
            self.end()
            if csd[0] & 0xC0 == 0x40:
                size = ((csd[7] & 0x3F) << 16) | (csd[8] << 8) | csd[9]
                self.sectors = (size + 1) * 1024
            elif csd[0] & 0xC0 == 0:
                size = ((csd[6] & 3) << 10) | (csd[7] << 2) | (csd[8] >> 6)
                multiplier = ((csd[9] & 3) << 1) | (csd[10] >> 7)
                self.sectors = ((size + 1) * (1 << (multiplier + 2)) * (1 << (csd[5] & 15))) // 512
            else:
                raise OSError("Unsupported SD CSD")
            if self.command(16, 512) != 0:
                raise OSError("SD block size rejected")
        finally:
            self.end()
        self.bus.configure(baudrate)

    def end(self):
        self.cs(1)
        self.spi.write(b"\xff")

    def command(self, command, argument, final=0, release=True, skip_first=False):
        self.cs(0)
        buffer = self.command_buffer
        buffer[0] = 0x40 | command
        buffer[1] = (argument >> 24) & 255
        buffer[2] = (argument >> 16) & 255
        buffer[3] = (argument >> 8) & 255
        buffer[4] = argument & 255
        buffer[5] = crc7(self.command_view) | 1
        self.spi.write(buffer)
        if skip_first:
            self.spi.readinto(self.token, 0xFF)
        for _ in range(100):
            self.spi.readinto(self.token, 0xFF)
            response = self.token[0]
            if response & 0x80 == 0:
                if final:
                    self.spi.readinto(self.tail, 0xFF)
                if release:
                    self.end()
                return response
        self.end()
        return -1

    def read_data(self, buffer):
        started = time.ticks_ms()
        while True:
            self.spi.readinto(self.token, 0xFF)
            if self.token[0] == 0xFE:
                break
            if self.token[0] != 0xFF:
                raise OSError("SD data error token")
            if time.ticks_diff(time.ticks_ms(), started) >= 250:
                raise OSError("SD read timeout")
        self.spi.readinto(buffer, 0xFF)
        self.spi.readinto(self.checksum, 0xFF)

    def readblocks(self, block, buffer):
        count, remainder = divmod(len(buffer), 512)
        if not count or remainder or block < 0 or block + count > self.sectors:
            raise ValueError("Invalid SD block range")
        self.bus.configure(self.baudrate)
        self.spi.write(b"\xff")
        multiple = count > 1
        try:
            command = 18 if multiple else 17
            if self.command(command, block * self.address_multiplier, release=False) != 0:
                raise OSError(5)
            if multiple:
                view = memoryview(buffer)
                for offset in range(0, len(buffer), 512):
                    self.read_data(view[offset:offset + 512])
                if self.command(12, 0, skip_first=True) != 0:
                    raise OSError(5)
            else:
                self.read_data(buffer)
        finally:
            self.end()

    def ioctl(self, operation, argument):
        if operation == 4:
            return self.sectors
        if operation == 5:
            return 512
        if operation in (1, 2, 3):
            return 0
