"""Finite read-only hardware check; run in RAM with mpremote, then reset."""

import binascii
import gc
import hashlib
import json
import time
from shared_spi import SharedSPI
from storage import Storage
from st7789 import LCD
from pet_player import PetPlayer
from buddy_ble import BuddyBLE

bus = SharedSPI()
lcd = LCD("landscape", bus=bus)
storage = Storage(bus)
player = None
buddy = None
try:
    if not storage.mount():
        raise RuntimeError(storage.error)
    player = PetPlayer(lcd)
    if not player.available:
        raise RuntimeError(player.error)
    buddy = BuddyBLE()
    tick = time.ticks_ms()
    started = time.ticks_ms()
    verified = []
    for state in ("idle", "pairing", "success", "error", "question", "working", "permission"):
        clip = player.states[state]
        animation = player.animations[clip]
        digest = hashlib.sha256()
        for _ in range(animation["frames"]):
            if not player.draw(state, tick, force=True):
                raise RuntimeError(player.error)
            digest.update(player.buffer)
            buddy.tick(time.ticks_ms())
            tick = time.ticks_add(tick, animation["frame_ms"])
        if binascii.hexlify(digest.digest()).decode() != animation["sha256"]:
            raise RuntimeError("Shared SPI frame checksum mismatch: " + clip)
        verified.append(clip)
    gc.collect()
    result = player.stats()
    result.update({"verified_clips": verified, "elapsed_ms": time.ticks_diff(time.ticks_ms(), started),
                   "free_heap": gc.mem_free(), "frame_buffer_bytes": len(player.buffer)})
    print(json.dumps(result))
finally:
    if buddy is not None:
        buddy.close()
    if player is not None:
        player.close()
    storage.close()
