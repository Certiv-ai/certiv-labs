"""Non-secret account registry, cooldowns, and concurrent-process leases."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG_VERSION = 1
STATE_VERSION = 1


class StoreError(RuntimeError):
    """Raised for invalid or unavailable pool state."""


class NoAvailableAccount(StoreError):
    """Raised when every configured account is unavailable."""


@dataclass(frozen=True)
class AccountSnapshot:
    name: str
    enabled: bool
    has_token: bool
    cooldown_until: datetime | None
    cooldown_reason: str | None
    active_sessions: int
    launch_count: int
    last_used_at: datetime | None
    five_hour_used_percentage: float | None
    seven_day_used_percentage: float | None
    usage_updated_at: datetime | None

    @property
    def cooling_down(self) -> bool:
        return self.cooldown_until is not None and self.cooldown_until > utc_now()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def default_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (
        Path(base).expanduser() / "claude-pool"
        if base
        else Path.home() / ".config" / "claude-pool"
    )


def default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return (
        Path(base).expanduser() / "claude-pool"
        if base
        else Path.home() / ".local" / "state" / "claude-pool"
    )


class PoolStore:
    def __init__(
        self,
        *,
        config_dir: Path | None = None,
        state_dir: Path | None = None,
        now: Callable[[], datetime] = utc_now,
        pid: int | None = None,
    ) -> None:
        self.config_dir = config_dir or default_config_dir()
        self.state_dir = state_dir or default_state_dir()
        self.config_path = self.config_dir / "config.json"
        self.state_path = self.state_dir / "state.json"
        self.lock_path = self.state_dir / "pool.lock"
        self.now = now
        self.pid = pid if pid is not None else os.getpid()

    def add_account(self, name: str) -> bool:
        with self._locked() as documents:
            accounts = documents.config.setdefault("accounts", [])
            for account in accounts:
                if account.get("name") == name:
                    account["enabled"] = True
                    documents.config_dirty = True
                    return False
            accounts.append({"name": name, "enabled": True})
            documents.config_dirty = True
            return True

    def remove_account(self, name: str) -> bool:
        with self._locked() as documents:
            accounts = documents.config.setdefault("accounts", [])
            filtered = [account for account in accounts if account.get("name") != name]
            if len(filtered) == len(accounts):
                return False
            documents.config["accounts"] = filtered
            documents.state.setdefault("accounts", {}).pop(name, None)
            leases = documents.state.setdefault("leases", {})
            for lease_pid in list(leases):
                if leases[lease_pid].get("account") == name:
                    del leases[lease_pid]
            documents.config_dirty = True
            documents.state_dirty = True
            return True

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._locked() as documents:
            for account in documents.config.setdefault("accounts", []):
                if account.get("name") == name:
                    account["enabled"] = enabled
                    documents.config_dirty = True
                    return
            raise StoreError(f'Unknown account "{name}"')

    def account_names(self, *, enabled_only: bool = False) -> list[str]:
        with self._locked() as documents:
            return [
                account["name"]
                for account in documents.config.get("accounts", [])
                if account.get("name")
                and (not enabled_only or account.get("enabled", True))
            ]

    def claim(
        self,
        *,
        token_accounts: set[str],
        preferred: str | None = None,
        force: bool = False,
        exclude: set[str] | None = None,
    ) -> str:
        exclude = exclude or set()
        with self._locked() as documents:
            self._clean_stale_leases(documents)
            accounts = documents.config.get("accounts", [])
            state_accounts = documents.state.setdefault("accounts", {})
            leases = documents.state.setdefault("leases", {})
            now = self.now()

            if preferred and not any(
                account.get("name") == preferred for account in accounts
            ):
                raise StoreError(f'Unknown account "{preferred}"')

            candidates: list[tuple[float, float, int, int, float, int, str]] = []
            for index, account in enumerate(accounts):
                name = account.get("name")
                if not name or name in exclude:
                    continue
                if preferred and name != preferred:
                    continue
                if not account.get("enabled", True) and not force:
                    continue
                if name not in token_accounts:
                    continue

                account_state = state_accounts.get(name, {})
                cooldown_until = parse_datetime(account_state.get("cooldown_until"))
                if cooldown_until and cooldown_until > now and not force:
                    continue

                active = sum(
                    1 for lease in leases.values() if lease.get("account") == name
                )
                last_used = parse_datetime(account_state.get("last_used_at"))
                candidates.append(
                    _candidate_rank(
                        account_state=account_state,
                        active_sessions=active,
                        last_used=last_used,
                        index=index,
                        name=name,
                        now=now,
                    )
                )

            if not candidates:
                if preferred:
                    raise NoAvailableAccount(
                        f'Account "{preferred}" is disabled, cooling down, '
                        "or missing its Keychain token"
                    )
                raise NoAvailableAccount(
                    "No enabled account with a Keychain token is currently available"
                )

            *_, selected = min(candidates)
            account_state = state_accounts.setdefault(selected, {})
            account_state["last_used_at"] = format_datetime(now)
            account_state["launch_count"] = (
                int(account_state.get("launch_count", 0)) + 1
            )
            leases[str(self.pid)] = {
                "account": selected,
                "started_at": format_datetime(now),
            }
            documents.state_dirty = True
            return selected

    def claim_desktop(
        self,
        *,
        host_session_id: str,
        token_accounts: set[str],
    ) -> str:
        """Claim an account for a Desktop session, preserving session affinity."""

        if not host_session_id:
            raise StoreError("Claude Desktop did not provide a host session ID")

        with self._locked() as documents:
            self._clean_stale_leases(documents)
            self._prune_desktop_sessions(documents)
            accounts = documents.config.get("accounts", [])
            state_accounts = documents.state.setdefault("accounts", {})
            leases = documents.state.setdefault("leases", {})
            desktop_sessions = documents.state.setdefault("desktop_sessions", {})
            now = self.now()

            configured = {
                account.get("name"): account
                for account in accounts
                if account.get("name")
            }
            affinity = desktop_sessions.get(host_session_id, {})
            selected = affinity.get("account")
            selected_config = configured.get(selected)
            if not (
                selected
                and selected_config
                and selected_config.get("enabled", True)
                and selected in token_accounts
            ):
                candidates: list[
                    tuple[float, float, float, int, int, float, int, str]
                ] = []
                for index, account in enumerate(accounts):
                    name = account.get("name")
                    if (
                        not name
                        or not account.get("enabled", True)
                        or name not in token_accounts
                    ):
                        continue
                    account_state = state_accounts.get(name, {})
                    cooldown_until = parse_datetime(
                        account_state.get("cooldown_until")
                    )
                    if cooldown_until and cooldown_until > now:
                        continue
                    active = sum(
                        1 for lease in leases.values() if lease.get("account") == name
                    )
                    last_used = parse_datetime(account_state.get("last_used_at"))
                    candidates.append(
                        _candidate_rank(
                            account_state=account_state,
                            active_sessions=active,
                            last_used=last_used,
                            index=index,
                            name=name,
                            now=now,
                        )
                    )

                if not candidates:
                    raise NoAvailableAccount(
                        "No enabled account with a Keychain token is currently "
                        "available"
                    )

                *_, selected = min(candidates)
                desktop_sessions[host_session_id] = {
                    "account": selected,
                    "created_at": format_datetime(now),
                    "last_seen_at": format_datetime(now),
                }
                account_state = state_accounts.setdefault(selected, {})
                account_state["launch_count"] = (
                    int(account_state.get("launch_count", 0)) + 1
                )
            else:
                affinity["last_seen_at"] = format_datetime(now)

            account_state = state_accounts.setdefault(selected, {})
            account_state["last_used_at"] = format_datetime(now)
            leases[str(self.pid)] = {
                "account": selected,
                "desktop_session_id": host_session_id,
                "started_at": format_datetime(now),
            }
            documents.state_dirty = True
            return selected

    def record_local_usage(self, *, account: str, units: float) -> None:
        """Record a bounded cost-equivalent event emitted by a local session."""

        if not isinstance(units, (int, float)) or units <= 0:
            return
        with self._locked() as documents:
            configured = {
                item.get("name") for item in documents.config.get("accounts", [])
            }
            if account not in configured:
                return
            now = self.now()
            account_state = documents.state.setdefault("accounts", {}).setdefault(
                account, {}
            )
            events = account_state.setdefault("local_usage_events", [])
            events.append(
                {
                    "at": format_datetime(now),
                    "units": min(float(units), 1_000_000.0),
                }
            )
            cutoff = now - timedelta(days=7, minutes=5)
            account_state["local_usage_events"] = [
                event
                for event in events[-10_000:]
                if (parse_datetime(event.get("at")) or now) >= cutoff
            ]
            documents.state_dirty = True

    def record_native_rate_limit(
        self,
        *,
        account: str,
        window: str,
        utilization: float | None,
        resets_at: datetime | None,
        limited: bool,
    ) -> None:
        """Cache a rate-limit event emitted by Claude's local JSON stream."""

        normalized_window = {
            "five_hour": "five_hour",
            "seven_day": "seven_day",
        }.get(window)
        if normalized_window is None:
            return
        percentage = utilization
        if percentage is not None and 0 <= percentage <= 1:
            percentage *= 100
        self.record_usage(
            name=account,
            five_hour_used_percentage=(
                percentage if normalized_window == "five_hour" else None
            ),
            five_hour_resets_at=(
                resets_at if normalized_window == "five_hour" else None
            ),
            seven_day_used_percentage=(
                percentage if normalized_window == "seven_day" else None
            ),
            seven_day_resets_at=(
                resets_at if normalized_window == "seven_day" else None
            ),
        )
        if limited and resets_at is not None:
            self.mark_cooldown(
                account=account,
                cooldown_until=resets_at,
                reason=f"{normalized_window.replace('_', '-')} limit",
            )

    def mark_cooldown_and_switch(
        self,
        *,
        current_account: str,
        cooldown_until: datetime,
        reason: str,
        token_accounts: set[str],
    ) -> str | None:
        """Record a limit and atomically move this process's lease."""

        with self._locked() as documents:
            self._clean_stale_leases(documents)
            now = self.now()
            state_accounts = documents.state.setdefault("accounts", {})
            current_state = state_accounts.setdefault(current_account, {})
            current_state["cooldown_until"] = format_datetime(cooldown_until)
            current_state["cooldown_reason"] = reason
            current_state["limited_at"] = format_datetime(now)

            accounts = documents.config.get("accounts", [])
            leases = documents.state.setdefault("leases", {})
            candidates: list[tuple[float, float, int, int, float, int, str]] = []
            for index, account in enumerate(accounts):
                name = account.get("name")
                if (
                    not name
                    or name == current_account
                    or not account.get("enabled", True)
                    or name not in token_accounts
                ):
                    continue
                account_state = state_accounts.get(name, {})
                candidate_cooldown = parse_datetime(account_state.get("cooldown_until"))
                if candidate_cooldown and candidate_cooldown > now:
                    continue
                active = sum(
                    1 for lease in leases.values() if lease.get("account") == name
                )
                last_used = parse_datetime(account_state.get("last_used_at"))
                candidates.append(
                    _candidate_rank(
                        account_state=account_state,
                        active_sessions=active,
                        last_used=last_used,
                        index=index,
                        name=name,
                        now=now,
                    )
                )

            if not candidates:
                documents.state_dirty = True
                return None

            *_, selected = min(candidates)
            selected_state = state_accounts.setdefault(selected, {})
            selected_state["last_used_at"] = format_datetime(now)
            selected_state["launch_count"] = (
                int(selected_state.get("launch_count", 0)) + 1
            )
            leases[str(self.pid)] = {
                "account": selected,
                "started_at": format_datetime(now),
            }
            documents.state_dirty = True
            return selected

    def mark_cooldown(
        self,
        *,
        account: str,
        cooldown_until: datetime,
        reason: str,
    ) -> None:
        """Record a detected limit without changing this process's lease."""

        configured = set(self.account_names())
        if account not in configured:
            raise StoreError(f'Unknown account "{account}"')
        with self._locked() as documents:
            now = self.now()
            account_state = documents.state.setdefault("accounts", {}).setdefault(
                account, {}
            )
            account_state["cooldown_until"] = format_datetime(cooldown_until)
            account_state["cooldown_reason"] = reason
            account_state["limited_at"] = format_datetime(now)
            documents.state_dirty = True

    def clear_cooldown(self, name: str | None = None) -> int:
        with self._locked() as documents:
            configured = {
                account.get("name") for account in documents.config.get("accounts", [])
            }
            if name and name not in configured:
                raise StoreError(f'Unknown account "{name}"')

            cleared = 0
            for account_name, account_state in documents.state.setdefault(
                "accounts", {}
            ).items():
                if name and account_name != name:
                    continue
                if account_state.pop("cooldown_until", None) is not None:
                    cleared += 1
                account_state.pop("cooldown_reason", None)
                account_state.pop("limited_at", None)
            if cleared:
                documents.state_dirty = True
            return cleared

    def set_manual_cooldown(
        self,
        name: str,
        duration: timedelta,
        reason: str = "manual",
    ) -> datetime:
        configured = set(self.account_names())
        if name not in configured:
            raise StoreError(f'Unknown account "{name}"')
        until = self.now() + duration
        with self._locked() as documents:
            account_state = documents.state.setdefault("accounts", {}).setdefault(
                name, {}
            )
            account_state["cooldown_until"] = format_datetime(until)
            account_state["cooldown_reason"] = reason
            account_state["limited_at"] = format_datetime(self.now())
            documents.state_dirty = True
        return until

    def record_usage(
        self,
        *,
        name: str,
        five_hour_used_percentage: float | None,
        five_hour_resets_at: datetime | None,
        seven_day_used_percentage: float | None,
        seven_day_resets_at: datetime | None,
    ) -> None:
        """Cache Claude's status-line rate-limit telemetry for scheduling."""

        with self._locked() as documents:
            configured = {
                account.get("name") for account in documents.config.get("accounts", [])
            }
            if name not in configured:
                raise StoreError(f'Unknown account "{name}"')

            account_state = documents.state.setdefault("accounts", {}).setdefault(
                name, {}
            )
            rate_limits = account_state.setdefault("rate_limits", {})
            changed = False
            for window, used, resets_at in (
                ("five_hour", five_hour_used_percentage, five_hour_resets_at),
                ("seven_day", seven_day_used_percentage, seven_day_resets_at),
            ):
                if used is None:
                    continue
                window_state = rate_limits.setdefault(window, {})
                percentage = max(0.0, min(100.0, used))
                if window_state.get("used_percentage") != percentage:
                    window_state["used_percentage"] = percentage
                    changed = True
                if resets_at is not None:
                    formatted_reset = format_datetime(resets_at)
                    if window_state.get("resets_at") != formatted_reset:
                        window_state["resets_at"] = formatted_reset
                        changed = True
            if changed:
                rate_limits["updated_at"] = format_datetime(self.now())
                documents.state_dirty = True

    def release(self) -> None:
        with self._locked() as documents:
            leases = documents.state.setdefault("leases", {})
            if leases.pop(str(self.pid), None) is not None:
                documents.state_dirty = True

    def snapshots(self, *, token_accounts: set[str]) -> list[AccountSnapshot]:
        with self._locked() as documents:
            self._clean_stale_leases(documents)
            state_accounts = documents.state.setdefault("accounts", {})
            leases = documents.state.setdefault("leases", {})
            snapshots = []
            for account in documents.config.get("accounts", []):
                name = account.get("name")
                if not name:
                    continue
                account_state = state_accounts.get(name, {})
                five_hour, seven_day = _current_usage(account_state, self.now())
                rate_limits = account_state.get("rate_limits", {})
                snapshots.append(
                    AccountSnapshot(
                        name=name,
                        enabled=account.get("enabled", True),
                        has_token=name in token_accounts,
                        cooldown_until=parse_datetime(
                            account_state.get("cooldown_until")
                        ),
                        cooldown_reason=account_state.get("cooldown_reason"),
                        active_sessions=sum(
                            1
                            for lease in leases.values()
                            if lease.get("account") == name
                        ),
                        launch_count=int(account_state.get("launch_count", 0)),
                        last_used_at=parse_datetime(account_state.get("last_used_at")),
                        five_hour_used_percentage=five_hour,
                        seven_day_used_percentage=seven_day,
                        usage_updated_at=parse_datetime(rate_limits.get("updated_at")),
                    )
                )
            return snapshots

    def paths(self) -> tuple[Path, Path]:
        return self.config_path, self.state_path

    def _clean_stale_leases(self, documents: _Documents) -> None:
        leases = documents.state.setdefault("leases", {})
        for lease_pid in list(leases):
            try:
                numeric_pid = int(lease_pid)
            except ValueError:
                del leases[lease_pid]
                documents.state_dirty = True
                continue
            if numeric_pid == self.pid:
                continue
            if not _pid_alive(numeric_pid):
                del leases[lease_pid]
                documents.state_dirty = True

    def _prune_desktop_sessions(self, documents: _Documents) -> None:
        sessions = documents.state.setdefault("desktop_sessions", {})
        if len(sessions) <= 5_000:
            return
        ordered = sorted(
            sessions.items(),
            key=lambda item: parse_datetime(item[1].get("last_seen_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        documents.state["desktop_sessions"] = dict(ordered[:5_000])
        documents.state_dirty = True

    @contextmanager
    def _locked(self) -> Iterator[_Documents]:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.config_dir, 0o700)
            os.chmod(self.state_dir, 0o700)
        except OSError:
            pass

        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            documents = _Documents(
                config=_read_json(
                    self.config_path,
                    {"version": CONFIG_VERSION, "accounts": []},
                ),
                state=_read_json(
                    self.state_path,
                    {"version": STATE_VERSION, "accounts": {}, "leases": {}},
                ),
            )
            try:
                yield documents
            finally:
                if documents.config_dirty:
                    _write_json_atomic(self.config_path, documents.config)
                if documents.state_dirty:
                    _write_json_atomic(self.state_path, documents.state)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@dataclass
class _Documents:
    config: dict
    state: dict
    config_dirty: bool = False
    state_dirty: bool = False


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StoreError(f"{path} must contain a JSON object")
    return value


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _candidate_rank(
    *,
    account_state: dict,
    active_sessions: int,
    last_used: datetime | None,
    index: int,
    name: str,
    now: datetime,
) -> tuple[float, float, float, int, int, float, int, str]:
    """Prefer least-used accounts, then spread live and sequential sessions."""

    five_hour, seven_day = _current_usage(account_state, now)
    percentages = [
        percentage
        for percentage in (five_hour, seven_day)
        if percentage is not None
    ]
    highest_usage = max(percentages, default=0.0)
    local_five_hour, local_seven_day = _current_local_usage(account_state, now)
    # Seven days contains roughly 33.6 five-hour windows. Comparing its
    # per-window average with the current five-hour burst keeps both windows
    # balanced without pretending the local telemetry is an exact quota.
    local_pressure = max(local_five_hour, local_seven_day / 33.6)
    local_total = local_five_hour + local_seven_day / 33.6
    launch_count = int(account_state.get("launch_count", 0))
    least_recent = last_used.timestamp() if last_used else float("-inf")
    return (
        highest_usage,
        local_pressure,
        local_total,
        active_sessions,
        launch_count,
        least_recent,
        index,
        name,
    )


def _current_local_usage(
    account_state: dict,
    now: datetime,
) -> tuple[float, float]:
    five_hour_cutoff = now - timedelta(hours=5)
    seven_day_cutoff = now - timedelta(days=7)
    five_hour = 0.0
    seven_day = 0.0
    for event in account_state.get("local_usage_events", []):
        timestamp = parse_datetime(event.get("at"))
        units = event.get("units")
        if timestamp is None or not isinstance(units, (int, float)):
            continue
        if timestamp >= seven_day_cutoff:
            seven_day += max(0.0, float(units))
        if timestamp >= five_hour_cutoff:
            five_hour += max(0.0, float(units))
    return five_hour, seven_day


def _current_usage(
    account_state: dict,
    now: datetime,
) -> tuple[float | None, float | None]:
    rate_limits = account_state.get("rate_limits", {})
    values: list[float | None] = []
    for window in ("five_hour", "seven_day"):
        window_state = rate_limits.get(window, {})
        value = window_state.get("used_percentage")
        if not isinstance(value, (int, float)):
            values.append(None)
            continue
        resets_at = parse_datetime(window_state.get("resets_at"))
        values.append(0.0 if resets_at and resets_at <= now else float(value))
    return values[0], values[1]
