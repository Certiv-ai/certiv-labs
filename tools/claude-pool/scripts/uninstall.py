#!/usr/bin/env python3
"""Remove the installed claude-pool program while preserving user data."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

MARKER_NAME = ".claude-pool-install.json"
PATH_BLOCK_START = "# >>> claude-pool installer >>>"
PATH_BLOCK_END = "# <<< claude-pool installer <<<"


class UninstallError(RuntimeError):
    """User-facing uninstall error."""


def default_install_dir() -> Path:
    override = os.environ.get("CLAUDE_POOL_INSTALL_DIR")
    if override:
        return Path(override).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return base / "claude-pool" / "app"


def default_bin_dir() -> Path:
    override = os.environ.get("CLAUDE_POOL_BIN_DIR")
    return (
        Path(override).expanduser()
        if override
        else Path.home() / ".local" / "bin"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Uninstall claude-pool but keep account tokens and state."
    )
    parser.add_argument("--install-dir", type=Path, default=default_install_dir())
    parser.add_argument("--bin-dir", type=Path, default=default_bin_dir())
    parser.add_argument(
        "--keep-shell-change",
        action="store_true",
        help="Keep the installer-managed PATH block in ~/.zshrc.",
    )
    return parser.parse_args()


def main() -> int:
    try:
        arguments = parse_args()
        uninstall(
            install_dir=arguments.install_dir.expanduser().resolve(),
            bin_dir=arguments.bin_dir.expanduser().resolve(),
            update_shell=not arguments.keep_shell_change,
        )
        return 0
    except UninstallError as exc:
        print(f"claude-pool uninstaller: {exc}", file=sys.stderr)
        return 2


def uninstall(*, install_dir: Path, bin_dir: Path, update_shell: bool) -> None:
    marker = install_dir / MARKER_NAME
    if install_dir.exists() and not marker.exists():
        raise UninstallError(f"refusing to remove unmanaged directory: {install_dir}")

    entrypoint = bin_dir / "claude-pool"
    if entrypoint.exists():
        subprocess.run(
            [str(entrypoint), "desktop", "uninstall", "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    if os.path.lexists(entrypoint):
        if not entrypoint.is_symlink():
            raise UninstallError(f"refusing to remove existing path: {entrypoint}")
        target = entrypoint.resolve(strict=False)
        if not _is_relative_to(target, install_dir):
            raise UninstallError(f"refusing to remove unrelated symlink: {entrypoint}")
        entrypoint.unlink()

    if update_shell:
        _remove_zsh_path_block()
    if install_dir.exists():
        shutil.rmtree(install_dir)

    print("Uninstalled claude-pool.")
    print("Account tokens, configuration, and runtime state were preserved.")


def _remove_zsh_path_block() -> None:
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return
    original = zshrc.read_text(encoding="utf-8")
    start = original.find(PATH_BLOCK_START)
    if start < 0:
        return
    end = original.find(PATH_BLOCK_END, start)
    if end < 0:
        return
    end += len(PATH_BLOCK_END)
    if end < len(original) and original[end] == "\n":
        end += 1
    updated = original[:start] + original[end:]
    zshrc.write_text(updated, encoding="utf-8")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
