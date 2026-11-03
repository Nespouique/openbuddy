"""Install the bundled plugin without editing OpenCode config or other plugins."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

PLUGIN_FILES = ("index.js", "package.json", "LICENSES.txt")
PACKAGE_NAME = "@openbuddy/local-plugin"


def global_config_directory():
    root = Path(os.environ["XDG_CONFIG_HOME"]) if os.environ.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return root / "opencode"


def default_source():
    root = Path(__file__).resolve().parents[1]
    release = root / "plugin/openbuddy"
    development = root / "integration/openbuddy/dist"
    return development if development.is_dir() else release


def validate_source(source):
    for name in PLUGIN_FILES:
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or unsafe plugin file: " + name)
    metadata = json.loads((source / "package.json").read_text(encoding="utf-8"))
    if (not isinstance(metadata, dict) or metadata.get("name") != PACKAGE_NAME
            or not isinstance(metadata.get("exports"), dict)
            or metadata["exports"].get(".") != "./index.js"):
        raise ValueError("Unexpected plugin package")
    if b"openbuddy.events" not in (source / "index.js").read_bytes():
        raise ValueError("Unexpected plugin bundle")
    manifest_path = source.parent.parent / "MANIFEST.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name in PLUGIN_FILES:
            expected = manifest["files"]["plugin/openbuddy/" + name]
            if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
                raise ValueError("Plugin checksum mismatch: " + name)


def install(source, config_directory, replace=False):
    source = source.expanduser().resolve()
    config_directory = config_directory.expanduser().resolve()
    validate_source(source)
    target = config_directory / "plugins/openbuddy"
    backup = None
    if target.exists() or target.is_symlink():
        if not target.is_dir() or target.is_symlink():
            raise ValueError("Refusing to overwrite an unrelated file or symlink")
        for name in PLUGIN_FILES:
            if (target / name).is_symlink():
                raise ValueError("Refusing to overwrite a plugin symlink")
        if all((target / name).is_file() and (target / name).read_bytes() == (source / name).read_bytes()
               for name in PLUGIN_FILES):
            return {"target": str(target), "changed": False, "backup": None}
        if not replace:
            raise ValueError("Plugin directory already exists; use --replace for an OpenBuddy update")
        package = target / "package.json"
        existing = json.loads(package.read_text(encoding="utf-8")) if package.is_file() else None
        if not isinstance(existing, dict) or existing.get("name") != PACKAGE_NAME:
            raise ValueError("Existing directory is not an OpenBuddy plugin; refusing replacement")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        backup = config_directory / "openbuddy-backups" / stamp
        backup.mkdir(parents=True)
        for name in PLUGIN_FILES:
            if (target / name).is_file():
                shutil.copy2(target / name, backup / name)
    target.mkdir(parents=True, exist_ok=True)
    # Publish complete files atomically, and package metadata last.
    for name in ("LICENSES.txt", "index.js", "package.json"):
        descriptor, temporary_name = tempfile.mkstemp(prefix=".openbuddy-", suffix=".tmp", dir=target)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write((source / name).read_bytes())
            os.replace(temporary, target / name)
        finally:
            temporary.unlink(missing_ok=True)
    return {"target": str(target), "changed": True, "backup": str(backup) if backup else None}


def main():
    parser = argparse.ArgumentParser(description="Install the standalone OpenBuddy plugin without administrator rights")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--project", type=Path, help="Install only in this project's .opencode directory")
    scope.add_argument("--config-dir", type=Path, help="Use a custom OpenCode global config directory")
    parser.add_argument("--replace", action="store_true", help="Back up and replace an existing OpenBuddy plugin")
    parser.add_argument("--source", type=Path, default=default_source())
    args = parser.parse_args()
    config = args.project / ".opencode" if args.project else args.config_dir or global_config_directory()
    try:
        result = install(args.source, config, args.replace)
        print("Plugin installed:" if result["changed"] else "Plugin already up to date:", result["target"])
        if result["backup"]:
            print("Previous files backed up outside the plugins directory:", result["backup"])
        print("In OpenCode, use /reload if the plugin does not appear automatically.")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, "Error: %s\n" % error)


if __name__ == "__main__":
    main()
