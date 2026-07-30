from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class PortableInstallationTests(unittest.TestCase):
    def test_install_upgrade_and_uninstall_in_isolated_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            install_dir = root / "data" / "claude-pool" / "app"
            bin_dir = root / "bin"
            home.mkdir()
            bin_dir.mkdir()
            fake_launchctl = bin_dir / "launchctl"
            fake_launchctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_launchctl.chmod(0o755)
            environment = {
                **os.environ,
                "HOME": str(home),
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "CLAUDE_POOL_LAUNCHCTL": str(fake_launchctl),
                "CLAUDE_POOL_INSTALL_DIR": str(install_dir),
                "CLAUDE_POOL_BIN_DIR": str(bin_dir),
                "CLAUDE_POOL_ALLOW_NON_MACOS": "1",
            }

            for _ in range(2):
                result = subprocess.run(
                    [str(PROJECT_ROOT / "install.sh"), "--no-shell-change"],
                    env=environment,
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            command = bin_dir / "claude-pool"
            self.assertTrue(command.is_symlink())
            self.assertTrue((install_dir / ".claude-pool-install.json").exists())
            version = subprocess.run(
                [str(command), "--version"],
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("claude-pool 0.5.0", version.stdout)
            self.assertTrue((install_dir / "LICENSE").exists())
            self.assertTrue((install_dir / "NOTICE").exists())

            uninstall = subprocess.run(
                [str(PROJECT_ROOT / "uninstall.sh"), "--keep-shell-change"],
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            self.assertFalse(os.path.lexists(command))
            self.assertFalse(install_dir.exists())

    def test_installer_refuses_unrelated_command_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            bin_dir = root / "bin"
            install_dir = root / "app"
            home.mkdir()
            bin_dir.mkdir()
            command = bin_dir / "claude-pool"
            command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            environment = {
                **os.environ,
                "HOME": str(home),
                "CLAUDE_POOL_INSTALL_DIR": str(install_dir),
                "CLAUDE_POOL_BIN_DIR": str(bin_dir),
                "CLAUDE_POOL_ALLOW_NON_MACOS": "1",
            }

            result = subprocess.run(
                [str(PROJECT_ROOT / "install.sh"), "--no-shell-change"],
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing to replace existing path", result.stderr)
            self.assertEqual(command.read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")
            self.assertFalse(install_dir.exists())


if __name__ == "__main__":
    unittest.main()
