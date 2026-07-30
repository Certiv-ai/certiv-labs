#!/usr/bin/env python3
"""Install a stable per-user copy of claude-pool."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKER_NAME = ".claude-pool-install.json"
PATH_BLOCK_START = "# >>> claude-pool installer >>>"
PATH_BLOCK_END = "# <<< claude-pool installer <<<"


class InstallError(RuntimeError):
    """User-facing installation error."""


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
        description="Install claude-pool for the current macOS user."
    )
    parser.add_argument("--install-dir", type=Path, default=default_install_dir())
    parser.add_argument("--bin-dir", type=Path, default=default_bin_dir())
    parser.add_argument(
        "--no-shell-change",
        action="store_true",
        help="Do not add the bin directory to ~/.zshrc when it is absent.",
    )
    return parser.parse_args()


def main() -> int:
    try:
        arguments = parse_args()
        install(
            install_dir=arguments.install_dir.expanduser().resolve(),
            bin_dir=arguments.bin_dir.expanduser().resolve(),
            update_shell=not arguments.no_shell_change,
        )
        return 0
    except InstallError as exc:
        print(f"claude-pool installer: {exc}", file=sys.stderr)
        return 2


def install(*, install_dir: Path, bin_dir: Path, update_shell: bool) -> None:
    if sys.version_info < (3, 10):
        raise InstallError(
            f"Python 3.10 or newer is required; found {sys.version.split()[0]}"
        )
    if sys.platform != "darwin" and os.environ.get("CLAUDE_POOL_ALLOW_NON_MACOS") != "1":
        raise InstallError("claude-pool currently supports macOS only")

    source_entrypoint = PROJECT_ROOT / "claude-pool"
    if not source_entrypoint.exists():
        raise InstallError(f"source entry point is missing: {source_entrypoint}")

    destination = bin_dir / "claude-pool"
    _validate_link_target(
        destination=destination,
        installed_entrypoint=install_dir / "claude-pool",
        source_entrypoint=source_entrypoint,
        install_dir=install_dir,
    )

    install_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=".claude-pool-install.",
            dir=install_dir.parent,
        )
    )
    previous = install_dir.with_name(install_dir.name + ".previous")
    moved_previous = False
    try:
        _stage_install(temporary)
        if install_dir.exists():
            if not (install_dir / MARKER_NAME).exists():
                raise InstallError(
                    f"refusing to replace unmanaged directory: {install_dir}"
                )
            if previous.exists():
                _remove_managed_tree(previous)
            os.replace(install_dir, previous)
            moved_previous = True
        os.replace(temporary, install_dir)
        if moved_previous:
            _remove_managed_tree(previous)
    except Exception:
        if moved_previous and not install_dir.exists() and previous.exists():
            os.replace(previous, install_dir)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    bin_dir.mkdir(parents=True, exist_ok=True)
    _install_link(
        destination=destination,
        installed_entrypoint=install_dir / "claude-pool",
        source_entrypoint=source_entrypoint,
        install_dir=install_dir,
    )
    shell_changed = update_shell and _ensure_zsh_path(bin_dir)

    version = _installed_version(install_dir)
    print(f"Installed claude-pool {version} at {install_dir}")
    print(f"Command: {destination}")
    if shell_changed:
        print("Added the command directory to ~/.zshrc; open a new terminal.")
    elif not _path_contains(bin_dir):
        print(f'Add this to your shell PATH: export PATH="{bin_dir}:$PATH"')
    print()
    print("Next step:")
    print(
        f"  {shlex.quote(str(destination))} "
        "setup --desktop personal work"
    )
    print()
    print("Use any aliases you like; each name is only a local label.")


def _stage_install(destination: Path) -> None:
    shutil.copy2(PROJECT_ROOT / "claude-pool", destination / "claude-pool")
    os.chmod(destination / "claude-pool", 0o755)
    shutil.copytree(
        PROJECT_ROOT / "claude_pool",
        destination / "claude_pool",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name in ("README.md", "CHANGELOG.md", "pyproject.toml", "uninstall.sh"):
        source = PROJECT_ROOT / name
        if source.exists():
            shutil.copy2(source, destination / name)
    _copy_repository_file("LICENSE", destination)
    _copy_repository_file("NOTICE", destination)
    scripts = destination / "scripts"
    scripts.mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "uninstall.py", scripts / "uninstall.py")
    marker = {
        "format": 1,
        "version": _source_version(),
        "source": str(PROJECT_ROOT),
    }
    (destination / MARKER_NAME).write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(destination / MARKER_NAME, 0o600)


def _copy_repository_file(name: str, destination: Path) -> None:
    local = PROJECT_ROOT / name
    repository = PROJECT_ROOT.parents[1] / (
        "LICENSE.md" if name == "LICENSE" else name
    )
    for source in (local, repository):
        if source.exists():
            shutil.copy2(source, destination / name)
            return
    raise InstallError(f"repository {name} file is missing")


def _install_link(
    *,
    destination: Path,
    installed_entrypoint: Path,
    source_entrypoint: Path,
    install_dir: Path,
) -> None:
    _validate_link_target(
        destination=destination,
        installed_entrypoint=installed_entrypoint,
        source_entrypoint=source_entrypoint,
        install_dir=install_dir,
    )
    if os.path.lexists(destination):
        destination.unlink()
    destination.symlink_to(installed_entrypoint)


def _validate_link_target(
    *,
    destination: Path,
    installed_entrypoint: Path,
    source_entrypoint: Path,
    install_dir: Path,
) -> None:
    if not os.path.lexists(destination):
        return
    if not destination.is_symlink():
        raise InstallError(f"refusing to replace existing path: {destination}")
    current = destination.resolve(strict=False)
    allowed = (
        current == source_entrypoint.resolve()
        or current == installed_entrypoint
        or _is_relative_to(current, install_dir)
    )
    if not allowed:
        raise InstallError(
            f"refusing to replace symlink owned by another tool: {destination}"
        )


def _ensure_zsh_path(bin_dir: Path) -> bool:
    if _path_contains(bin_dir):
        return False
    zshrc = Path.home() / ".zshrc"
    existing = zshrc.read_text(encoding="utf-8") if zshrc.exists() else ""
    if PATH_BLOCK_START in existing:
        return False
    block = (
        f"{PATH_BLOCK_START}\n"
        f'export PATH="{bin_dir}:$PATH"\n'
        f"{PATH_BLOCK_END}\n"
    )
    separator = "" if not existing or existing.endswith("\n") else "\n"
    with zshrc.open("a", encoding="utf-8") as handle:
        handle.write(separator + block)
    return True


def _path_contains(directory: Path) -> bool:
    return any(
        Path(value).expanduser().resolve() == directory
        for value in os.environ.get("PATH", "").split(os.pathsep)
        if value
    )


def _source_version() -> str:
    namespace: dict[str, object] = {}
    source = PROJECT_ROOT / "claude_pool" / "__init__.py"
    exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
    return str(namespace["__version__"])


def _installed_version(install_dir: Path) -> str:
    marker = json.loads((install_dir / MARKER_NAME).read_text(encoding="utf-8"))
    return str(marker["version"])


def _remove_managed_tree(path: Path) -> None:
    if not (path / MARKER_NAME).exists():
        raise InstallError(f"refusing to remove unmanaged directory: {path}")
    shutil.rmtree(path)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
