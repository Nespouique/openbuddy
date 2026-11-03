"""Run in RAM, in one mpremote command chain; never format any device."""

import json
import os
import vfs
from machine import Pin, SDCard, SPI
import board_config as cfg


def valid_name(value):
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and all(character in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in value))


if not valid_name(PACK_ID):
    raise ValueError("Unsafe pack id")
Pin(cfg.LCD_CS, Pin.OUT, value=1)
Pin(cfg.SD_CS, Pin.OUT, value=1)
spi = SPI(cfg.LCD_SPI_ID, baudrate=1_000_000, sck=Pin(cfg.LCD_SCLK),
          mosi=Pin(cfg.LCD_MOSI), miso=Pin(cfg.SD_MISO))
spi.deinit()
sd = SDCard(slot=2, sck=Pin(cfg.LCD_SCLK), mosi=Pin(cfg.LCD_MOSI),
            miso=Pin(cfg.SD_MISO), cs=Pin(cfg.SD_CS), freq=10_000_000)
vfs.mount(sd, cfg.SD_MOUNT)
for path in ("/sd/openbuddy", "/sd/openbuddy/pets"):
    try:
        os.mkdir(path)
    except OSError as error:
        if error.args[0] != 17:
            raise
pack_root = "/sd/openbuddy/pets/" + PACK_ID
try:
    os.stat(pack_root)
except OSError as error:
    if error.args[0] != 2:
        raise
    os.mkdir(pack_root)
else:
    if not REPLACE:
        raise ValueError("Pack already exists; explicitly allow replacement")
    try:
        with open(pack_root + "/manifest.json") as handle:
            existing = json.load(handle)
    except OSError as error:
        if error.args[0] != 2:
            raise
        with open(pack_root + "/openbuddy-install.json") as handle:
            existing = json.load(handle)
    if existing.get("id") != PACK_ID or existing.get("format") != "RGB565-BE":
        raise ValueError("Existing directory is not this OpenBuddy pack")
with open(pack_root + "/openbuddy-install.json", "w") as handle:
    json.dump({"id": PACK_ID, "format": "RGB565-BE", "version": 1}, handle)
    handle.flush()
os.sync()
print("External SD target ready:", pack_root)
