from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_pool.desktop import (
    _desktop_environment,
    _observe_event,
    default_shim_path,
    install_desktop,
    is_shim,
    uninstall_desktop,
)
from claude_pool.storage import PoolStore


class DesktopEnvironmentTests(unittest.TestCase):
    def test_pool_token_disables_desktop_auth_refresh(self) -> None:
        environment = _desktop_environment(
            {
                "CLAUDE_CODE_OAUTH_TOKEN": "desktop-token",
                "CLAUDE_CODE_OAUTH_SCOPES": "user:profile",
                "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH": "1",
                "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH": "1",
                "KEEP_ME": "yes",
            },
            "pool-token",
            "account-a",
        )

        self.assertEqual(environment["CLAUDE_CODE_OAUTH_TOKEN"], "pool-token")
        self.assertEqual(environment["CLAUDE_POOL_ACCOUNT"], "account-a")
        self.assertEqual(environment["KEEP_ME"], "yes")
        self.assertNotIn("CLAUDE_CODE_OAUTH_SCOPES", environment)
        self.assertNotIn("CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH", environment)
        self.assertNotIn("CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH", environment)


class DesktopInstallerTests(unittest.TestCase):
    def test_install_uses_local_binary_override_without_changing_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = (
                root
                / "2.1.219"
                / "claude.app"
                / "Contents"
                / "MacOS"
                / "claude"
            )
            worker.parent.mkdir(parents=True)
            original = b"#!/bin/sh\necho original\n"
            worker.write_bytes(original)
            worker.chmod(worker.stat().st_mode | stat.S_IXUSR)

            with patch.dict(
                os.environ,
                {"XDG_DATA_HOME": str(root / "data")},
                clear=False,
            ):
                installed = install_desktop(
                    source_root=Path(__file__).resolve().parent.parent,
                    python_executable=Path(sys.executable),
                    root=root,
                    install_agent=False,
                    quiet=True,
                )

                self.assertEqual(installed, default_shim_path())
                self.assertTrue(is_shim(installed))
                removed = uninstall_desktop(
                    root=root,
                    remove_agent=False,
                    quiet=True,
                )

            self.assertEqual(removed, [installed])
            self.assertEqual(worker.read_bytes(), original)


class DesktopTelemetryTests(unittest.TestCase):
    def test_result_event_records_cost_equivalent_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = PoolStore(
                config_dir=root / "config",
                state_dir=root / "state",
            )
            store.add_account("account-a")
            line = json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": 0.42,
                    "usage": {"input_tokens": 10_000},
                }
            ).encode()

            with patch(
                "claude_pool.desktop.PoolStore",
                return_value=store,
            ):
                _observe_event(line, "account-a")

            selected = store.claim_desktop(
                host_session_id="local-new",
                token_accounts={"account-a"},
            )
            self.assertEqual(selected, "account-a")
            state = json.loads(store.state_path.read_text(encoding="utf-8"))
            events = state["accounts"]["account-a"]["local_usage_events"]
            self.assertEqual(events[-1]["units"], 0.42)

    def test_native_rate_limit_event_updates_utilization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = PoolStore(
                config_dir=root / "config",
                state_dir=root / "state",
            )
            store.add_account("account-a")
            line = json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "allowed_warning",
                        "rateLimitType": "five_hour",
                        "utilization": 0.76,
                        "resetsAt": 1_800_000_000,
                    },
                }
            ).encode()

            with patch(
                "claude_pool.desktop.PoolStore",
                return_value=store,
            ):
                _observe_event(line, "account-a")

            snapshot = store.snapshots(token_accounts={"account-a"})[0]
            self.assertEqual(snapshot.five_hour_used_percentage, 76)


if __name__ == "__main__":
    unittest.main()
