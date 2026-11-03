"""Build an allowlisted, secret-free Windows transfer kit with offline wheels."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSIONS = ("3.11", "3.12", "3.13", "3.14")
FILES = {
    "README.md": "distribution/workplace/README.md",
    "requirements.txt": "pc/requirements.txt",
    "pc/requirements.txt": "pc/requirements.txt",
    "pc/__init__.py": "pc/__init__.py",
    "pc/buddy.py": "pc/buddy.py",
    "pc/opencode.py": "pc/opencode.py",
    "pc/notifications.py": "pc/notifications.py",
    "pc/toasts.py": "pc/toasts.py",
    "pc/install_plugin.py": "pc/install_plugin.py",
    "c6lcd/buddy_protocol.py": "c6lcd/buddy_protocol.py",
    "plugin/openbuddy/index.js": "integration/openbuddy/dist/index.js",
    "plugin/openbuddy/package.json": "integration/openbuddy/dist/package.json",
    "plugin/openbuddy/LICENSES.txt": "integration/openbuddy/dist/LICENSES.txt",
    "docs/opencode-events.md": "docs/opencode-events.md",
}


def download_wheels(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for version in PYTHON_VERSIONS:
        subprocess.run([
            sys.executable, "-m", "pip", "download", "--only-binary=:all:",
            "--platform", "win_amd64", "--implementation", "cp", "--python-version", version,
            "--abi", "cp" + version.replace(".", ""),
            "--dest", str(directory), "-r", str(ROOT / "pc/requirements.txt"),
        ], check=True)


def build(output, wheelhouse, replace=False):
    if output.exists() and not replace:
        raise ValueError("Archive exists; explicitly allow replacement")
    entries = {name: (ROOT / source).read_bytes() for name, source in FILES.items()}
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels:
        raise ValueError("Offline wheelhouse is empty; use --download-wheels")
    for path in wheels:
        entries["wheelhouse/" + path.name] = path.read_bytes()
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(entries.items())}
    manifest = {
        "package": "openbuddy-workplace", "version": "1.1.0",
        "built_utc": datetime.now(timezone.utc).isoformat(),
        "platform": "Windows x64", "offline_python_versions": list(PYTHON_VERSIONS),
        "tested_opencode": "2.2.5", "files": hashes,
    }
    entries["MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    hashes["MANIFEST.json"] = hashlib.sha256(entries["MANIFEST.json"]).hexdigest()
    entries["SHA256SUMS.txt"] = "".join("%s  %s\n" % (digest, name) for name, digest in sorted(hashes.items())).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".openbuddy-kit-", suffix=".zip", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(entries.items()):
                archive.writestr("openbuddy-workplace/" + name, data)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("Archive integrity check failed")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n", encoding="ascii")
    print("Archive:", output)
    print("Size:", output.stat().st_size, "bytes; wheels:", len(wheels))
    print("SHA-256:", digest)


def main():
    parser = argparse.ArgumentParser(description="Build the portable OpenBuddy workplace ZIP")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/openbuddy-workplace.zip")
    parser.add_argument("--wheelhouse", type=Path, default=ROOT / "build/workplace-wheelhouse")
    parser.add_argument("--download-wheels", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        if args.download_wheels:
            download_wheels(args.wheelhouse)
        build(args.output, args.wheelhouse, args.replace)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, "Error: %s\n" % error)


if __name__ == "__main__":
    main()
