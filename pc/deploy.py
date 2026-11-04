"""Deploy only known firmware files, leaving bonding keys and the SD untouched."""

import argparse
from pathlib import Path
import subprocess
import sys

FILES = ("board_config.py", "shared_spi.py", "sdcard_spi.py", "storage.py",
         "st7789.py", "buddy_protocol.py", "buddy_ble.py", "buddy_usb.py", "pet_player.py",
         "buddy_ui.py", "companion.py", "orientation_demo.py", "main.py")


def main():
    parser = argparse.ArgumentParser(description="Deploy OpenBuddy MicroPython files without erasing flash")
    parser.add_argument("--port", default="COM9")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "c6lcd"
    command = [sys.executable, "-u", "-m", "mpremote", "connect", args.port]
    for index, name in enumerate(FILES):
        if index:
            command.append("+")
        command.extend(["fs", "cp", str(root / name), ":" + name])
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as error:
        parser.exit(1, "Deployment failed: %s\n" % error)
    finally:
        subprocess.run([sys.executable, "-m", "mpremote", "connect", args.port, "reset"],
                       timeout=20, check=False)


if __name__ == "__main__":
    main()
