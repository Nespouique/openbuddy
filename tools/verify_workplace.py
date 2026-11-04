"""Validate the transfer ZIP in a fresh, offline Python environment."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile


def command(arguments, cwd, environment=None):
    subprocess.run(arguments, cwd=cwd, env=environment, check=True, timeout=120)


def verify(archive_path, temporary_root):
    if sysconfig.get_platform() != "win-amd64":
        raise ValueError("The offline runtime test requires Windows x64 Python")
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openbuddy-workplace-test-", dir=temporary_root) as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 32 * 1024 * 1024:
                raise ValueError("Unexpected archive size")
            root_name = archive.namelist()[0].split("/")[0]
            if root_name not in ("openbuddy-workplace", "openbuddy-workplace-usb"):
                raise ValueError("Unexpected archive root")
            for name in archive.namelist():
                path = PurePosixPath(name)
                if (not name.startswith(root_name + "/") or ".." in path.parts
                        or "\\" in name or ":" in name or path.is_absolute()):
                    raise ValueError("Unsafe archive path")
                if any(value in name for value in ("bridge.json", "bonds.json", "__pycache__", ".venv/", "node_modules/")):
                    raise ValueError("Private or generated data found in archive")
            manifest = json.loads(archive.read(root_name + "/MANIFEST.json"))
            if manifest["package"] != root_name:
                raise ValueError("Manifest does not match the archive")
            expected = {root_name + "/" + name for name in manifest["files"]}
            expected.update({root_name + "/MANIFEST.json", root_name + "/SHA256SUMS.txt"})
            if set(archive.namelist()) != expected:
                raise ValueError("Archive contains undeclared files")
            for name, digest in manifest["files"].items():
                if hashlib.sha256(archive.read(root_name + "/" + name)).hexdigest() != digest:
                    raise ValueError("Archive checksum mismatch: " + name)
            archive.extractall(root)
        kit = root / root_name
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(root / "isolated-profile")
        environment["XDG_CACHE_HOME"] = str(root / "isolated-cache")
        command([sys.executable, "-m", "venv", str(kit / ".venv")], kit)
        python = str(kit / ".venv/Scripts/python.exe")
        command([python, "-m", "pip", "install", "--disable-pip-version-check", "--no-index",
                 "--find-links", str(kit / "wheelhouse"), "-r", str(kit / "requirements.txt")], kit, environment)
        command([python, "-c", "import bleak, pc.opencode; import winrt.windows.devices.bluetooth; "
                 "from pathlib import Path; from importlib.metadata import version; "
                 "assert Path(bleak.__file__).resolve().is_relative_to(Path('.venv').resolve()); "
                 "assert version('bleak') == '3.0.2'; print('Fresh offline imports OK')"], kit, environment)
        command([python, "-m", "pc.install_plugin", "--config-dir", str(root / "isolated-config")], kit, environment)
        command([python, "-m", "pc.install_plugin", "--config-dir", str(root / "isolated-config")], kit, environment)
        command([python, "-m", "pc.opencode", "--no-ble", "--mute", "--no-notifications", "--port", "0", "--duration", "0.5"], kit, environment)
        command([python, "-m", "pc.opencode", "sound-test", "--mute"], kit, environment)
        if manifest.get("variant") == "usb":
            command([python, "-c", "import serial, mpremote; from pc.buddy import UsbLink; "
                     "print('USB kit imports OK')"], kit, environment)
            command([python, "-m", "pc.deploy", "--help"], kit, environment)
            command([python, "-m", "pc.opencode", "--serial", "COM999", "--mute", "--no-notifications",
                     "--port", "0", "--duration", "1"], kit, environment)
        if shutil.which("node"):
            plugin = (root / "isolated-config/plugins/openbuddy/index.js").as_uri()
            code = "const {default:p}=await import(%s); if(p.id!=='openbuddy.events'||typeof p.setup!=='function') throw new Error('Bad bundle'); console.log('Isolated standalone plugin import OK')" % json.dumps(plugin)
            command(["node", "--input-type=module", "-e", code], kit, environment)
        print("Workplace archive verified: offline install, isolated plugin and muted bridge startup")


def main():
    parser = argparse.ArgumentParser(description="Verify the portable workplace ZIP without changing the active setup")
    parser.add_argument("archive", type=Path, nargs="?", default=Path("dist/openbuddy-workplace.zip"))
    default_root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp/opencode"
    parser.add_argument("--temp-root", type=Path, default=default_root)
    args = parser.parse_args()
    verify(args.archive.resolve(), args.temp_root)


if __name__ == "__main__":
    main()
