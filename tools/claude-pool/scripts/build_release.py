#!/usr/bin/env python3
"""Build a self-contained, shareable claude-pool source archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELEASE_ITEMS = (
    ".gitignore",
    "CHANGELOG.md",
    "README.md",
    "claude-pool",
    "claude_pool",
    "install.sh",
    "pyproject.toml",
    "scripts",
    "tests",
    "uninstall.sh",
)
EXCLUDED_NAMES = {"__pycache__", ".DS_Store", "dist"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
CREDENTIAL_PATTERNS = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\blin_api_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
)


class ReleaseError(RuntimeError):
    """A release archive cannot be safely created."""


@dataclass(frozen=True)
class ReleaseFile:
    source: Path
    archive_path: Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "dist",
    )
    arguments = parser.parse_args()

    version = _source_version()
    output_dir = arguments.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    files = list(_release_files())
    _validate_release(files)

    archives = (
        output_dir / f"claude-pool-{version}.zip",
        output_dir / f"claude-pool-{version}.tar.gz",
    )
    _write_zip(archives[0], version, files)
    _write_tarball(archives[1], version, files)
    for archive in archives:
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksum = archive.with_name(archive.name + ".sha256")
        checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        print(f"Built {archive}")
        print(f"SHA-256: {digest}")
    return 0


def _release_files() -> Iterator[ReleaseFile]:
    for item in RELEASE_ITEMS:
        source = PROJECT_ROOT / item
        if not source.exists():
            raise ReleaseError(f"required release item is missing: {item}")
        if source.is_file():
            yield ReleaseFile(source, source.relative_to(PROJECT_ROOT))
            continue
        for path in sorted(source.rglob("*")):
            relative_parts = path.relative_to(PROJECT_ROOT).parts
            if path.is_file() and not (
                any(part in EXCLUDED_NAMES for part in relative_parts)
                or path.suffix in EXCLUDED_SUFFIXES
            ):
                yield ReleaseFile(path, path.relative_to(PROJECT_ROOT))
    yield ReleaseFile(_repository_file("LICENSE"), Path("LICENSE"))
    yield ReleaseFile(_repository_file("NOTICE"), Path("NOTICE"))


def _validate_release(files: list[ReleaseFile]) -> None:
    macos_home_prefix = "/" + "Users/"
    for item in files:
        try:
            content = item.source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if macos_home_prefix in content:
            raise ReleaseError(
                f"machine-specific home path found in {item.archive_path}"
            )
        if any(pattern.search(content) for pattern in CREDENTIAL_PATTERNS):
            raise ReleaseError(
                f"possible credential found in {item.archive_path}"
            )


def _write_zip(archive: Path, version: str, files: list[ReleaseFile]) -> None:
    prefix = Path(f"claude-pool-{version}")
    with zipfile.ZipFile(
        archive,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as bundle:
        for item in files:
            info = zipfile.ZipInfo(str(prefix / item.archive_path))
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = 0o755 if os.access(item.source, os.X_OK) else 0o644
            info.external_attr = mode << 16
            bundle.writestr(info, item.source.read_bytes(), compresslevel=9)


def _write_tarball(
    archive: Path,
    version: str,
    files: list[ReleaseFile],
) -> None:
    prefix = Path(f"claude-pool-{version}")
    with archive.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as tar:
                for item in files:
                    data = item.source.read_bytes()
                    info = tarfile.TarInfo(str(prefix / item.archive_path))
                    info.size = len(data)
                    info.mode = (
                        0o755 if os.access(item.source, os.X_OK) else 0o644
                    )
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    tar.addfile(info, io.BytesIO(data))


def _repository_file(name: str) -> Path:
    local = PROJECT_ROOT / name
    repository = PROJECT_ROOT.parents[1] / (
        "LICENSE.md" if name == "LICENSE" else name
    )
    for candidate in (local, repository):
        if candidate.exists():
            return candidate
    raise ReleaseError(f"repository {name} file is missing")


def _source_version() -> str:
    namespace: dict[str, object] = {}
    source = PROJECT_ROOT / "claude_pool" / "__init__.py"
    exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
    return str(namespace["__version__"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        raise SystemExit(f"claude-pool release: {exc}") from exc
