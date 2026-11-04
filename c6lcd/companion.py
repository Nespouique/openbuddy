"""Single-threaded firmware loop: BLE callbacks never perform LCD drawing."""

import os
import sys
import time

from machine import Pin
from neopixel import NeoPixel

import board_config as cfg
from buddy_ble import BuddyBLE
from buddy_usb import BuddyUSB
from buddy_ui import BuddyUI
from st7789 import LCD, rgb
from shared_spi import SharedSPI
from storage import Storage
from pet_player import PetPlayer


ORIENTATION_FILE = "openbuddy-orientation.txt"
# The UI layout is landscape-only: BOOT flips the view by 180 degrees.
FLIP = {"landscape": "landscape_inv", "landscape_inv": "landscape"}
PET_RETRY_MS = 3000
PET_SLOW_RETRY_MS = 30000
PET_FAST_ATTEMPTS = 20


def load_orientation():
    try:
        with open(ORIENTATION_FILE) as handle:
            name = handle.read().strip()
    except OSError:
        return "landscape"
    return name if name in FLIP else "landscape"


def save_orientation(name):
    try:
        with open(ORIENTATION_FILE, "w") as handle:
            handle.write(name)
    except OSError:
        pass


def run():
    bus = SharedSPI()
    lcd = LCD(load_orientation(), bus=bus)
    led = NeoPixel(Pin(cfg.RGB_LED), 1)
    led[0] = (0, 0, 0)
    led.write()
    buddy = None
    storage = Storage(bus)
    player = None
    ui = None
    try:
        if storage.mount():
            player = PetPlayer(lcd)

        def asset_status():
            return {"sd": storage.mounted,
                    "pet": player.pet_id if player is not None and player.available else None}

        def reload_pet():
            nonlocal player
            try:
                storage.close()
                if player is not None:
                    player.close()
                if storage.mount(attempts=1):
                    player = PetPlayer(lcd)
                    ui.player = player
            except OSError as error:
                print("Pet reload failed:", error)

        buddy = BuddyBLE(asset_status=asset_status)
        usb = BuddyUSB(asset_status=asset_status)
        ui = BuddyUI(lcd, led, player)
        pet_attempts = 0
        pet_retry_at = time.ticks_add(time.ticks_ms(), PET_RETRY_MS)
        button = Pin(cfg.BOOT_BUTTON, Pin.IN, Pin.PULL_UP)
        raw_level = button.value()
        stable_level = raw_level
        changed_at = time.ticks_ms()
        pressed_at = None
        long_sent = False
        print("OpenBuddy BLE and USB ready; hold BOOT 3s to allow BLE pairing")
        while True:
            now = time.ticks_ms()
            level = button.value()
            if level != raw_level:
                raw_level = level
                changed_at = now
            if level != stable_level and time.ticks_diff(now, changed_at) >= 30:
                stable_level = level
                if level == 0:
                    pressed_at = now
                    long_sent = False
                else:
                    if pressed_at is not None and not long_sent:
                        if buddy.confirm is not None:
                            buddy.button()  # BLE pairing needs a physical confirmation.
                        else:
                            name = FLIP[lcd.orientation]
                            lcd.set_orientation(name)
                            save_orientation(name)
                            ui.invalidate()
                    pressed_at = None
            if (pressed_at is not None and not long_sent
                    and time.ticks_diff(now, pressed_at) >= 3000):
                buddy.button(long_press=True)
                long_sent = True
            if (not (player is not None and player.available)
                    and time.ticks_diff(now, pet_retry_at) >= 0):
                pet_attempts += 1
                pet_retry_at = time.ticks_add(
                    now, PET_RETRY_MS if pet_attempts < PET_FAST_ATTEMPTS else PET_SLOW_RETRY_MS)
                reload_pet()
            buddy.tick(now)
            usb.tick(now)
            pairing = buddy.pairing_open(now) or buddy.passkey is not None
            ui.draw(usb if usb.online(now) and not pairing else buddy, now)
            time.sleep_ms(20)
    except Exception as error:
        sys.print_exception(error)
        try:
            with open("openbuddy-error.txt", "w") as handle:
                if buddy is not None:
                    handle.write("\n".join(buddy.diagnostics) + "\n")
                sys.print_exception(error, handle)
                handle.flush()
            os.sync()
        except OSError:
            pass
        lcd.fill_rect(0, 0, lcd.w, lcd.h, 0)
        lcd.text("OPENBUDDY ERROR", 12, 20, rgb(255, 60, 60), 0, 2)
        lcd.text(str(error)[:36], 12, 65, rgb(220, 220, 220), 0)
        raise
    finally:
        if buddy is not None:
            buddy.close()
        if player is not None:
            player.close()
        storage.close()
        led[0] = (0, 0, 0)
        led.write()
