"""Convert Codex Pets atlases and install local RGB565 packs over USB."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_PET = "super-saiyan-goku-combat"
BACKGROUND = (8, 12, 20)
CELL = (192, 208)
OUTPUT_SIZE = (96, 104)
# Codex Pets src/domain/config.ts, blob 12403682002a5f4415c65e1514317c7e978161bc.
# A clip is a sequence of (atlas row, frame count) segments played back to back.
# Rows 9-10 only exist in v2 atlases: sixteen head-turn frames of the idle pose.
CLIPS = {"idle": ((0, 6),), "waving": ((3, 4),), "jumping": ((4, 5),),
         "failed": ((5, 8),), "waiting": ((6, 6),), "running": ((7, 6),),
         "review": ((8, 6),),
         "look": ((9, 8), (10, 8)),
         "retry": ((2, 8), (1, 8))}  # running-left, then running-right
STATE_CLIPS = {"offline": "idle", "idle": "idle", "thinking": "look",
               "working": "running", "permission": "review", "question": "waving",
               "success": "jumping", "error": "failed", "retry": "retry", "pairing": "waiting"}
CLIP_LABELS = {
    "idle": "Repos (idle)",
    "look": "Regard autour (look)",
    "running": "Action / Course (running)",
    "review": "Vérification (review)",
    "waving": "Salut (waving)",
    "jumping": "Saut / Succès (jumping)",
    "failed": "Échec / Triste (failed)",
    "retry": "Aller-retour (retry)",
    "waiting": "Attente (waiting)",
}


def has_pillow():
    """Return True if Pillow (PIL.Image) can be imported."""
    try:
        return (importlib.util.find_spec("PIL") is not None
                and importlib.util.find_spec("PIL.Image") is not None)
    except (ImportError, ValueError):
        return False


def clip_frames(clip):
    return sum(count for _, count in clip)


def layout(sheet_height):
    """v1 atlases have no look rows: thinking falls back to the idle clip."""
    if sheet_height >= 11 * CELL[1]:
        return CLIPS, STATE_CLIPS
    clips = {name: clip for name, clip in CLIPS.items() if name != "look"}
    return clips, dict(STATE_CLIPS, thinking="idle")


def default_states_for_clips(available_clips):
    """Build default OpenBuddy state -> clip mapping for a given set of available clip names."""
    available = set(available_clips)
    if "idle" not in available:
        fallback = next(iter(available))
    else:
        fallback = "idle"
    return {
        state: (clip if clip in available else fallback)
        for state, clip in STATE_CLIPS.items()
    }


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value):
        raise ValueError("Invalid pet id")
    return value


def parse_codex_pet_url(url_or_id):
    """Extract and validate a Codex Pets id from a URL or bare pet slug."""
    if not isinstance(url_or_id, str):
        raise ValueError("URL ou identifiant de pet invalide")
    raw = url_or_id.strip()
    if not raw:
        raise ValueError("Veuillez saisir une URL Codex Pets")
    if "://" not in raw and "/" not in raw and "#" not in raw:
        return valid_id(raw.lower())
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname != "codex-pets.net":
        raise ValueError("Seules les URL HTTPS https://codex-pets.net/... sont acceptées")
    candidates = []
    if parsed.fragment:
        candidates.append(parsed.fragment.split("?", 1)[0])
    if parsed.path:
        candidates.append(parsed.path.split("?", 1)[0])
    for segment in candidates:
        match = re.search(r"(?:^|/)pets/([a-z0-9][a-z0-9-]{0,63})(?:/|$)", segment.lower())
        if match:
            return valid_id(match.group(1))
    raise ValueError("Impossible d'extraire l'identifiant du pet depuis cette URL Codex Pets")


def download(url, destination, limit):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "codex-pets.net":
        raise ValueError("Only HTTPS assets from codex-pets.net are accepted")
    request = Request(url, headers={"User-Agent": "OpenBuddy/0.1 (local asset conversion)"})
    with urlopen(request, timeout=30) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname != "codex-pets.net":
            raise ValueError("Unexpected asset redirect")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Download exceeds size limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def fetch_codex_pet(url_or_id, cache_root=None):
    """Download metadata and spritesheet for a Codex Pets URL or slug."""
    pet_id = parse_codex_pet_url(url_or_id)
    base_cache = Path(cache_root) if cache_root is not None else Path(".cache/pets")
    cache = base_cache / pet_id
    metadata = cache / "catalog.json"
    download("https://codex-pets.net/api/pets/" + pet_id, metadata, 128 * 1024)
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    pet = payload.get("pet")
    if not isinstance(pet, dict) or not isinstance(pet.get("spritesheetUrl"), str):
        raise ValueError("Réponse Codex Pets incomplète pour " + pet_id)
    sheet = cache / "spritesheet.webp"
    download(pet["spritesheetUrl"], sheet, 16 * 1024 * 1024)
    source = {
        "name": pet.get("displayName") or pet_id,
        "url": "https://codex-pets.net/#/pets/" + pet_id,
        "spritesheet_url": pet["spritesheetUrl"],
        "author": pet.get("ownerHandle") or pet.get("ownerName") or "unknown",
        "sha256": hashlib.sha256(sheet.read_bytes()).hexdigest(),
        "rights": "Community artwork; redistribution rights not verified; local use only",
    }
    return pet_id, sheet, source, pet


def rgb565(image):
    """Big-endian RGB565; alpha must already be composited on the background."""
    pixels = image.convert("RGB").tobytes()
    result = bytearray(len(pixels) // 3 * 2)
    target = 0
    for offset in range(0, len(pixels), 3):
        red, green, blue = pixels[offset:offset + 3]
        color = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        result[target] = color >> 8
        result[target + 1] = color & 255
        target += 2
    return result


def _write_manifest_atomic(output, manifest):
    descriptor, temp_name = tempfile.mkstemp(prefix=".manifest-", suffix=".json", dir=output)
    temporary = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, indent=2) + "\n")
        os.replace(temporary, output / "manifest.json")
    finally:
        temporary.unlink(missing_ok=True)


def write_pack_from_clips(output, pet_id, source, clip_payloads, states=None, frame_ms=None, force=True):
    """Write a validated RGB565 pack directly from pre-encoded clip bytes (no Pillow required).

    `clip_payloads` maps clip_name -> (raw_rgb565_bytes, frame_count).
    """
    valid_id(pet_id)
    output = Path(output)
    if frame_ms is not None and not 100 <= frame_ms <= 2000:
        raise ValueError("Frame duration must be between 100 and 2000 ms")
    if output.exists() and any(output.iterdir()) and not force:
        raise ValueError("Output is not empty; use --force to replace generated pack files")
    if not isinstance(clip_payloads, dict) or not 1 <= len(clip_payloads) <= 16:
        raise ValueError("Invalid clip payload set")
    if "idle" not in clip_payloads:
        raise ValueError("Pack must include an 'idle' clip")

    frame_bytes = OUTPUT_SIZE[0] * OUTPUT_SIZE[1] * 2
    output.mkdir(parents=True, exist_ok=True)

    resolved_states = default_states_for_clips(clip_payloads.keys())
    if states is not None:
        for state_name, clip_name in states.items():
            if state_name in resolved_states:
                if clip_name not in clip_payloads:
                    raise ValueError("Unknown clip in state mapping: " + str(clip_name))
                resolved_states[state_name] = clip_name

    manifest = {
        "version": 1, "id": pet_id, "format": "RGB565-BE",
        "width": OUTPUT_SIZE[0], "height": OUTPUT_SIZE[1],
        "background": list(BACKGROUND), "states": resolved_states,
        "source": dict(source) if isinstance(source, dict) else {},
        "animations": {},
    }

    for name, (data, count) in clip_payloads.items():
        valid_id(name)
        if type(count) is not int or not 1 <= count <= 64:
            raise ValueError("Invalid frame count for clip: " + name)
        expected = count * frame_bytes
        if not isinstance(data, (bytes, bytearray)) or len(data) != expected:
            raise ValueError("Invalid RGB565 byte length for clip: " + name)
        path = output / (name + ".rgb565")
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        manifest["animations"][name] = {
            "file": path.name,
            "frames": count,
            "frame_ms": frame_ms or max(count * 260, 1400) // count,
            "sha256": digest,
            "bytes": expected,
        }

    # Remove stale clip files when replacing an existing pack (e.g., v2 -> v1).
    expected_files = {item["file"] for item in manifest["animations"].values()}
    for existing in output.glob("*.rgb565"):
        if existing.name not in expected_files:
            existing.unlink(missing_ok=True)

    _write_manifest_atomic(output, manifest)
    return manifest


def convert(sheet_path, output, pet_id, source, frame_ms=None, force=False, states=None):
    from PIL import Image, ImageDraw

    valid_id(pet_id)
    output = Path(output)
    if frame_ms is not None and not 100 <= frame_ms <= 2000:
        raise ValueError("Frame duration must be between 100 and 2000 ms")
    if output.exists() and any(output.iterdir()) and not force:
        raise ValueError("Output is not empty; use --force to replace generated pack files")
    with Image.open(sheet_path) as image:
        if image.size not in ((1536, 1872), (1536, 2288)):
            raise ValueError("Expected a Codex Pets v1/v2 atlas (1536x1872 or 1536x2288)")
        sheet = image.convert("RGBA")
    output.mkdir(parents=True, exist_ok=True)
    clips, default_states = layout(sheet.height)
    resolved_states = dict(default_states)
    if states is not None:
        for state_name, clip_name in states.items():
            if state_name in resolved_states:
                if clip_name not in clips:
                    raise ValueError("Unknown clip in state mapping: " + str(clip_name))
                resolved_states[state_name] = clip_name
    manifest = {
        "version": 1, "id": pet_id, "format": "RGB565-BE",
        "width": OUTPUT_SIZE[0], "height": OUTPUT_SIZE[1],
        "background": list(BACKGROUND), "states": resolved_states,
        "source": source, "animations": {},
    }
    preview = Image.new("RGB", (len(clips) * 112, 128), BACKGROUND)
    draw = ImageDraw.Draw(preview)
    for index, (name, clip) in enumerate(clips.items()):
        path = output / (name + ".rgb565")
        digest = hashlib.sha256()
        count = clip_frames(clip)
        with path.open("wb") as handle:
            frame_number = 0
            for row, segment in clip:
                for frame in range(segment):
                    box = (frame * CELL[0], row * CELL[1], (frame + 1) * CELL[0], (row + 1) * CELL[1])
                    cell = sheet.crop(box).resize(OUTPUT_SIZE, Image.Resampling.NEAREST)
                    opaque = Image.new("RGBA", OUTPUT_SIZE, BACKGROUND + (255,))
                    opaque.alpha_composite(cell)
                    data = rgb565(opaque)
                    handle.write(data)
                    digest.update(data)
                    if frame_number == 0:
                        preview.paste(opaque.convert("RGB"), (index * 112 + 8, 0))
                    frame_number += 1
        draw.text((index * 112 + 8, 110), name, fill=(225, 233, 245))
        manifest["animations"][name] = {
            "file": path.name, "frames": count,
            "frame_ms": frame_ms or max(count * 260, 1400) // count,
            "sha256": digest.hexdigest(), "bytes": path.stat().st_size,
        }
    preview.save(output / "preview.png")
    # Remove stale clip files when replacing an existing pack.
    expected_files = {item["file"] for item in manifest["animations"].values()}
    for existing in output.glob("*.rgb565"):
        if existing.name not in expected_files:
            existing.unlink(missing_ok=True)
    # Publish the manifest last: a failed conversion is not a complete pack.
    _write_manifest_atomic(output, manifest)
    return manifest


def convert_command(args):
    pet_id, sheet, source, _pet = fetch_codex_pet(args.pet)
    output = Path(args.output) if args.output else Path("build/pets") / pet_id
    manifest = convert(sheet, output, pet_id, source, args.frame_ms, args.force)
    total = sum(item["bytes"] for item in manifest["animations"].values())
    print("Pack:", output)
    print("%d animations, %d frames, %d bytes; RGB565-BE 96x104" % (
        len(manifest["animations"]), sum(item["frames"] for item in manifest["animations"].values()), total))
    print("Preview:", output / "preview.png")


def install(args):
    folder = Path(args.pack).resolve()
    manifest_path = folder / "manifest.json"
    if manifest_path.stat().st_size > 8192:
        raise ValueError("Manifest too large")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pet_id = valid_id(manifest["id"])
    if manifest.get("version") != 1 or manifest.get("format") != "RGB565-BE":
        raise ValueError("Unsupported pack")
    filenames = []
    for animation in manifest["animations"].values():
        name = animation["file"]
        if not re.fullmatch(r"[a-z0-9-]+\.rgb565", name):
            raise ValueError("Unsafe pack filename")
        path = folder / name
        if path.stat().st_size != animation["bytes"]:
            raise ValueError("Size mismatch: " + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != animation["sha256"]:
            raise ValueError("Checksum mismatch: " + name)
        if name not in filenames:
            filenames.append(name)
    root = Path(__file__).resolve().parents[1]
    target = ":/sd/openbuddy/pets/" + pet_id + "/"
    command = [sys.executable, "-u", "-m", "mpremote", "connect", args.port,
               "exec", "PACK_ID=%r; REPLACE=%r" % (pet_id, args.replace),
               "+", "run", str(root / "tools/prepare_pet_sd.py")]
    for name in filenames + ["manifest.json"]:
        command.extend(["+", "fs", "cp", str(folder / name), target + name])
    command.extend(["+", "run", str(root / "tools/verify_pet_sd.py")])
    print("Installing pack on external SD only.", flush=True)
    try:
        subprocess.run(command, check=True)
    finally:
        # Also restart after an interrupted transfer; never leave the companion in REPL.
        subprocess.run([sys.executable, "-m", "mpremote", "connect", args.port, "reset"],
                       timeout=20, check=False)


def main():
    parser = argparse.ArgumentParser(description="Convert and install OpenBuddy pet animations")
    commands = parser.add_subparsers(dest="command", required=True)
    conversion = commands.add_parser("convert", help="Download and convert a Codex Pets atlas")
    conversion.add_argument("--pet", default=DEFAULT_PET, help="Codex Pets URL or pet id")
    conversion.add_argument("--output")
    conversion.add_argument("--frame-ms", type=int)
    conversion.add_argument("--force", action="store_true")
    installation = commands.add_parser("install", help="Copy and verify a pack on the ESP32 SD via USB")
    installation.add_argument("pack")
    installation.add_argument("--port", default="COM9")
    installation.add_argument("--replace", action="store_true", help="Replace an existing OpenBuddy pack")
    args = parser.parse_args()
    try:
        if args.command == "convert":
            convert_command(args)
        else:
            install(args)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, "Error: %s\n" % error)


if __name__ == "__main__":
    main()
