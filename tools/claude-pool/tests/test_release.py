from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from claude_pool import __version__


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ReleaseArchiveTests(unittest.TestCase):
    def test_release_is_deterministic_and_license_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            command = [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "build_release.py"),
                "--output-dir",
                str(output),
            ]

            first = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            archive = output / f"claude-pool-{__version__}.zip"
            first_digest = hashlib.sha256(archive.read_bytes()).hexdigest()

            second = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            second_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(first_digest, second_digest)

            prefix = f"claude-pool-{__version__}/"
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                self.assertIn(prefix + "LICENSE", names)
                self.assertIn(prefix + "NOTICE", names)
                self.assertIn(prefix + "README.md", names)
                self.assertFalse(any("__pycache__" in name for name in names))
                license_text = bundle.read(prefix + "LICENSE").decode()
                self.assertIn("Apache License", license_text)
                self.assertIn("Version 2.0", license_text)


if __name__ == "__main__":
    unittest.main()
