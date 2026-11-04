import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from pc.deploy import FILES as DEPLOY_FILES
from tools.package_workplace import FIRMWARE_FILES, VERSION, build, files_for, package_name


class PackageTests(unittest.TestCase):
    def build_kit(self, variant):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        wheels = root / "wheels"
        wheels.mkdir()
        (wheels / "example-1.0-py3-none-any.whl").write_bytes(b"test wheel fixture")
        (wheels / "bridge.json").write_text('{"token":"do-not-package"}', encoding="utf-8")
        output = root / "kit.zip"
        build(output, wheels, variant=variant)
        with self.assertRaises(ValueError):
            build(output, wheels, variant=variant)
        return zipfile.ZipFile(output)

    def check_common(self, archive, variant):
        prefix = package_name(variant) + "/"
        names = archive.namelist()
        self.assertTrue(all(name.startswith(prefix) for name in names))
        self.assertFalse(any("bridge.json" in name or "bonds" in name or "__pycache__" in name for name in names))
        manifest = json.loads(archive.read(prefix + "MANIFEST.json"))
        self.assertEqual((manifest["package"], manifest["variant"], manifest["version"]),
                         (package_name(variant), variant, VERSION))
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256(archive.read(prefix + name)).hexdigest(), digest)
        self.assertIn(prefix + "README.md", names)
        self.assertIn(prefix + "pc/notifications.py", names)
        self.assertEqual(len(names), len(files_for(variant)) + 3)
        return prefix, names

    def test_bluetooth_kit_allowlist_and_integrity_manifest(self):
        with self.build_kit("bluetooth") as archive:
            prefix, names = self.check_common(archive, "bluetooth")
            self.assertFalse(any(name.startswith(prefix + "c6lcd/buddy_usb") or name.endswith("deploy.py")
                                 for name in names))
            self.assertIn(b"pyserial", archive.read(prefix + "requirements.txt"))
            self.assertNotIn(b"mpremote", archive.read(prefix + "requirements.txt"))

    def test_usb_kit_contains_firmware_deploy_tool_and_usb_readme(self):
        with self.build_kit("usb") as archive:
            prefix, names = self.check_common(archive, "usb")
            for name in FIRMWARE_FILES:
                self.assertIn(prefix + "c6lcd/" + name, names)
            self.assertIn(prefix + "pc/deploy.py", names)
            self.assertIn(prefix + "tools/openbuddy-usb.cmd", names)
            self.assertIn(prefix + "tools/openbuddy-usb-stop.cmd", names)
            requirements = archive.read(prefix + "requirements.txt")
            self.assertIn(b"mpremote", requirements)
            self.assertIn(b"pyserial", requirements)
            self.assertEqual(requirements, archive.read(prefix + "pc/requirements.txt"))
            self.assertIn(b"openbuddy-workplace-usb", archive.read(prefix + "README.md"))
            self.assertFalse(any(name.endswith(".sd") or "pets" in name for name in names))

    def test_firmware_list_matches_the_deploy_tool(self):
        self.assertEqual(tuple(FIRMWARE_FILES), tuple(DEPLOY_FILES))

    def test_unknown_variant_is_refused(self):
        with self.assertRaises(ValueError):
            files_for("wifi")

    def test_empty_wheelhouse_does_not_claim_an_offline_kit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                build(root / "kit.zip", root)
