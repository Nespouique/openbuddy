"""Landscape diagnostic UI and a deliberately dim, non-blocking status halo."""

import time

from st7789 import rgb

COLORS = {
    "offline": (72, 84, 108), "idle": (72, 132, 180),
    "thinking": (70, 116, 255), "working": (32, 220, 210),
    "permission": (255, 120, 24), "question": (255, 210, 40),
    "success": (50, 230, 120), "error": (255, 55, 60),
    "retry": (220, 140, 32), "pairing": (170, 90, 255),
}
LABELS = {
    "offline": "OFFLINE", "idle": "READY", "thinking": "THINKING",
    "working": "WORKING", "permission": "PERMISSION", "question": "QUESTION",
    "success": "DONE", "error": "ERROR", "retry": "RETRY", "pairing": "PAIRING",
}
BACKGROUND = rgb(8, 12, 20)
WHITE = rgb(225, 233, 245)
MUTED = rgb(120, 138, 158)


def printable(text):
    return "".join(character if 32 <= ord(character) <= 126 else "?"
                   for character in text)


class BuddyUI:
    def __init__(self, lcd, led, player=None):
        self.lcd = lcd
        self.led = led
        self.player = player
        self.previous = None
        self.face_frame = None
        self.led_value = None
        self.lcd.fill_rect(0, 0, lcd.w, lcd.h, BACKGROUND)

    def invalidate(self):
        """Force a full redraw, e.g. after the panel orientation changed."""
        self.previous = None

    def _face(self, accent, frame, state):
        lcd = self.lcd
        lcd.fill_rect(16, 43, 82, 88, BACKGROUND)
        bob = 3 if frame % 2 and state in ("thinking", "working") else 0
        x, y = 25, 55 + bob
        lcd.fill_rect(x + 28, y - 10, 5, 10, accent)
        lcd.fill_rect(x + 5, y, 56, 48, accent)
        lcd.fill_rect(x, y + 10, 66, 30, accent)
        lcd.fill_rect(x + 10, y + 8, 46, 28, BACKGROUND)
        height = 2 if frame % 8 == 0 else 8
        lcd.fill_rect(x + 18, y + 17, 7, height, WHITE)
        lcd.fill_rect(x + 41, y + 17, 7, height, WHITE)
        lcd.fill_rect(x + 24, y + 43, 18, 3, BACKGROUND)
        lcd.fill_rect(x + 10, y + 48, 12, 8, accent)
        lcd.fill_rect(x + 44, y + 48, 12, 8, accent)

    def draw(self, buddy, now):
        usb = getattr(buddy, "kind", None) == "usb"
        pairing = buddy.pairing_open(now) or buddy.passkey is not None
        state = "pairing" if pairing else buddy.state if buddy.online(now) else "offline"
        color = COLORS[state]
        accent = rgb(*color)
        if buddy.passkey is not None:
            detail = "%06d" % buddy.passkey
            footer = "CHECK PC CODE - BOOT TO CONFIRM" if buddy.confirm else "ENTER THIS CODE ON YOUR PC"
        elif pairing:
            remaining = max(0, time.ticks_diff(buddy.pair_until, now) // 1000)
            detail = "WINDOW %ds" % remaining
            footer = "ADD OPENBUDDY IN WINDOWS BLUETOOTH"
        elif buddy.hid_until is not None:
            detail = "F13 TEST ARMED"
            footer = "SHORT BOOT = ONE F13 KEY"
        else:
            detail = ((printable(buddy.label)[:24] or "Awaiting events")
                      if buddy.online(now) else "No Python bridge")
            footer = "USB BRIDGE ACTIVE" if usb else "BOOT: ROTATE | HOLD 3s: PAIR"
        link = "USB LINK" if usb else "BLE SECURE" if buddy.secure else "BLE WAIT"
        asset_ready = self.player is not None and self.player.available
        quota = printable(getattr(buddy, "info", ""))[:24] if buddy.online(now) else ""
        snapshot = (state, detail, footer, link, asset_ready, quota)
        changed = snapshot != self.previous
        if changed:
            self.previous = snapshot
            self.face_frame = None
            lcd = self.lcd
            lcd.fill_rect(0, 0, lcd.w, lcd.h, BACKGROUND)
            lcd.fill_rect(0, 0, lcd.w, 3, accent)
            lcd.text("OPENBUDDY", 12, 15, WHITE, BACKGROUND)
            lcd.text(link, 224, 15, MUTED, BACKGROUND)
            lcd.text(LABELS[state], 124, 55, accent, BACKGROUND, 2)
            scale = 2 if buddy.passkey is not None else 1
            lcd.text(detail[:24], 124, 87, WHITE, BACKGROUND, scale)
            lcd.text(quota or ("PET / MICROSD" if asset_ready
                               else "USB SERIAL / v1" if usb else "HID + GATT / v1"),
                     124, 113, MUTED, BACKGROUND)
            lcd.fill_rect(12, 141, 296, 1, accent)
            lcd.text(footer, 12, 153, MUTED, BACKGROUND)
        frame = now // 350
        played = asset_ready and self.player.draw(state, now, force=changed)
        if not played and frame != self.face_frame:
            if asset_ready:
                self.lcd.fill_rect(8, 35, 104, 104, BACKGROUND)
            self.face_frame = frame
            self._face(accent, frame, state)
        # RGB components stay below 40; do not blink at strobe frequencies.
        phase = (now % 2000) / 1000
        breath = 1 - abs(phase - 1)
        brightness = 0.02 if state == "offline" else 0.03 + 0.09 * breath
        value = tuple(min(32, int(channel * brightness)) for channel in color)
        if value != self.led_value:
            self.led_value = value
            self.led[0] = value
            self.led.write()
