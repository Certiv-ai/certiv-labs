"""Local-only Claude Desktop worker integration."""

from __future__ import annotations

import json
import os
import plistlib
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Sequence

from .keychain import MacOSKeychain
from .storage import PoolStore, StoreError, default_state_dir

SHIM_MARKER = "claude-pool desktop shim v1"
LOCAL_BINARY_ENV = "CLAUDE_CODE_LOCAL_BINARY"
LAUNCH_AGENT_LABEL = "com.codex.claude-pool.desktop-environment"
LEGACY_LAUNCH_AGENT_LABEL = "com.codex.claude-pool.desktop-repair"
VERSION_RE = re.compile(r"^\d+(?:\.\d+)+$")


class DesktopError(RuntimeError):
    """Raised for a Desktop integration problem."""


def default_desktop_root() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude-code"
    )


def worker_paths(root: Path | None = None) -> list[Path]:
    base = root or default_desktop_root()
    return sorted(
        (
            path
            for path in base.glob("*/claude.app/Contents/MacOS/claude")
            if VERSION_RE.fullmatch(path.parts[-5])
        ),
        key=lambda path: _version_key(path.parts[-5]),
    )


def is_shim(path: Path) -> bool:
    try:
        return SHIM_MARKER.encode() in path.read_bytes()[:4_096]
    except OSError:
        return False


def default_shim_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return base / "claude-pool" / "desktop" / "claude"


def install_desktop(
    *,
    source_root: Path,
    python_executable: Path,
    quiet: bool = False,
    root: Path | None = None,
    install_agent: bool = True,
) -> Path:
    workers = worker_paths(root)
    if not workers:
        raise DesktopError(
            f"Claude Desktop's local worker was not found under "
            f"{root or default_desktop_root()}"
        )

    shim = default_shim_path()
    shim.parent.mkdir(parents=True, exist_ok=True)
    _write_shim(
        shim,
        _shim_source(
            source_root=source_root,
            python_executable=python_executable,
        ),
    )
    if install_agent:
        _set_local_binary_environment(shim)
        _install_launch_agent(shim)
    if not quiet:
        active = _desktop_process_uses(shim)
        if active:
            print("Claude Desktop local routing is active.")
        else:
            print(
                "Installed Claude Desktop local routing. Restart Claude Desktop "
                "when you are ready to activate it."
            )
    return shim


def repair_desktop(
    *,
    source_root: Path,
    python_executable: Path,
    quiet: bool = False,
    root: Path | None = None,
) -> Path:
    return install_desktop(
        source_root=source_root,
        python_executable=python_executable,
        quiet=quiet,
        root=root,
        install_agent=True,
    )


def uninstall_desktop(
    *,
    quiet: bool = False,
    root: Path | None = None,
    remove_agent: bool = True,
) -> list[Path]:
    removed = []
    shim = default_shim_path()
    if remove_agent:
        _unset_local_binary_environment()
        _remove_launch_agent()
    if shim.exists():
        shim.unlink()
        removed.append(shim)
        try:
            shim.parent.rmdir()
        except OSError:
            pass
    if not quiet:
        if removed:
            print(
                "Removed Claude Desktop local routing. Restart Claude Desktop "
                "to return a currently running app to its normal worker."
            )
        else:
            print("Claude Desktop routing was not installed.")
    return removed


def desktop_status(root: Path | None = None) -> int:
    workers = worker_paths(root)
    if not workers:
        print("Claude Desktop local worker: not found")
        return 1
    print(f"Claude Code signed worker: {workers[-1].parts[-5]}")
    shim = default_shim_path()
    environment_value = _get_local_binary_environment()
    agent = _launch_agent_path()
    installed = (
        is_shim(shim)
        and environment_value == str(shim)
        and agent.exists()
    )
    if not installed:
        print("Local routing override: not installed")
        return 1
    active = _desktop_process_uses(shim)
    if active is True:
        state = "active"
    elif active is False:
        state = "installed; restart Claude Desktop to activate"
    else:
        state = "installed; activates the next time Claude Desktop opens"
    print(f"Local routing override: {state}")
    return 0


def worker_main(
    arguments: Sequence[str],
    *,
    executable: Path | None = None,
) -> int:
    worker = executable or Path(sys.argv[0])
    originals = [
        path
        for path in worker_paths()
        if path.resolve() != worker.resolve()
    ]
    if not originals:
        _log("Claude Desktop's signed local worker is missing")
        return 127
    original = originals[-1]

    environment = os.environ.copy()
    account: str | None = None
    try:
        host_session_id = environment.get("CLAUDE_CODE_HOST_SESSION_ID", "")
        store = PoolStore()
        keychain = MacOSKeychain()
        token_accounts = {
            name
            for name in store.account_names(enabled_only=False)
            if keychain.has_token(name)
        }
        account = store.claim_desktop(
            host_session_id=host_session_id,
            token_accounts=token_accounts,
        )
        token = keychain.get(account)
        environment = _desktop_environment(environment, token, account)
        _log(f"session {host_session_id} assigned to {account}")
    except Exception as exc:
        # Fail open: the original Desktop account remains usable if pool state
        # or Keychain access is temporarily unavailable.
        _log(f"pool selection failed; using Desktop account: {exc}")

    child = subprocess.Popen(
        [str(original), *arguments],
        env=environment,
        stdin=None,
        stdout=subprocess.PIPE,
        stderr=None,
    )
    previous_handlers = _forward_signals(child)
    try:
        if child.stdout is not None:
            _forward_output(child.stdout, account)
        return child.wait()
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
        try:
            PoolStore().release()
        except StoreError:
            pass


def _forward_output(stream: BinaryIO, account: str | None) -> None:
    output = sys.stdout.buffer
    for line in iter(stream.readline, b""):
        output.write(line)
        output.flush()
        if account is not None:
            try:
                _observe_event(line, account)
            except Exception as exc:
                # Telemetry improves scheduling but must never interrupt the
                # Desktop protocol or the user's active Claude process.
                _log(f"telemetry observation failed for {account}: {exc}")


def _observe_event(line: bytes, account: str) -> None:
    try:
        document = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    store = PoolStore()
    if document.get("type") == "result":
        units = document.get("total_cost_usd")
        if not isinstance(units, (int, float)) or units <= 0:
            usage = document.get("usage", {})
            units = sum(
                float(usage.get(key, 0) or 0)
                for key in (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                    "output_tokens",
                )
                if isinstance(usage.get(key, 0), (int, float))
            )
        store.record_local_usage(account=account, units=float(units or 0))
        return

    if document.get("type") != "rate_limit_event":
        return
    info = document.get("rate_limit_info", {})
    reset_value = info.get("resetsAt")
    resets_at = None
    if isinstance(reset_value, (int, float)):
        resets_at = datetime.fromtimestamp(reset_value, tz=timezone.utc)
    utilization = info.get("utilization")
    if not isinstance(utilization, (int, float)):
        utilization = None
    status = str(info.get("status", "")).lower()
    store.record_native_rate_limit(
        account=account,
        window=str(info.get("rateLimitType", "")),
        utilization=utilization,
        resets_at=resets_at,
        limited=status in {"blocked", "exceeded", "rejected"},
    )


def _desktop_environment(
    environment: dict[str, str],
    token: str,
    account: str,
) -> dict[str, str]:
    result = environment.copy()
    for variable in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
        "CLAUDE_CODE_OAUTH_SCOPES",
        "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH",
        "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
        "CLAUDE_CODE_SUBSCRIPTION_TYPE",
        "CLAUDE_CODE_RATE_LIMIT_TIER",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        result.pop(variable, None)
    result["CLAUDE_CODE_OAUTH_TOKEN"] = token
    result["CLAUDE_POOL_ACCOUNT"] = account
    return result


def _write_shim(worker: Path, shim: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".claude-pool-shim.",
        dir=worker.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(shim)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o755)
        os.replace(temporary, worker)
    finally:
        if temporary.exists():
            temporary.unlink()


def _shim_source(*, source_root: Path, python_executable: Path) -> str:
    return (
        f"#!{python_executable}\n"
        f'"""{SHIM_MARKER}."""\n'
        "import pathlib\n"
        "import sys\n"
        f"sys.path.insert(0, {str(source_root)!r})\n"
        "from claude_pool.desktop import worker_main\n"
        "raise SystemExit(worker_main(sys.argv[1:], "
        "executable=pathlib.Path(sys.argv[0])))\n"
    )


def _forward_signals(child: subprocess.Popen[bytes]) -> dict[int, object]:
    previous = {}

    def forward(signum: int, _frame: object) -> None:
        try:
            child.send_signal(signum)
        except ProcessLookupError:
            pass

    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, forward)
    return previous


def _version_key(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return (0,)


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def _launchctl_path() -> str:
    return os.environ.get("CLAUDE_POOL_LAUNCHCTL", "/bin/launchctl")


def _install_launch_agent(shim: Path) -> None:
    path = _launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    arguments = [
        _launchctl_path(),
        "setenv",
        LOCAL_BINARY_ENV,
        str(shim),
    ]
    state_dir = default_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "StandardOutPath": str(state_dir / "desktop-environment.log"),
        "StandardErrorPath": str(state_dir / "desktop-environment.log"),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            plistlib.dump(document, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

    domain = f"gui/{os.getuid()}"
    subprocess.run(
        [_launchctl_path(), "bootout", domain, str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    result = subprocess.run(
        [_launchctl_path(), "bootstrap", domain, str(path)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise DesktopError(
            f"Desktop environment agent could not be loaded: "
            f"{result.stderr.strip() or 'launchctl failed'}"
        )


def _remove_launch_agent() -> None:
    for label in (LAUNCH_AGENT_LABEL, LEGACY_LAUNCH_AGENT_LABEL):
        path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if not path.exists():
            continue
        subprocess.run(
            [_launchctl_path(), "bootout", f"gui/{os.getuid()}", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        path.unlink()


def _set_local_binary_environment(shim: Path) -> None:
    result = subprocess.run(
        [_launchctl_path(), "setenv", LOCAL_BINARY_ENV, str(shim)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise DesktopError(
            f"Could not set Claude Desktop's local worker override: "
            f"{result.stderr.strip() or 'launchctl failed'}"
        )


def _unset_local_binary_environment() -> None:
    subprocess.run(
        [_launchctl_path(), "unsetenv", LOCAL_BINARY_ENV],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _get_local_binary_environment() -> str | None:
    result = subprocess.run(
        [_launchctl_path(), "getenv", LOCAL_BINARY_ENV],
        capture_output=True,
        check=False,
        text=True,
    )
    value = result.stdout.strip()
    return value or None


def _desktop_process_uses(shim: Path) -> bool | None:
    listing = subprocess.run(
        ["ps", "-ww", "-axo", "pid=,comm="],
        capture_output=True,
        check=False,
        text=True,
    ).stdout
    pids = []
    for line in listing.splitlines():
        if line.rstrip().endswith("/Claude.app/Contents/MacOS/Claude"):
            pids.append(line.strip().split(None, 1)[0])
    if not pids:
        return None
    expected = re.compile(
        rf"(?:^|\s){re.escape(LOCAL_BINARY_ENV)}={re.escape(str(shim))}(?:\s|$)"
    )
    for pid in pids:
        command = subprocess.run(
            ["ps", "eww", "-p", pid, "-o", "command="],
            capture_output=True,
            check=False,
            text=True,
        ).stdout
        if expected.search(command):
            return True
    return False


def _log(message: str) -> None:
    path = default_state_dir() / "desktop.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            timestamp = datetime.now(timezone.utc).isoformat()
            handle.write(f"{timestamp} {message}\n")
    except OSError:
        pass
