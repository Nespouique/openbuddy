import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pc.install_plugin import PACKAGE_NAME, PLUGIN_FILES, global_config_directory, install


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "kit/plugin/openbuddy"
        self.source.mkdir(parents=True)
        (self.source / "index.js").write_text('export default {id:"openbuddy.events",setup(){}}', encoding="utf-8")
        (self.source / "package.json").write_text(json.dumps({
            "name": PACKAGE_NAME, "type": "module", "exports": {".": "./index.js"},
        }), encoding="utf-8")
        (self.source / "LICENSES.txt").write_text("Test license", encoding="utf-8")
        self.config = self.root / "config"
        self.config.mkdir()
        self.sentinel = self.config / "opencode.jsonc"
        self.sentinel.write_text('// User settings\n{"model":"user/model"}', encoding="utf-8")

    def test_install_does_not_change_config_or_other_plugins(self):
        other = self.config / "plugins/other.js"
        other.parent.mkdir()
        other.write_text("user code", encoding="utf-8")
        original = self.sentinel.read_bytes()
        result = install(self.source, self.config)
        self.assertTrue(result["changed"])
        for name in PLUGIN_FILES:
            self.assertEqual((Path(result["target"]) / name).read_bytes(), (self.source / name).read_bytes())
        self.assertEqual(self.sentinel.read_bytes(), original)
        self.assertEqual(other.read_text(), "user code")

    def test_identical_install_is_idempotent(self):
        install(self.source, self.config)
        self.assertFalse(install(self.source, self.config)["changed"])

    def test_update_requires_consent_and_backs_up_outside_plugins(self):
        result = install(self.source, self.config)
        target = Path(result["target"])
        previous = b"// User-modified old OpenBuddy code"
        (target / "index.js").write_bytes(previous)
        (target / "notes.txt").write_text("Keep this", encoding="utf-8")
        with self.assertRaises(ValueError):
            install(self.source, self.config)
        result = install(self.source, self.config, replace=True)
        backup = Path(result["backup"])
        self.assertEqual(backup.relative_to(self.config).parts[0], "openbuddy-backups")
        self.assertEqual((backup / "index.js").read_bytes(), previous)
        self.assertEqual((target / "notes.txt").read_text(), "Keep this")

    def test_unrelated_directory_is_not_replaced(self):
        target = self.config / "plugins/openbuddy"
        target.mkdir(parents=True)
        (target / "package.json").write_text('{"name":"another-plugin"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            install(self.source, self.config, replace=True)
        self.assertEqual(json.loads((target / "package.json").read_text())["name"], "another-plugin")

    def test_release_checksums_are_verified_before_copying(self):
        hashes = {"plugin/openbuddy/" + name: hashlib.sha256((self.source / name).read_bytes()).hexdigest()
                  for name in PLUGIN_FILES}
        (self.root / "kit/MANIFEST.json").write_text(json.dumps({"files": hashes}), encoding="utf-8")
        with (self.source / "index.js").open("a", encoding="utf-8") as handle:
            handle.write("\n// Corrupted transfer")
        with self.assertRaises(ValueError):
            install(self.source, self.config)
        self.assertFalse((self.config / "plugins/openbuddy").exists())

    def test_xdg_config_home_is_respected(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "custom")}):
            self.assertEqual(global_config_directory(), self.root / "custom/opencode")
