"""Stream one big-endian RGB565 frame from SD, with a bounded RAM footprint."""

import json
import os
import time

import board_config as cfg
from buddy_protocol import STATES


def safe_name(value):
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and value[0] in "abcdefghijklmnopqrstuvwxyz0123456789"
            and all(character in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in value))


def read_json(path, limit):
    if not 0 < os.stat(path)[6] <= limit:
        raise ValueError("Invalid pet metadata size")
    with open(path) as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Pet metadata must be an object")
    return value


class PetPlayer:
    def __init__(self, lcd, directory=cfg.PET_DIRECTORY, selection=cfg.PET_SELECTION):
        self.lcd = lcd
        self.available = False
        self.error = None
        self.handle = None
        self.buffer = None
        self.clip = None
        self.cached = False
        self.index = 0
        self.next_due = None
        self.reads = 0
        self.draws = 0
        self.read_us = 0
        self.draw_us = 0
        self.max_read_us = 0
        self.pet_id = None
        try:
            self.pet_id = read_json(selection, 256).get("pet")
            if not safe_name(self.pet_id):
                raise ValueError("Unsafe selected pet id")
            self.root = directory + "/" + self.pet_id
            manifest = read_json(self.root + "/manifest.json", 8192)
            self._validate(manifest)
            self.width = manifest["width"]
            self.height = manifest["height"]
            self.frame_bytes = self.width * self.height * 2
            self.animations = manifest["animations"]
            self.states = manifest["states"]
            self.buffer = bytearray(self.frame_bytes)
            self.x = 8 + (104 - self.width) // 2
            self.y = 35 + (104 - self.height) // 2
            self.available = True
            print("Pet ready:", self.pet_id, self.width, self.height, "RAM", self.frame_bytes)
        except (OSError, ValueError, TypeError, KeyError, MemoryError) as error:
            self.disable(error)

    def _validate(self, manifest):
        if (manifest.get("version") != 1 or manifest.get("format") != "RGB565-BE"
                or manifest.get("id") != self.pet_id):
            raise ValueError("Unsupported pet manifest")
        width, height = manifest.get("width"), manifest.get("height")
        if (type(width) is not int or type(height) is not int
                or not 1 <= width <= 104 or not 1 <= height <= 104):
            raise ValueError("Pet dimensions exceed the display stage")
        if manifest.get("background") != [8, 12, 20]:
            raise ValueError("Pet background does not match the UI")
        animations, states = manifest.get("animations"), manifest.get("states")
        if (not isinstance(animations, dict) or not 1 <= len(animations) <= 16
                or not isinstance(states, dict)):
            raise ValueError("Invalid pet animation table")
        for state in STATES + ("offline", "pairing"):
            if states.get(state) not in animations:
                raise ValueError("Pet is missing a required state")
        for name, animation in animations.items():
            if not safe_name(name) or not isinstance(animation, dict):
                raise ValueError("Invalid animation entry")
            filename = animation.get("file", "")
            if (not isinstance(filename, str) or not filename.endswith(".rgb565")
                    or not safe_name(filename[:-7])):
                raise ValueError("Unsafe animation filename")
            frames, delay = animation.get("frames"), animation.get("frame_ms")
            if (type(frames) is not int or not 1 <= frames <= 64
                    or type(delay) is not int or not 100 <= delay <= 2000):
                raise ValueError("Invalid animation timing")
            expected = width * height * 2 * frames
            if animation.get("bytes") != expected or os.stat(self.root + "/" + filename)[6] != expected:
                raise ValueError("Incomplete pet animation")

    def draw(self, state, now, force=False):
        if not self.available:
            return False
        try:
            clip = self.states.get(state, self.states["idle"])
            if clip != self.clip:
                self.close()
                self.clip = clip
                self.animation = self.animations[clip]
                self.handle = open(self.root + "/" + self.animation["file"], "rb")
                self.index = 0
                self.next_due = None
                self.cached = False
            due = self.next_due is None or time.ticks_diff(now, self.next_due) >= 0
            if not due and not force:
                return True
            if due:
                if self.index >= self.animation["frames"]:
                    self.handle.seek(0)
                    self.index = 0
                started = time.ticks_us()
                if self.handle.readinto(self.buffer) != self.frame_bytes:
                    raise OSError("Short animation frame")
                elapsed = time.ticks_diff(time.ticks_us(), started)
                self.read_us += elapsed
                self.max_read_us = max(self.max_read_us, elapsed)
                self.reads += 1
                self.index += 1
                self.cached = True
                self.next_due = time.ticks_add(now, self.animation["frame_ms"])
            started = time.ticks_us()
            self.lcd.blit(self.buffer, self.x, self.y, self.width, self.height)
            self.draw_us += time.ticks_diff(time.ticks_us(), started)
            self.draws += 1
            return True
        except (OSError, ValueError, MemoryError) as error:
            self.disable(error)
            return False

    def stats(self):
        return {"frames_read": self.reads, "frames_drawn": self.draws,
                "read_ms_avg": self.read_us / max(1, self.reads) / 1000,
                "read_ms_max": self.max_read_us / 1000,
                "draw_ms_avg": self.draw_us / max(1, self.draws) / 1000}

    def disable(self, error):
        self.error = str(error)
        self.available = False
        self.close()
        self.buffer = None
        print("Pet fallback:", self.error)

    def close(self):
        if self.handle is not None:
            try:
                self.handle.close()
            except OSError:
                pass
            self.handle = None
