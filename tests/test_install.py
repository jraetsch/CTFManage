"""install.sh must be additive and reversible: it should never remove or
overwrite anything it did not create itself, and --uninstall must never touch
$CTF_ROOT. Run with:
    python3 -m unittest discover -s tests -t . -v
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


class InstallTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.install_dir = self.home / ".local" / "share" / "ctftool"
        self.manifest = self.home / ".local" / "share" / "ctftool.manifest"
        self.launcher = self.home / ".local" / "bin" / "ctf"
        self.config_dir = self.home / ".config" / "ctftool"

    def run_install(self, *args, input_text=None):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        return subprocess.run(
            [str(INSTALL_SH), *args],
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=30,
        )


class TestCopyInstall(InstallTestCase):
    def test_copies_package_and_writes_launcher(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.install_dir / "ctf" / "__init__.py").exists())
        self.assertFalse(self.install_dir.is_symlink())
        self.assertTrue(self.launcher.exists())
        self.assertTrue(os.access(self.launcher, os.X_OK))

    def test_launcher_runs(self):
        self.run_install()
        result = subprocess.run(
            [str(self.launcher), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rerun_is_idempotent(self):
        self.assertEqual(self.run_install().returncode, 0)
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.install_dir / "ctf" / "__init__.py").exists())

    def test_refuses_to_clobber_unmanaged_install_dir(self):
        self.install_dir.mkdir(parents=True)
        sentinel = self.install_dir / "not_ours.txt"
        sentinel.write_text("do not touch")

        result = self.run_install()

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(sentinel.exists())

    def test_refuses_to_clobber_unmanaged_launcher(self):
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text("#!/bin/sh\necho not ctftool\n")
        self.launcher.chmod(0o755)

        result = self.run_install()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not ctftool", self.launcher.read_text())


class TestSymlinkInstall(InstallTestCase):
    def test_links_to_repo_checkout(self):
        result = self.run_install("--symlink")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.install_dir.is_symlink())
        self.assertEqual(self.install_dir.resolve(), REPO_ROOT.resolve())

    def test_switching_modes_replaces_previous_install(self):
        self.assertEqual(self.run_install().returncode, 0)
        self.assertFalse(self.install_dir.is_symlink())

        self.assertEqual(self.run_install("--symlink").returncode, 0)
        self.assertTrue(self.install_dir.is_symlink())

        self.assertEqual(self.run_install().returncode, 0)
        self.assertFalse(self.install_dir.is_symlink())


class TestUninstall(InstallTestCase):
    def test_removes_launcher_and_install_dir_but_keeps_config_by_default_noninteractive(self):
        self.run_install()
        self.config_dir.mkdir(parents=True)
        (self.config_dir / "config.toml").write_text("root = '/x'\n")

        result = self.run_install("--uninstall", input_text="")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.install_dir.exists())
        self.assertFalse(self.launcher.exists())
        self.assertFalse(self.manifest.exists())
        self.assertTrue(self.config_dir.exists())

    def test_purge_config_removes_it(self):
        self.run_install()
        self.config_dir.mkdir(parents=True)

        result = self.run_install("--uninstall", "--purge-config")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.config_dir.exists())

    def test_keep_config_flag_is_respected(self):
        self.run_install()
        self.config_dir.mkdir(parents=True)

        result = self.run_install("--uninstall", "--keep-config")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.config_dir.exists())

    def test_leaves_unmanaged_install_dir_alone(self):
        self.install_dir.mkdir(parents=True)
        sentinel = self.install_dir / "unrelated.txt"
        sentinel.write_text("keep me")

        result = self.run_install("--uninstall")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(sentinel.exists())

    def test_leaves_unmanaged_launcher_alone(self):
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text("#!/bin/sh\necho keep me\n")
        self.launcher.chmod(0o755)

        result = self.run_install("--uninstall")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("keep me", self.launcher.read_text())

    def test_never_mentions_deleting_ctf_root(self):
        self.run_install()
        result = self.run_install("--uninstall", "--purge-config")
        self.assertIn("were not touched", result.stdout)


@unittest.skipUnless(sys.platform.startswith("linux"), "install.sh targets Linux/zsh setups")
class TestHelp(InstallTestCase):
    def test_help_exits_zero(self):
        result = self.run_install("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--symlink", result.stdout)
        self.assertIn("--uninstall", result.stdout)


if __name__ == "__main__":
    unittest.main()
