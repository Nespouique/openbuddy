"""Verify all copied bytes before activating the pack; run after prepare_pet_sd."""

import binascii
import hashlib
import json
import os
import vfs

if os.stat(pack_root + "/manifest.json")[6] > 8192:
    raise ValueError("Manifest too large")
with open(pack_root + "/manifest.json") as handle:
    manifest = json.load(handle)
if manifest.get("id") != PACK_ID or manifest.get("format") != "RGB565-BE":
    raise ValueError("Unexpected transferred pack")
buffer = bytearray(8192)
for animation in manifest["animations"].values():
    name = animation["file"]
    if ("/" in name or "\\" in name or ".." in name
            or not name.endswith(".rgb565")):
        raise ValueError("Unsafe transferred filename")
    path = pack_root + "/" + name
    if os.stat(path)[6] != animation["bytes"]:
        raise ValueError("Transferred size mismatch")
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            count = handle.readinto(buffer)
            if not count:
                break
            digest.update(memoryview(buffer)[:count])
    if binascii.hexlify(digest.digest()).decode() != animation["sha256"]:
        raise ValueError("Transferred checksum mismatch: " + name)
    print("Verified:", name)
with open("/sd/openbuddy/active.json.tmp", "w") as handle:
    json.dump({"pet": PACK_ID}, handle)
    handle.flush()
os.sync()
os.rename("/sd/openbuddy/active.json.tmp", "/sd/openbuddy/active.json")
os.sync()
vfs.umount("/sd")
sd.deinit()
print("Pet activated:", PACK_ID)
