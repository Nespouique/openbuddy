from pathlib import Path
import tempfile
import unittest

from pc.install_alias import (
    BASH_LOADER_END,
    BASH_LOADER_START,
    END_MARKER,
    START_MARKER,
    install_aliases,
    render_bash_block,
    render_powershell_block,
    uninstall_aliases,
)


class InstallAliasTests(unittest.TestCase):
    def test_rendered_blocks_are_ascii_and_escape_quotes(self):
        repo = Path("/opt/dev's/openbuddy")
        py = Path("/opt/dev's/openbuddy/.venv/bin/python")
        ps = render_powershell_block(repo, py)
        sh = render_bash_block(repo, py)

        self.assertTrue(ps.isascii())
        self.assertTrue(sh.isascii())
        self.assertIn("dev''s", ps)
        self.assertIn("dev'\\''s", sh)
        self.assertIn("function opencode {", ps)
        self.assertIn("opencode() {", sh)
        self.assertIn("--pet-overlay", ps)
        self.assertIn("--pet-overlay", sh)

    def test_install_update_and_uninstall_preserve_existing_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ps_profile = root / "PowerShell/Microsoft.PowerShell_profile.ps1"
            ps_profile.parent.mkdir(parents=True)
            ps_profile.write_bytes(b"# user ps config\r\n$env:FOO = 'bar'\r\n")

            bashrc = root / ".bashrc"
            bashrc.write_bytes(b"# user bash config\nexport FOO=bar\n")
            bash_profile = root / ".bash_profile"

            repo_a = root / "repo-a"
            repo_a.mkdir()
            py_a = root / "python-a"

            res1 = install_aliases(
                shell="all",
                repo_root=repo_a,
                python_exe=py_a,
                powershell_profiles=[ps_profile],
                bash_rc=bashrc,
                bash_profile=bash_profile,
            )
            self.assertTrue(all(item["changed"] for item in res1))

            ps_text = ps_profile.read_bytes().decode("utf-8")
            self.assertIn("$env:FOO = 'bar'", ps_text)
            self.assertIn(START_MARKER, ps_text)
            self.assertIn(END_MARKER, ps_text)
            self.assertIn("\r\n", ps_text)

            rc_text = bashrc.read_text(encoding="utf-8")
            self.assertIn("export FOO=bar", rc_text)
            self.assertIn(START_MARKER, rc_text)

            bp_text = bash_profile.read_text(encoding="utf-8")
            self.assertIn(BASH_LOADER_START, bp_text)
            self.assertIn(BASH_LOADER_END, bp_text)

            # Second install with same paths is idempotent.
            res2 = install_aliases(
                shell="all",
                repo_root=repo_a,
                python_exe=py_a,
                powershell_profiles=[ps_profile],
                bash_rc=bashrc,
                bash_profile=bash_profile,
            )
            self.assertTrue(all(not item["changed"] for item in res2))

            # Updating to a new repo replaces the block in place without duplicating markers.
            repo_b = root / "repo-b"
            repo_b.mkdir()
            res3 = install_aliases(
                shell="all",
                repo_root=repo_b,
                python_exe=py_a,
                powershell_profiles=[ps_profile],
                bash_rc=bashrc,
                bash_profile=bash_profile,
            )
            changed_paths = {Path(item["path"]).resolve() for item in res3 if item["changed"]}
            self.assertEqual(changed_paths, {ps_profile.resolve(), bashrc.resolve()})
            self.assertEqual(ps_profile.read_text(encoding="utf-8").count(START_MARKER), 1)
            self.assertEqual(bashrc.read_text(encoding="utf-8").count(START_MARKER), 1)
            self.assertIn("repo-b", bashrc.read_text(encoding="utf-8"))

            # Uninstall removes only the managed blocks.
            res4 = uninstall_aliases(
                shell="all",
                powershell_profiles=[ps_profile],
                bash_rc=bashrc,
                bash_profile=bash_profile,
            )
            self.assertTrue(all(item["changed"] for item in res4))
            self.assertNotIn(START_MARKER, ps_profile.read_text(encoding="utf-8"))
            self.assertIn("$env:FOO = 'bar'", ps_profile.read_text(encoding="utf-8"))
            self.assertNotIn(START_MARKER, bashrc.read_text(encoding="utf-8"))
            self.assertIn("export FOO=bar", bashrc.read_text(encoding="utf-8"))
            self.assertNotIn(BASH_LOADER_START, bash_profile.read_text(encoding="utf-8"))

    def test_existing_bash_profile_sourcing_bashrc_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bashrc = root / ".bashrc"
            bash_profile = root / ".bash_profile"
            original = "test -f ~/.bashrc && . ~/.bashrc\n"
            bash_profile.write_text(original, encoding="utf-8")

            install_aliases(
                shell="bash",
                repo_root=root,
                python_exe=root / "python",
                bash_rc=bashrc,
                bash_profile=bash_profile,
            )
            self.assertEqual(bash_profile.read_text(encoding="utf-8"), original)

    def test_symlinked_profile_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real.bashrc"
            real.write_text("# real\n", encoding="utf-8")
            link = root / "link.bashrc"
            try:
                link.symlink_to(real)
            except OSError:
                self.skipTest("Symlinks require elevated privileges in this environment")
            with self.assertRaises(ValueError):
                install_aliases(
                    shell="bash",
                    repo_root=root,
                    python_exe=root / "python",
                    bash_rc=link,
                    bash_profile=root / ".bash_profile",
                )
