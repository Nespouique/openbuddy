"""Keep the public source allowlist complete without exporting private notes."""

from pathlib import Path
import unittest

from tools.prepare_publication import MEDIA_FILES, ROOT, files, prepare


class PublicationTests(unittest.TestCase):
    def test_usb_build_dependencies_and_launchers_are_published(self):
        selected = files()
        for name in ("distribution/workplace/requirements-usb.txt",
                     "tools/openbuddy-usb.cmd", "tools/openbuddy-usb-stop.cmd",
                     "c6lcd/buddy_usb.py"):
            self.assertIn(name, selected)

    def test_private_notes_and_device_keys_are_not_selected(self):
        selected = files()
        self.assertNotIn("docs/hardware-notes.md", selected)
        self.assertNotIn("openbuddy-bonds.json", selected)
        self.assertEqual(selected["AGENTS.md"], selected["distribution/public/AGENTS.md"])

    def test_readme_media_is_explicit_and_never_treated_as_source_text(self):
        selected = files()
        self.assertEqual(MEDIA_FILES, ("docs/media/openbuddy-demo.gif", "docs/media/openbuddy-demo.png"))
        for name in MEDIA_FILES:
            self.assertTrue((ROOT / name).is_file())
            self.assertNotIn(name, selected)
            self.assertLess((ROOT / name).stat().st_size, 2 * 1024 * 1024)
        font = selected["tools/assets/font_petme128_8x8.h"].read_text(encoding="utf-8")
        self.assertIn("The MIT License (MIT)", font)

    def test_every_usb_kit_source_is_available_in_the_public_payload(self):
        from tools.package_workplace import ROOT, files_for

        payload = {item["path"] for item in prepare()}
        compiled = "integration/openbuddy/dist/"
        for source in files_for("usb").values():
            public = "plugin/openbuddy/" + Path(source).name if source.startswith(compiled) else source
            self.assertIn(public, payload, source)
            self.assertTrue((ROOT / source).is_file())
