"""Render a README GIF from the real firmware UI and a local, unbundled pet pack.

No device, OpenCode service, Bluetooth connection or network access is used.
The OpenCode event sequence is illustrative, not a recording of a live session.
"""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SIZE = (1120, 660)
FONT = ROOT / "tools/assets/font_petme128_8x8.h"
BACKGROUND = (11, 16, 23)
PANEL = (17, 24, 34)
WHITE = (231, 238, 247)
MUTED = (151, 168, 187)
BORDER = (42, 56, 73)
TEAL = (83, 221, 193)
STEP_MS = 100


@dataclass(frozen=True)
class Scene:
    state: str
    title: str
    description: str
    event: str
    label: str
    mode: str
    duration: int


SCENES = (
    Scene("idle", "Prêt à démarrer", "Le compagnon attend l'activité\nd'une session OpenCode.",
          "Aucune exécution active", "OpenCode ready", "usb", 1600),
    Scene("thinking", "L'agent réfléchit", "L'activité de raisonnement\nfait évoluer l'état du compagnon.",
          "session.reasoning.started", "OpenCode active", "usb", 2600),
    Scene("working", "L'agent travaille", "Un outil s'exécute :\nGoku passe à l'action.",
          "session.tool.called", "Running tools", "usb", 2000),
    Scene("permission", "À vous de valider", "Une autorisation est en attente.\nLa validation reste dans OpenCode.",
          "permission.asked", "1 approval(s) pending", "usb", 2300),
    Scene("question", "Une réponse attendue", "OpenCode pose une question.\nLe pet et le halo vous alertent.",
          "form.created", "1 response(s) pending", "usb", 2300),
    Scene("success", "Réponse terminée", "L'exécution a réussi.\nGoku fête la fin de la tâche.",
          "session.execution.succeeded", "Response ready", "usb", 1800),
    Scene("working", "Même synchro, sans fil", "Le transport change,\nles états restent identiques.",
          "session.tool.called", "Running tools", "ble", 1600),
    Scene("error", "Une erreur survient", "L'exécution a échoué.\nLes détails restent dans OpenCode.",
          "session.execution.failed", "Execution failed", "ble", 1800),
    Scene("retry", "Nouvelle tentative", "OpenCode attend avant de réessayer.\nLe compagnon indique RETRY.",
          "session.retry.scheduled", "Retry scheduled", "ble", 4200),
    Scene("success", "C'est reparti !", "La nouvelle exécution a réussi.\nLe même pet, en USB ou Bluetooth.",
          "session.execution.succeeded", "Response ready", "ble", 1800),
    Scene("offline", "Plus de source active", "Sans heartbeat, retour OFFLINE\naprès 15 s (transition illustrée).",
          "Expiration du heartbeat", "", "ble", 1800),
)


def load_font(path=FONT):
    source = path.read_text(encoding="utf-8")
    table = source.split("font_petme128_8x8[] = {", 1)[1].split("};", 1)[0]
    data = bytes(int(value, 16) for value in re.findall(r"0x([0-9a-fA-F]{2})", table))
    if len(data) != 96 * 8:
        raise ValueError("Expected the MicroPython 8x8 font for ASCII 32-127")
    return data


def rgb565(red, green, blue):
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


def unpack_color(color):
    red, green, blue = color >> 11, (color >> 5) & 63, color & 31
    return ((red << 3) | (red >> 2), (green << 2) | (green >> 4), (blue << 3) | (blue >> 2))


@lru_cache(maxsize=64)
def decode_frame(data, width, height):
    if len(data) != width * height * 2:
        raise ValueError("Unexpected RGB565 frame length")
    pixels = bytes(channel for (value,) in struct.iter_unpack(">H", data) for channel in unpack_color(value))
    return Image.frombytes("RGB", (width, height), pixels)


class Display:
    """Pillow equivalent of the three drawing methods used by BuddyUI/PetPlayer."""

    w, h = 320, 172

    def __init__(self, font):
        self.font = font
        self.image = Image.new("RGB", (self.w, self.h))

    def fill_rect(self, x, y, width, height, color):
        ImageDraw.Draw(self.image).rectangle((x, y, x + width - 1, y + height - 1), fill=unpack_color(color))

    def text(self, value, x, y, fg, bg, scale=1):
        if not value:
            return
        line = Image.new("RGB", (len(value) * 8, 8), unpack_color(bg))
        for index, character in enumerate(value):
            code = ord(character)
            code = code if 32 <= code <= 127 else 63
            for column, bits in enumerate(self.font[(code - 32) * 8:(code - 31) * 8]):
                for row in range(8):
                    if bits & (1 << row):
                        line.putpixel((index * 8 + column, row), unpack_color(fg))
        self.image.paste(line.resize((line.width * scale, 8 * scale), Image.Resampling.NEAREST), (x, y))

    def blit(self, data, x, y, width, height):
        self.image.paste(decode_frame(bytes(data), width, height), (x, y))


class Led:
    def __init__(self):
        self.value = (0, 0, 0)

    def __setitem__(self, _index, value):
        self.value = value

    def write(self):
        pass


@contextmanager
def firmware_runtime():
    # Import the real UI/player without importing machine, SPI or Bluetooth.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from c6lcd import board_config, buddy_protocol

    driver = ModuleType("st7789")
    driver.rgb = rgb565

    def module(name):
        spec = importlib.util.spec_from_file_location("_demo_" + name, ROOT / "c6lcd" / (name + ".py"))
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
        return result

    with patch.dict(sys.modules, {"st7789": driver, "board_config": board_config,
                                 "buddy_protocol": buddy_protocol}), \
            patch.object(time, "ticks_diff", lambda end, start: end - start, create=True), \
            patch.object(time, "ticks_add", lambda now, delay: now + delay, create=True), \
            patch.object(time, "ticks_us", lambda: time.monotonic_ns() // 1000, create=True):
        yield module("buddy_ui"), module("pet_player")


@lru_cache(maxsize=24)
def font(size, bold=False, mono=False):
    candidates = (["consola.ttf", "DejaVuSansMono.ttf"] if mono else
                  ["segoeuib.ttf" if bold else "segoeui.ttf", "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"])
    for name in candidates:
        for path in (Path("C:/Windows/Fonts") / name, Path(name)):
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def text(draw, xy, value, size=16, color=WHITE, bold=False, mono=False):
    draw.text(xy, value, font=font(size, bold, mono), fill=color, anchor="lt")


def pill(draw, box, label, active, accent):
    draw.rounded_rectangle(box, radius=8, fill=(22, 46, 45) if active else PANEL,
                           outline=accent if active else BORDER, width=1)
    text(draw, (box[0] + 14, box[1] + 12), label, 13,
         accent if active else MUTED, bold=True)


def compose(screen, scene, index, color, progress=0, total=len(SCENES)):
    image = Image.new("RGB", SIZE, BACKGROUND)
    draw = ImageDraw.Draw(image)
    text(draw, (36, 24), "OPENCODE  ×  ESP32", 12, TEAL, mono=True)
    text(draw, (34, 49), "OpenBuddy", 42, bold=True)
    text(draw, (286, 67), "Votre session, en un regard.", 19, MUTED)
    pill(draw, (814, 48, 934, 88), "LOCAL USB", scene.mode == "usb", TEAL)
    pill(draw, (946, 48, 1084, 88), "BLUETOOTH", scene.mode == "ble", TEAL)
    draw.line((36, 119, 1084, 119), fill=BORDER)

    draw.rounded_rectangle((36, 150, 368, 510), radius=14, fill=PANEL, outline=BORDER)
    draw.rounded_rectangle((36, 150, 40, 510), radius=2, fill=color)
    text(draw, (56, 172), "ÉVÉNEMENT OPENCODE", 12, MUTED, mono=True)
    draw.ellipse((56, 204, 64, 212), fill=color)
    text(draw, (74, 200), scene.state.upper(), 13, color, bold=True, mono=True)
    text(draw, (56, 238), scene.title, 23, bold=True)
    draw.multiline_text((56, 281), scene.description, font=font(15), fill=MUTED, spacing=8)
    draw.line((56, 346, 348, 346), fill=BORDER)
    text(draw, (56, 366), scene.event, 12, color, mono=True)
    text(draw, (56, 400), 'state  "' + scene.state + '"', 13, mono=True)
    text(draw, (56, 427), 'label  "' + (scene.label or "No Python bridge") + '"', 12, MUTED, mono=True)
    text(draw, (56, 476), "État transmis, pas le contenu.", 13, MUTED)

    text(draw, (432, 130), "APERÇU DU RENDU FIRMWARE  ·  320 × 172", 11, MUTED, mono=True)
    draw.rounded_rectangle((414, 150, 1090, 510), radius=17, fill=(23, 30, 40), outline=BORDER, width=2)
    draw.rounded_rectangle((402, 307, 415, 351), radius=3, fill=(74, 85, 100))
    draw.rectangle((407, 316, 414, 341), fill=BACKGROUND)
    image.paste(screen.resize((640, 344), Image.Resampling.NEAREST), (432, 158))
    # Explanatory halo marker, not a photo or a measurement of LED brightness.
    draw.ellipse((1081, 158, 1095, 172), fill=tuple(value // 5 for value in color))
    draw.ellipse((1085, 162, 1091, 168), fill=color)

    text(draw, (36, 530), "%02d / %02d" % (index + 1, total), 12, MUTED, mono=True)
    start, end = 126, 1084
    gap = 6
    width = (end - start - gap * (total - 1)) // total
    for position in range(total):
        x = start + position * (width + gap)
        draw.rounded_rectangle((x, 534, x + width, 538), radius=2, fill=BORDER)
        if position == index:
            draw.rounded_rectangle((x, 534, x + max(2, int(width * progress)), 538), radius=2, fill=color)

    nodes = ((36, 566, 334, 610), (390, 566, 690, 610), (746, 566, 1084, 610))
    labels = ("1  Événements OpenCode", "2  Pont Python local", "3  " + ("USB série → écran + halo" if scene.mode == "usb" else "Bluetooth LE → écran + halo"))
    for box, label in zip(nodes, labels):
        draw.rounded_rectangle(box, radius=8, fill=PANEL, outline=BORDER)
        text(draw, (box[0] + 16, box[1] + 13), label, 14, WHITE)
    for x in (350, 706):
        draw.line((x, 588, x + 24, 588), fill=color, width=2)
        draw.line((x + 18, 583, x + 24, 588, x + 18, 593), fill=color, width=2)
    text(draw, (36, 634), "DÉMO SIMULÉE · UI et animations du firmware · aucun accès à une session réelle", 11, MUTED)
    text(draw, (869, 634), "Pet : datpham / Codex Pets", 11, MUTED)
    return image


def generate(pet_dir, output, poster, scenes=SCENES):
    if not scenes or any(scene.duration <= 0 or scene.duration % STEP_MS for scene in scenes):
        raise ValueError("Scenes need positive durations in multiples of 100ms")
    pet_dir = pet_dir.resolve()
    if not (pet_dir / "manifest.json").is_file():
        raise ValueError("Local pet pack missing; install/convert it first (artwork is not bundled)")
    output.parent.mkdir(parents=True, exist_ok=True)
    poster.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="readme-demo-", dir=ROOT / "build") as temporary, \
            firmware_runtime() as (ui_module, player_module):
        selection = Path(temporary) / "active.json"
        selection.write_text(json.dumps({"pet": pet_dir.name}), encoding="utf-8")
        display, led = Display(load_font()), Led()
        player = player_module.PetPlayer(display, str(pet_dir.parent), str(selection))
        if not player.available:
            raise ValueError("Pet pack cannot be rendered: " + player.error)
        ui = ui_module.BuddyUI(display, led, player)
        buddy = SimpleNamespace(passkey=None, confirm=None, hid_until=None, secure=True, info="37 / 200",
                                pairing_open=lambda _now: False)
        frames, palette, samples = [], None, []
        now = 0
        poster_saved = False
        try:
            # Use one shared palette, trained on every state, to avoid color flicker.
            for index, scene in enumerate(scenes):
                buddy.state, buddy.label, buddy.kind = scene.state, scene.label, scene.mode
                buddy.online = lambda _now, state=scene.state: state != "offline"
                ui.draw(buddy, now)
                samples.append(compose(display.image, scene, index, ui_module.COLORS[scene.state], 0.5, len(scenes)))
                now += 2000
            training = Image.new("RGB", (560 * 3, 330 * ((len(samples) + 2) // 3)))
            for index, sample in enumerate(samples):
                training.paste(sample.resize((560, 330)), ((index % 3) * 560, (index // 3) * 330))
            palette = training.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
            ui.previous = None
            player.clip = None
            now = 0
            for index, scene in enumerate(scenes):
                buddy.state, buddy.label, buddy.kind = scene.state, scene.label, scene.mode
                buddy.online = lambda _now, state=scene.state: state != "offline"
                for elapsed in range(0, scene.duration, STEP_MS):
                    # Preserve the firmware's 20ms update cadence and clip timings.
                    for _ in range(STEP_MS // 20):
                        ui.draw(buddy, now)
                        now += 20
                    frame = compose(display.image, scene, index, ui_module.COLORS[scene.state], elapsed / scene.duration, len(scenes))
                    if not poster_saved and scene.state == "working":
                        frame.save(poster, optimize=True)
                        poster_saved = True
                    frames.append(frame.quantize(palette=palette, dither=Image.Dither.NONE))
            if not frames:
                raise ValueError("No demonstration frames generated")
            if not poster_saved:
                frames[0].convert("RGB").save(poster)
            frames[0].save(output, save_all=True, append_images=frames[1:], duration=STEP_MS,
                           loop=0, disposal=1, optimize=True,
                           comment=b"Simulated OpenBuddy demo; real firmware UI. Pet: datpham / Codex Pets.")
        finally:
            player.close()
            decode_frame.cache_clear()
    with Image.open(output) as gif:
        print("GIF:", output, gif.size, gif.n_frames, "frames;", output.stat().st_size, "bytes")
    print("Still preview:", poster)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pet-dir", type=Path, required=True, help="Local converted RGB565 pet pack")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/media/openbuddy-demo.gif")
    parser.add_argument("--poster", type=Path, default=ROOT / "docs/media/openbuddy-demo.png")
    args = parser.parse_args()
    generate(args.pet_dir, args.output, args.poster)


if __name__ == "__main__":
    main()
