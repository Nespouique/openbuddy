import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.package_workplace import FILES, build


class PackageTests(unittest.TestCase):
    def test_allowlist_and_integrity_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheels = root / "wheels"
            wheels.mkdir()
            (wheels / "example-1.0-py3-none-any.whl").write_bytes(b"test wheel fixture")
            (wheels / "bridge.json").write_text('{"token":"do-not-package"}', encoding="utf-8")
            output = root / "kit.zip"
            build(output, wheels)
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertTrue(all(name.startswith("openbuddy-workplace/") for name in names))
                self.assertFalse(any("bridge.json" in name or "bonds" in name or "__pycache__" in name for name in names))
                manifest = json.loads(archive.read("openbuddy-workplace/MANIFEST.json"))
                for name, digest in manifest["files"].items():
                    self.assertEqual(hashlib.sha256(archive.read("openbuddy-workplace/" + name)).hexdigest(), digest)
                self.assertIn("openbuddy-workplace/README.md", names)
                self.assertIn("openbuddy-workplace/pc/notifications.py", names)
                self.assertEqual(len(names), len(FILES) + 3)
            with self.assertRaises(ValueError):
                build(output, wheels)

    def test_empty_wheelhouse_does_not_claim_an_offline_kit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                build(root / "kit.zip", root)
