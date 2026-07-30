from __future__ import annotations

import json
import io
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from claude_pool.cli import (
    CliError,
    RunOptions,
    SessionPlan,
    _claude_environment,
    _generate_setup_token,
    _parse_duration,
    _run,
    _setup,
    _usage_update,
)
from claude_pool.runner import RunResult
from claude_pool.storage import PoolStore


class SessionPlanTests(unittest.TestCase):
    @patch("claude_pool.cli.uuid.uuid4", return_value="session-uuid")
    def test_new_session_gets_stable_id_for_resume(self, _uuid: object) -> None:
        plan = SessionPlan.build(["--model", "opus"], failover=True)

        self.assertEqual(
            plan.first_args,
            ["--session-id", "session-uuid", "--model", "opus"],
        )
        self.assertEqual(plan.resume_args, ["--resume", "session-uuid"])

    def test_existing_resume_id_is_reused(self) -> None:
        plan = SessionPlan.build(["--resume", "existing"], failover=True)

        self.assertEqual(plan.resume_args, ["--resume", "existing"])

    def test_bare_resume_picker_disables_auto_resume(self) -> None:
        plan = SessionPlan.build(["--resume"], failover=True)

        self.assertIsNone(plan.resume_args)

    def test_continue_is_reused(self) -> None:
        plan = SessionPlan.build(["--continue"], failover=True)

        self.assertEqual(plan.resume_args, ["--continue"])

    def test_bare_is_rejected_even_without_failover(self) -> None:
        with self.assertRaises(CliError):
            SessionPlan.build(["--bare"], failover=False)


class EnvironmentTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {
            "ANTHROPIC_API_KEY": "wrong",
            "ANTHROPIC_BASE_URL": "https://gateway.example",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "KEEP_ME": "yes",
        },
        clear=True,
    )
    def test_oauth_profile_removes_conflicting_credentials(self) -> None:
        environment = _claude_environment("secret-token", "primary")

        self.assertNotIn("ANTHROPIC_API_KEY", environment)
        self.assertNotIn("ANTHROPIC_BASE_URL", environment)
        self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", environment)
        self.assertEqual(environment["CLAUDE_CODE_OAUTH_TOKEN"], "secret-token")
        self.assertEqual(environment["CLAUDE_POOL_ACCOUNT"], "primary")
        self.assertEqual(environment["KEEP_ME"], "yes")

    def test_duration_parser(self) -> None:
        self.assertEqual(_parse_duration("5h").total_seconds(), 18_000)


class FakeKeychain:
    def __init__(self, accounts: set[str]) -> None:
        self.accounts = accounts

    def exists(self, account: str) -> bool:
        return account in self.accounts

    def get(self, account: str) -> str:
        return f"test-token-{account}"

    def has_token(self, account: str) -> bool:
        return account in self.accounts


class GuidedSetupTests(unittest.TestCase):
    @patch("claude_pool.cli._status", return_value=0)
    @patch("claude_pool.cli._desktop", return_value=0)
    @patch("claude_pool.cli._add", return_value=0)
    def test_setup_skips_enrolled_accounts_and_installs_desktop(
        self,
        add: object,
        desktop: object,
        _status: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = PoolStore(
                config_dir=root / "config",
                state_dir=root / "state",
            )
            store.add_account("personal")
            keychain = FakeKeychain({"personal"})

            exit_code = _setup(
                store,
                keychain,  # type: ignore[arg-type]
                ["--desktop", "personal", "work"],
            )

            self.assertEqual(exit_code, 0)
            add.assert_called_once_with(store, keychain, ["work"])  # type: ignore[attr-defined]
            desktop.assert_called_once_with(["install"])  # type: ignore[attr-defined]

    def test_setup_requires_two_unique_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = PoolStore(
                config_dir=root / "config",
                state_dir=root / "state",
            )
            keychain = FakeKeychain(set())

            with self.assertRaises(CliError):
                _setup(
                    store,
                    keychain,  # type: ignore[arg-type]
                    ["same", "same"],
                )


class UsageTelemetryTests(unittest.TestCase):
    def test_status_line_payload_updates_account_utilization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = PoolStore(
                config_dir=root / "config",
                state_dir=root / "state",
            )
            store.add_account("primary")
            payload = {
                "rate_limits": {
                    "five_hour": {
                        "used_percentage": 42,
                        "resets_at": 1_800_000_000,
                    },
                    "seven_day": {
                        "used_percentage": 17,
                        "resets_at": 1_800_500_000,
                    },
                }
            }

            with patch("sys.stdin", io.StringIO(json.dumps(payload))):
                exit_code = _usage_update(store, ["primary"])

            snapshot = store.snapshots(token_accounts={"primary"})[0]
            self.assertEqual(exit_code, 0)
            self.assertEqual(snapshot.five_hour_used_percentage, 42)
            self.assertEqual(snapshot.seven_day_used_percentage, 17)


class SetupTokenTests(unittest.TestCase):
    @patch("claude_pool.cli.run_interactive")
    def test_generated_token_is_captured_from_transient_tui(
        self,
        run_interactive: object,
    ) -> None:
        token = "A" * 92

        def fake_run(*_args: object, **kwargs: object) -> RunResult:
            kwargs["on_output"](
                b"\x1b[32mYour OAuth token (valid for 1 year):\x1b[0m\r\n"
                + b"\x1b[33m"
                + token.encode()
                + b"\x1b[0m\r\n"
            )
            return RunResult(exit_code=0)

        run_interactive.side_effect = fake_run  # type: ignore[attr-defined]

        self.assertEqual(_generate_setup_token("/fake/claude"), token)


class EndToEndHandoffTests(unittest.TestCase):
    @patch("claude_pool.cli._is_interactive", return_value=True)
    @patch("claude_pool.cli.uuid.uuid4", return_value="handoff-session")
    def test_limit_relaunches_next_account_and_resumes_session(
        self,
        _uuid: object,
        _interactive: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log_path = root / "launches.jsonl"
            fake_claude = root / "fake-claude"
            reset = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            fake_claude.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys, time\n"
                "with open(os.environ['FAKE_CLAUDE_LOG'], 'a') as handle:\n"
                "    handle.write(json.dumps({\n"
                "        'account': os.environ['CLAUDE_POOL_ACCOUNT'],\n"
                "        'args': sys.argv[1:],\n"
                "    }) + '\\n')\n"
                "if os.environ['CLAUDE_POOL_ACCOUNT'] == 'primary':\n"
                '    print("You\'ve hit your session limit", flush=True)\n'
                '    print("Your session limit resets at " + '
                "os.environ['FAKE_CLAUDE_RESET'], flush=True)\n"
                "    time.sleep(30)\n",
                encoding="utf-8",
            )
            fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
            store = PoolStore(
                config_dir=root / "config",
                state_dir=root / "state",
            )
            store.add_account("primary")
            store.add_account("backup")
            options = RunOptions(
                account=None,
                force=False,
                failover=True,
                quiet=True,
                claude_path=str(fake_claude),
                claude_args=[],
            )

            with patch.dict(
                os.environ,
                {
                    "FAKE_CLAUDE_LOG": str(log_path),
                    "FAKE_CLAUDE_RESET": reset,
                },
                clear=False,
            ):
                saved_stdout = os.dup(sys.stdout.fileno())
                devnull = os.open(os.devnull, os.O_WRONLY)
                try:
                    os.dup2(devnull, sys.stdout.fileno())
                    exit_code = _run(
                        store,
                        FakeKeychain({"primary", "backup"}),  # type: ignore[arg-type]
                        options,
                    )
                finally:
                    os.dup2(saved_stdout, sys.stdout.fileno())
                    os.close(saved_stdout)
                    os.close(devnull)

            launches = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                [launch["account"] for launch in launches],
                ["primary", "backup"],
            )
            self.assertEqual(
                launches[0]["args"],
                ["--session-id", "handoff-session"],
            )
            self.assertEqual(
                launches[1]["args"],
                ["--resume", "handoff-session"],
            )
            snapshots = store.snapshots(
                token_accounts={"primary", "backup"},
            )
            primary = next(
                snapshot for snapshot in snapshots if snapshot.name == "primary"
            )
            self.assertEqual(primary.cooldown_reason, "session limit")


if __name__ == "__main__":
    unittest.main()
