"""Command-line interface for claude-pool."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .desktop import (
    DesktopError,
    desktop_status,
    install_desktop,
    repair_desktop,
    uninstall_desktop,
)
from .keychain import KeychainError, MacOSKeychain
from .limits import LimitDetector, LimitEvent, normalize_terminal_text
from .runner import run_interactive
from .storage import (
    PoolStore,
    StoreError,
    utc_now,
)

ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SETUP_TOKEN_RE = re.compile(
    r"Your OAuth token \(valid for 1 year\):\s*"
    r"(?P<token>[A-Za-z0-9._~-]{40,})"
)
COMMANDS = {
    "add",
    "remove",
    "list",
    "status",
    "enable",
    "disable",
    "cooldown",
    "clear",
    "doctor",
    "desktop",
    "run",
    "setup",
    "help",
    "usage-update",
}

HELP = """\
claude-pool — route Claude Code through your available account profiles

Usage:
  claude-pool [pool options] [--] [claude arguments...]
  claude-pool run [pool options] [--] [claude arguments...]
  claude-pool add <name> [--skip-setup]
  claude-pool setup [--desktop] <name> <name> [...]
  claude-pool remove <name> [--yes]
  claude-pool status
  claude-pool enable|disable <name>
  claude-pool cooldown <name> <duration>
  claude-pool clear [name]
  claude-pool doctor
  claude-pool desktop install|status|uninstall

Pool options:
  --account <name>    Select a specific account profile
  --force             Use the selected account even if disabled/cooling down
  --no-failover       Do not relaunch on a detected usage-window limit
  --quiet             Suppress claude-pool status messages
  --claude <path>     Use a specific Claude Code executable

Examples:
  claude-pool
  claude-pool --account primary
  claude-pool -- --model opus
  claude-pool add primary
  claude-pool setup --desktop personal work
  claude-pool cooldown primary 5h

Pass Claude's --help after a separator:
  claude-pool -- --help
"""


class CliError(RuntimeError):
    """User-facing command error."""


@dataclass(frozen=True)
class RunOptions:
    account: str | None
    force: bool
    failover: bool
    quiet: bool
    claude_path: str | None
    claude_args: list[str]


@dataclass(frozen=True)
class SessionPlan:
    first_args: list[str]
    resume_args: list[str] | None

    @classmethod
    def build(cls, args: Sequence[str], *, failover: bool) -> SessionPlan:
        first = list(args)
        if _has_flag(first, "--remote-control") or any(
            argument.startswith("--remote-control=") for argument in first
        ):
            raise CliError(
                "Long-lived OAuth tokens cannot start Remote Control sessions. "
                "Use Claude's normal /login session for --remote-control."
            )

        if _has_flag(first, "--bare"):
            raise CliError(
                "Claude's --bare mode explicitly ignores OAuth tokens, so it "
                "cannot be used through claude-pool."
            )

        if not failover or _has_flag(first, "--no-session-persistence"):
            return cls(first_args=first, resume_args=None)

        session_id = _option_value(first, "--session-id")
        if session_id:
            return cls(
                first_args=first,
                resume_args=["--resume", session_id],
            )

        resume = _option_value(first, "--resume", short="-r")
        if resume:
            return cls(first_args=first, resume_args=["--resume", resume])
        if _has_flag(first, "--resume", "-r"):
            return cls(first_args=first, resume_args=None)

        if _has_flag(first, "--continue", "-c"):
            return cls(first_args=first, resume_args=["--continue"])

        session_id = str(uuid.uuid4())
        return cls(
            first_args=["--session-id", session_id, *first],
            resume_args=["--resume", session_id],
        )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    try:
        return _main(arguments)
    except (CliError, DesktopError, KeychainError, StoreError) as exc:
        print(f"claude-pool: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


def _main(arguments: list[str]) -> int:
    if arguments and arguments[0] in {"-h", "--help", "help"}:
        print(HELP)
        return 0
    if arguments and arguments[0] in {"-V", "--version"}:
        print(f"claude-pool {__version__}")
        return 0

    store = PoolStore()
    if arguments and arguments[0] == "usage-update":
        return _usage_update(store, arguments[1:])

    keychain = MacOSKeychain()
    keychain.validate()

    if arguments and arguments[0] in COMMANDS:
        command = arguments.pop(0)
        if command == "run":
            return _run(store, keychain, _parse_run_options(arguments))
        if command == "add":
            return _add(store, keychain, arguments)
        if command == "setup":
            return _setup(store, keychain, arguments)
        if command == "remove":
            return _remove(store, keychain, arguments)
        if command in {"list", "status"}:
            _require_no_arguments(command, arguments)
            return _status(store, keychain)
        if command in {"enable", "disable"}:
            return _set_enabled(store, command == "enable", arguments)
        if command == "cooldown":
            return _cooldown(store, arguments)
        if command == "clear":
            return _clear(store, arguments)
        if command == "doctor":
            _require_no_arguments(command, arguments)
            return _doctor(store, keychain)
        if command == "desktop":
            return _desktop(arguments)

    return _run(store, keychain, _parse_run_options(arguments))


def _setup(
    store: PoolStore,
    keychain: MacOSKeychain,
    arguments: list[str],
) -> int:
    install_desktop_after = False
    names = []
    for argument in arguments:
        if argument == "--desktop":
            install_desktop_after = True
        elif argument.startswith("-"):
            raise CliError(f"Unknown setup option: {argument}")
        else:
            names.append(_validate_account_name(argument))
    if len(names) < 2:
        raise CliError(
            "Usage: claude-pool setup [--desktop] <name> <name> [...]"
        )
    if len(set(names)) != len(names):
        raise CliError("Account aliases in setup must be unique")

    print(
        "Claude Pool setup\n"
        "Each alias will open Claude's OAuth flow. Before authorizing, make "
        "sure the browser is signed into the intended subscription account."
    )
    for index, name in enumerate(names, start=1):
        if keychain.has_token(name):
            store.add_account(name)
            print(f'\n[{index}/{len(names)}] "{name}" is already enrolled; skipping.')
            continue
        print(f'\n[{index}/{len(names)}] Enrolling "{name}"')
        result = _add(store, keychain, [name])
        if result != 0:
            return result

    if install_desktop_after:
        _desktop(["install"])
    print("\nSetup complete.")
    _status(store, keychain)
    if install_desktop_after:
        print("\nFully quit and reopen Claude Desktop once to activate routing.")
    return 0


def _desktop(arguments: list[str]) -> int:
    action = arguments.pop(0) if arguments else "status"
    quiet = False
    root = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--quiet":
            quiet = True
            index += 1
            continue
        if argument == "--root":
            value, index = _take_value(arguments, index, argument)
            root = Path(value).expanduser()
            continue
        raise CliError(f"Unknown desktop option: {argument}")

    source_root = Path(__file__).resolve().parent.parent
    python_executable = Path(sys.executable).resolve()
    if action == "status":
        if quiet:
            raise CliError("--quiet is not valid with desktop status")
        return desktop_status(root)
    if action == "install":
        keychain = MacOSKeychain()
        keychain.validate()
        store = PoolStore()
        tokens = _token_accounts(store, keychain) & set(
            store.account_names(enabled_only=True)
        )
        if len(tokens) < 2:
            raise CliError(
                "Desktop routing needs at least two enabled Keychain profiles"
            )
        install_desktop(
            source_root=source_root,
            python_executable=python_executable,
            quiet=quiet,
            root=root,
        )
        return 0
    if action == "repair":
        repair_desktop(
            source_root=source_root,
            python_executable=python_executable,
            quiet=quiet,
            root=root,
        )
        return 0
    if action == "uninstall":
        uninstall_desktop(quiet=quiet, root=root)
        return 0
    raise CliError("Usage: claude-pool desktop install|status|repair|uninstall")


def _parse_run_options(arguments: list[str]) -> RunOptions:
    account = None
    force = False
    failover = True
    quiet = False
    claude_path = None
    index = 0

    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            index += 1
            break
        if argument == "--account":
            account, index = _take_value(arguments, index, argument)
            continue
        if argument == "--claude":
            claude_path, index = _take_value(arguments, index, argument)
            continue
        if argument == "--force":
            force = True
            index += 1
            continue
        if argument == "--no-failover":
            failover = False
            index += 1
            continue
        if argument == "--quiet":
            quiet = True
            index += 1
            continue
        break

    return RunOptions(
        account=account,
        force=force,
        failover=failover,
        quiet=quiet,
        claude_path=claude_path,
        claude_args=arguments[index:],
    )


def _run(
    store: PoolStore,
    keychain: MacOSKeychain,
    options: RunOptions,
) -> int:
    claude = _find_claude(options.claude_path)
    session_plan = SessionPlan.build(
        options.claude_args,
        failover=options.failover and _is_interactive(options.claude_args),
    )
    token_accounts = _token_accounts(store, keychain)
    current = store.claim(
        token_accounts=token_accounts,
        preferred=options.account,
        force=options.force,
    )
    current_args = session_plan.first_args
    automatic_handoff = options.failover and session_plan.resume_args is not None
    notify = (lambda _message: None) if options.quiet else _pool_message

    try:
        while True:
            token = keychain.get(current)
            child_env = _claude_environment(token, current)
            notify(f"using {current}")

            if not _is_interactive(current_args):
                return subprocess.run(
                    [claude, *current_args],
                    env=child_env,
                    check=False,
                ).returncode

            next_account: str | None = None
            account_for_run = current

            def on_limit(
                event: LimitEvent,
                account_for_run: str = account_for_run,
            ) -> bool:
                nonlocal next_account
                if not automatic_handoff:
                    store.mark_cooldown(
                        account=account_for_run,
                        cooldown_until=event.cooldown_until,
                        reason=f"{event.kind} limit",
                    )
                    notify(
                        f"{account_for_run} reached its {event.kind} limit "
                        f"(available {_format_local(event.cooldown_until)}); "
                        "automatic handoff is disabled for this session"
                    )
                    return False

                next_account = store.mark_cooldown_and_switch(
                    current_account=account_for_run,
                    cooldown_until=event.cooldown_until,
                    reason=f"{event.kind} limit",
                    token_accounts=token_accounts,
                )
                reset_label = _format_local(event.cooldown_until)
                if next_account is None:
                    notify(
                        f"{account_for_run} reached its {event.kind} limit "
                        f"(available {reset_label}); no other account is ready"
                    )
                    return False
                notify(
                    f"{account_for_run} reached its {event.kind} limit "
                    f"(available {reset_label}); switching to {next_account}"
                )
                return True

            result = run_interactive(
                [claude, *current_args],
                env=child_env,
                detector=LimitDetector(now=lambda: datetime.now().astimezone()),
                on_limit=on_limit,
            )
            if not result.restart_requested:
                return result.exit_code
            if next_account is None or session_plan.resume_args is None:
                return result.exit_code

            current = next_account
            current_args = session_plan.resume_args
    finally:
        store.release()


def _add(
    store: PoolStore,
    keychain: MacOSKeychain,
    arguments: list[str],
) -> int:
    skip_setup = False
    names = []
    for argument in arguments:
        if argument == "--skip-setup":
            skip_setup = True
        elif argument.startswith("-"):
            raise CliError(f"Unknown add option: {argument}")
        else:
            names.append(argument)
    if len(names) != 1:
        raise CliError("Usage: claude-pool add <name> [--skip-setup]")
    name = _validate_account_name(names[0])

    if not skip_setup:
        claude = _find_claude(None)
        print(
            f'\nAdding Claude account profile "{name}".\n'
            "Claude will open an OAuth flow. Sign in to the intended account; "
            "the generated token will be captured and stored in Keychain "
            "automatically.\n"
        )
        token = _generate_setup_token(claude)
        keychain.store(name, token)
    else:
        print(
            "\nPaste the generated token at the Keychain password prompt. "
            "The value will not be echoed or placed in shell history."
        )
        keychain.store_interactive(name)
    keychain.get(name)
    created = store.add_account(name)
    action = "Added" if created else "Updated"
    print(f'{action} account profile "{name}".')
    return 0


def _remove(
    store: PoolStore,
    keychain: MacOSKeychain,
    arguments: list[str],
) -> int:
    assume_yes = "--yes" in arguments
    names = [argument for argument in arguments if argument != "--yes"]
    if len(names) != 1:
        raise CliError("Usage: claude-pool remove <name> [--yes]")
    name = _validate_account_name(names[0])
    if name not in store.account_names():
        raise CliError(f'Unknown account "{name}"')

    if not assume_yes:
        response = input(
            f'Remove "{name}" from claude-pool and delete its Keychain token? [y/N] '
        )
        if response.strip().lower() not in {"y", "yes"}:
            print("Cancelled.")
            return 0

    keychain.remove(name)
    store.remove_account(name)
    print(f'Removed account profile "{name}".')
    return 0


def _status(store: PoolStore, keychain: MacOSKeychain) -> int:
    names = store.account_names()
    token_accounts = _token_accounts(store, keychain)
    snapshots = store.snapshots(token_accounts=token_accounts)
    if not snapshots:
        print("No accounts configured. Run: claude-pool add <name>")
        return 0

    header = ("ACCOUNT", "STATUS", "ACTIVE", "5H", "7D", "LAUNCHES", "LAST USED")
    rows = [header]
    now = utc_now()
    for snapshot in snapshots:
        if not snapshot.has_token:
            status = "missing token"
        elif not snapshot.enabled:
            status = "disabled"
        elif snapshot.cooldown_until and snapshot.cooldown_until > now:
            status = f"cooldown to {_format_local(snapshot.cooldown_until)}"
        else:
            status = "available"
        rows.append(
            (
                snapshot.name,
                status,
                str(snapshot.active_sessions),
                _format_percentage(snapshot.five_hour_used_percentage),
                _format_percentage(snapshot.seven_day_used_percentage),
                str(snapshot.launch_count),
                _format_local(snapshot.last_used_at)
                if snapshot.last_used_at
                else "never",
            )
        )
    _print_table(rows)
    return 0


def _set_enabled(
    store: PoolStore,
    enabled: bool,
    arguments: list[str],
) -> int:
    if len(arguments) != 1:
        command = "enable" if enabled else "disable"
        raise CliError(f"Usage: claude-pool {command} <name>")
    name = _validate_account_name(arguments[0])
    store.set_enabled(name, enabled)
    print(f'Account "{name}" {"enabled" if enabled else "disabled"}.')
    return 0


def _cooldown(store: PoolStore, arguments: list[str]) -> int:
    if len(arguments) != 2:
        raise CliError("Usage: claude-pool cooldown <name> <duration>")
    name = _validate_account_name(arguments[0])
    duration = _parse_duration(arguments[1])
    until = store.set_manual_cooldown(name, duration)
    print(f'Account "{name}" cooling down until {_format_local(until)}.')
    return 0


def _clear(store: PoolStore, arguments: list[str]) -> int:
    if len(arguments) > 1:
        raise CliError("Usage: claude-pool clear [name]")
    name = _validate_account_name(arguments[0]) if arguments else None
    cleared = store.clear_cooldown(name)
    if name:
        print(f'Cooldown cleared for "{name}".')
    else:
        print(f"Cleared {cleared} cooldown(s).")
    return 0


def _doctor(store: PoolStore, keychain: MacOSKeychain) -> int:
    claude = _find_claude(None)
    version = subprocess.run(
        [claude, "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    ).stdout.strip()
    names = store.account_names()
    tokens = _token_accounts(store, keychain)
    config_path, state_path = store.paths()

    print(f"Claude Code: {version or claude}")
    print(f"Claude binary: {claude}")
    print(f"Config: {config_path}")
    print(f"State: {state_path}")
    print(f"Profiles: {len(names)} configured, {len(tokens)} with Keychain tokens")
    missing = [name for name in names if name not in tokens]
    if missing:
        print(f"Missing/empty tokens: {', '.join(missing)}")
        return 1
    if not names:
        print("Next step: claude-pool add <name>")
    else:
        print("Ready.")
    return 0


def _claude_environment(token: str, account: str) -> dict[str, str]:
    environment = os.environ.copy()
    for variable in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
        "CLAUDE_CODE_OAUTH_SCOPES",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        environment.pop(variable, None)
    environment["CLAUDE_CODE_OAUTH_TOKEN"] = token
    environment["CLAUDE_POOL_ACCOUNT"] = account
    return environment


def _token_accounts(
    store: PoolStore,
    keychain: MacOSKeychain,
) -> set[str]:
    return {
        name
        for name in store.account_names(enabled_only=False)
        if keychain.has_token(name)
    }


def _usage_update(store: PoolStore, arguments: list[str]) -> int:
    if len(arguments) != 1:
        raise CliError("Usage: claude-pool usage-update <name>")
    name = _validate_account_name(arguments[0])
    payload = sys.stdin.read(1_000_001)
    if len(payload) > 1_000_000:
        raise CliError("Status-line payload is too large")
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CliError("Status-line payload is not valid JSON") from exc
    rate_limits = document.get("rate_limits", {})
    five_hour = rate_limits.get("five_hour", {})
    seven_day = rate_limits.get("seven_day", {})
    five_used = _optional_percentage(five_hour.get("used_percentage"))
    seven_used = _optional_percentage(seven_day.get("used_percentage"))
    if five_used is None and seven_used is None:
        return 0
    store.record_usage(
        name=name,
        five_hour_used_percentage=five_used,
        five_hour_resets_at=_optional_epoch(five_hour.get("resets_at")),
        seven_day_used_percentage=seven_used,
        seven_day_resets_at=_optional_epoch(seven_day.get("resets_at")),
    )
    return 0


def _optional_percentage(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CliError("Usage percentage must be numeric") from exc


def _optional_epoch(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError) as exc:
        raise CliError("Usage reset time must be a Unix timestamp") from exc


def _format_percentage(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "—"


def _find_claude(explicit: str | None) -> str:
    candidate = explicit or os.environ.get("CLAUDE_POOL_CLAUDE") or "claude"
    resolved = shutil.which(candidate)
    if not resolved:
        raise CliError(
            f'Claude Code executable not found: "{candidate}". '
            "Install Claude Code or pass --claude <path>."
        )
    return resolved


def _generate_setup_token(claude: str) -> str:
    captured = bytearray()

    def capture(data: bytes) -> None:
        captured.extend(data)
        if len(captured) > 1_000_000:
            del captured[:-1_000_000]

    result = run_interactive(
        [claude, "setup-token"],
        env=os.environ.copy(),
        detector=LimitDetector(now=lambda: datetime.now().astimezone()),
        on_limit=lambda _event: False,
        on_output=capture,
    )
    if result.exit_code != 0:
        raise CliError("claude setup-token did not complete successfully")

    normalized = normalize_terminal_text(captured.decode("utf-8", errors="ignore"))
    matches = list(SETUP_TOKEN_RE.finditer(normalized))
    if not matches:
        raise CliError(
            "Claude completed authorization but the generated token could not "
            "be captured. Run `claude setup-token` directly and keep the "
            "terminal open, then use `claude-pool add <name> --skip-setup`."
        )
    return matches[-1].group("token")


def _is_interactive(arguments: Sequence[str]) -> bool:
    return (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and not _has_flag(arguments, "--print", "-p")
    )


def _has_flag(arguments: Sequence[str], *flags: str) -> bool:
    return any(argument in flags for argument in arguments)


def _option_value(
    arguments: Sequence[str],
    long: str,
    *,
    short: str | None = None,
) -> str | None:
    flags = {long}
    if short:
        flags.add(short)
    for index, argument in enumerate(arguments):
        if argument.startswith(f"{long}="):
            return argument.split("=", 1)[1]
        if argument in flags and index + 1 < len(arguments):
            candidate = arguments[index + 1]
            if not candidate.startswith("-"):
                return candidate
    return None


def _take_value(
    arguments: Sequence[str],
    index: int,
    option: str,
) -> tuple[str, int]:
    if index + 1 >= len(arguments):
        raise CliError(f"{option} requires a value")
    return arguments[index + 1], index + 2


def _validate_account_name(name: str) -> str:
    if not ACCOUNT_RE.fullmatch(name):
        raise CliError(
            "Account names must start with a letter or number and contain only "
            "letters, numbers, dots, underscores, or hyphens (64 chars max)"
        )
    return name


def _parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"(?P<number>\d+)(?P<unit>[mhdw])", value.lower())
    if not match:
        raise CliError("Duration must look like 30m, 5h, 2d, or 1w")
    number = int(match.group("number"))
    if number <= 0:
        raise CliError("Duration must be greater than zero")
    unit = match.group("unit")
    return {
        "m": timedelta(minutes=number),
        "h": timedelta(hours=number),
        "d": timedelta(days=number),
        "w": timedelta(weeks=number),
    }[unit]


def _format_local(value: datetime) -> str:
    local = value.astimezone()
    return local.strftime("%Y-%m-%d %I:%M %p %Z")


def _pool_message(message: str) -> None:
    print(f"\r\n\033[2K[claude-pool] {message}", file=sys.stderr, flush=True)


def _print_table(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for row_index, row in enumerate(rows):
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
        if row_index == 0:
            print("  ".join("-" * width for width in widths))


def _require_no_arguments(command: str, arguments: Sequence[str]) -> None:
    if arguments:
        raise CliError(f"Usage: claude-pool {command}")
