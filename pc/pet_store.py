"""Local pet pack discovery and pure-Python RGB565 decoding (no Pillow required)."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
import sys

from c6lcd.buddy_protocol import STATES

REQUIRED_STATES = STATES + ("offline", "pairing")
CHROMA_KEY_RGB = (1, 2, 3)  # Cannot be produced by RGB565 channel expansion.
CHROMA_KEY_HEX = "#010203"
BUILTIN_PET_ID = "builtin"
ROOT_DIR = Path(__file__).resolve().parents[1]


def safe_name(value):
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and value[0] in "abcdefghijklmnopqrstuvwxyz0123456789"
            and all(character in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in value))


def rgb565(red, green, blue):
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


def _build_lut():
    """Precompute big-endian RGB565 -> 3-byte RGB888 lookup table (196 KB)."""
    table = [b""] * 65536
    for value in range(65536):
        red = value >> 11
        green = (value >> 5) & 63
        blue = value & 31
        r8 = (red << 3) | (red >> 2)
        g8 = (green << 2) | (green >> 4)
        b8 = (blue << 3) | (blue >> 2)
        table[value] = bytes((r8, g8, b8))
    return table


_RGB565_LUT = None


def _rgb565_lut():
    global _RGB565_LUT
    if _RGB565_LUT is None:
        _RGB565_LUT = _build_lut()
    return _RGB565_LUT


def default_pet_roots():
    roots = [ROOT_DIR / "build/pets"]
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        roots.append(appdata / "openbuddy/pets")
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        roots.append(xdg / "openbuddy/pets")
    return roots


@dataclass(frozen=True)
class PetPack:
    pet_id: str
    directory: Path
    width: int
    height: int
    background: tuple
    states: dict
    animations: dict
    author: str = ""

    @property
    def frame_bytes(self):
        return self.width * self.height * 2

    @property
    def background_rgb565(self):
        return rgb565(*self.background)

    def metadata(self):
        return {
            "id": self.pet_id,
            "builtin": False,
            "width": self.width,
            "height": self.height,
            "background": list(self.background),
            "background_rgb565": self.background_rgb565,
            "states": dict(self.states),
            "animations": {
                name: {
                    "frames": info["frames"],
                    "frame_ms": info["frame_ms"],
                    "bytes": info["bytes"],
                }
                for name, info in self.animations.items()
            },
            "author": self.author,
        }


def validate_pack_directory(directory):
    """Validate and load a converted OpenBuddy RGB565 pack directory."""
    manifest_path = Path(directory) / "manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 16384:
        raise ValueError("Missing or oversized pet manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Pet manifest must be a JSON object")
    pet_id = manifest.get("id")
    if (manifest.get("version") != 1 or manifest.get("format") != "RGB565-BE"
            or not safe_name(pet_id)):
        raise ValueError("Unsupported pet manifest")
    width, height = manifest.get("width"), manifest.get("height")
    if (type(width) is not int or type(height) is not int
            or not 1 <= width <= 104 or not 1 <= height <= 104):
        raise ValueError("Invalid pet dimensions")
    background = manifest.get("background")
    if (not isinstance(background, list) or len(background) != 3
            or any(type(channel) is not int or not 0 <= channel <= 255 for channel in background)):
        raise ValueError("Invalid pet background")
    animations = manifest.get("animations")
    states = manifest.get("states")
    if (not isinstance(animations, dict) or not 1 <= len(animations) <= 16
            or not isinstance(states, dict)):
        raise ValueError("Invalid pet animation table")
    for state in REQUIRED_STATES:
        if states.get(state) not in animations:
            raise ValueError("Pet manifest missing state: " + state)
    clean_animations = {}
    root = Path(directory).resolve()
    for name, entry in animations.items():
        if not safe_name(name) or not isinstance(entry, dict):
            raise ValueError("Invalid animation entry")
        filename = entry.get("file", "")
        if (not isinstance(filename, str) or not filename.endswith(".rgb565")
                or not safe_name(filename[:-7])):
            raise ValueError("Unsafe animation filename")
        frames, delay = entry.get("frames"), entry.get("frame_ms")
        if (type(frames) is not int or not 1 <= frames <= 64
                or type(delay) is not int or not 100 <= delay <= 2000):
            raise ValueError("Invalid animation timing")
        expected = width * height * 2 * frames
        clip_path = (root / filename).resolve()
        if clip_path.parent != root or not clip_path.is_file():
            raise ValueError("Missing pet animation file")
        if entry.get("bytes") != expected or clip_path.stat().st_size != expected:
            raise ValueError("Incomplete pet animation file")
        clean_animations[name] = {
            "file": filename,
            "frames": frames,
            "frame_ms": delay,
            "bytes": expected,
        }
    source = manifest.get("source")
    author = source.get("author", "") if isinstance(source, dict) and isinstance(source.get("author"), str) else ""
    return PetPack(
        pet_id=pet_id,
        directory=root,
        width=width,
        height=height,
        background=tuple(background),
        states={state: states[state] for state in REQUIRED_STATES},
        animations=clean_animations,
        author=author,
    )


def discover_packs(roots=None):
    """Return a dict of {pet_id: PetPack} from local pet directories."""
    packs = {}
    for root in (default_pet_roots() if roots is None else roots):
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for child in sorted(root_path.iterdir()):
            if not child.is_dir() or not safe_name(child.name):
                continue
            try:
                pack = validate_pack_directory(child)
            except (OSError, ValueError, TypeError):
                continue
            packs.setdefault(pack.pet_id, pack)
    return packs


def resolve_pet(selection="auto", roots=None):
    """Resolve 'auto', 'builtin', or a pet_id into (active_id, PetPack | None, all_packs)."""
    packs = discover_packs(roots)
    if selection == BUILTIN_PET_ID:
        return BUILTIN_PET_ID, None, packs
    if selection and selection != "auto" and selection in packs:
        return selection, packs[selection], packs
    if packs:
        first_id = next(iter(packs))
        return first_id, packs[first_id], packs
    return BUILTIN_PET_ID, None, packs


def read_clip_bytes(pack, clip_name):
    if clip_name not in pack.animations:
        raise KeyError("Unknown animation clip: " + str(clip_name))
    info = pack.animations[clip_name]
    clip_path = (pack.directory / info["file"]).resolve()
    if clip_path.parent != pack.directory:
        raise ValueError("Unsafe animation path")
    data = clip_path.read_bytes()
    if len(data) != info["bytes"]:
        raise ValueError("Animation size mismatch")
    return data


def rgb565_frame_to_ppm(frame_bytes, width, height, transparent_rgb565=None, chroma_key=CHROMA_KEY_RGB):
    """Convert one big-endian RGB565 frame into binary PPM (P6) bytes for Tk PhotoImage."""
    expected = width * height * 2
    if len(frame_bytes) != expected:
        raise ValueError("Unexpected RGB565 frame size")
    lut = _rgb565_lut()
    key_bytes = bytes(chroma_key)
    values = struct.unpack(">%dH" % (width * height), frame_bytes)
    if transparent_rgb565 is None:
        rgb_data = b"".join(lut[value] for value in values)
    else:
        rgb_data = b"".join(
            key_bytes if value == transparent_rgb565 else lut[value]
            for value in values
        )
    header = ("P6\n%d %d\n255\n" % (width, height)).encode("ascii")
    return header + rgb_data
