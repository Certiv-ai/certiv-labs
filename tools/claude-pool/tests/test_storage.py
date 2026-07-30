from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_pool.storage import NoAvailableAccount, PoolStore


class PoolStoreTests(unittest.TestCase):
    def test_desktop_session_keeps_account_affinity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = PoolStore(
                config_dir=root / "config",
                state_dir=root / "state",
                pid=41_001,
            )
            store.add_account("account-a")
            store.add_account("account-b")

            first = store.claim_desktop(
                host_session_id="local-one",
                token_accounts={"account-a", "account-b"},
            )
            store.release()
            store.pid = 41_002
            second = store.claim_desktop(
                host_session_id="local-two",
                token_accounts={"account-a", "account-b"},
            )
            store.release()
            store.pid = 41_003
            resumed = store.claim_desktop(
                host_session_id="local-one",
                token_accounts={"account-a", "account-b"},
            )

            self.assertNotEqual(first, second)
            self.assertEqual(resumed, first)

    def test_desktop_balancing_uses_recent_local_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = PoolStore(
                config_dir=root / "config",
                state_dir=root / "state",
                pid=42_001,
            )
            store.add_account("account-a")
            store.add_account("account-b")
            store.record_local_usage(account="account-a", units=12.5)

            selected = store.claim_desktop(
                host_session_id="local-new",
                token_accounts={"account-a", "account-b"},
            )

            self.assertEqual(selected, "account-b")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
        self.store = PoolStore(
            config_dir=root / "config",
            state_dir=root / "state",
            now=lambda: self.now,
            pid=4242,
        )
        self.store.add_account("primary")
        self.store.add_account("backup")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_rotates_accounts_between_sequential_launches(self) -> None:
        first = self.store.claim(token_accounts={"primary", "backup"})
        self.store.release()
        second = self.store.claim(token_accounts={"primary", "backup"})

        self.assertEqual(first, "primary")
        self.assertEqual(second, "backup")

    def test_three_accounts_rotate_evenly(self) -> None:
        self.store.add_account("third")
        selected = []
        for _ in range(6):
            selected.append(
                self.store.claim(token_accounts={"primary", "backup", "third"})
            )
            self.store.release()

        self.assertEqual(
            selected,
            ["primary", "backup", "third", "primary", "backup", "third"],
        )

    def test_prefers_account_with_lower_reported_usage(self) -> None:
        self.store.record_usage(
            name="primary",
            five_hour_used_percentage=72,
            five_hour_resets_at=self.now + timedelta(hours=2),
            seven_day_used_percentage=35,
            seven_day_resets_at=self.now + timedelta(days=3),
        )
        self.store.record_usage(
            name="backup",
            five_hour_used_percentage=21,
            five_hour_resets_at=self.now + timedelta(hours=2),
            seven_day_used_percentage=30,
            seven_day_resets_at=self.now + timedelta(days=3),
        )

        selected = self.store.claim(token_accounts={"primary", "backup"})

        self.assertEqual(selected, "backup")

    def test_expired_usage_window_no_longer_penalizes_account(self) -> None:
        self.store.record_usage(
            name="primary",
            five_hour_used_percentage=99,
            five_hour_resets_at=self.now - timedelta(minutes=1),
            seven_day_used_percentage=None,
            seven_day_resets_at=None,
        )
        self.store.record_usage(
            name="backup",
            five_hour_used_percentage=10,
            five_hour_resets_at=self.now + timedelta(hours=2),
            seven_day_used_percentage=None,
            seven_day_resets_at=None,
        )

        selected = self.store.claim(token_accounts={"primary", "backup"})

        self.assertEqual(selected, "primary")

    def test_balances_parallel_sessions_across_accounts(self) -> None:
        self.store.pid = os.getpid()
        first = self.store.claim(token_accounts={"primary", "backup"})
        second_store = PoolStore(
            config_dir=self.store.config_dir,
            state_dir=self.store.state_dir,
            now=lambda: self.now,
            pid=os.getpid() + 1_000_000,
        )

        second = second_store.claim(token_accounts={"primary", "backup"})

        self.assertEqual(first, "primary")
        self.assertEqual(second, "backup")

    def test_skips_account_in_cooldown(self) -> None:
        self.store.set_manual_cooldown("primary", timedelta(hours=5))

        selected = self.store.claim(token_accounts={"primary", "backup"})

        self.assertEqual(selected, "backup")

    def test_mark_cooldown_atomically_switches_lease(self) -> None:
        selected = self.store.claim(token_accounts={"primary", "backup"})

        next_account = self.store.mark_cooldown_and_switch(
            current_account=selected,
            cooldown_until=self.now + timedelta(hours=5),
            reason="session limit",
            token_accounts={"primary", "backup"},
        )

        self.assertEqual(next_account, "backup")
        state = json.loads(self.store.state_path.read_text())
        self.assertEqual(state["leases"]["4242"]["account"], "backup")
        self.assertEqual(
            state["accounts"]["primary"]["cooldown_reason"],
            "session limit",
        )

    def test_mark_cooldown_without_switch_preserves_lease(self) -> None:
        selected = self.store.claim(token_accounts={"primary", "backup"})

        self.store.mark_cooldown(
            account=selected,
            cooldown_until=self.now + timedelta(hours=5),
            reason="session limit",
        )

        state = json.loads(self.store.state_path.read_text())
        self.assertEqual(state["leases"]["4242"]["account"], "primary")
        self.assertEqual(
            state["accounts"]["primary"]["cooldown_reason"],
            "session limit",
        )

    def test_missing_keychain_token_is_not_selected(self) -> None:
        selected = self.store.claim(token_accounts={"backup"})

        self.assertEqual(selected, "backup")

    def test_raises_when_every_account_is_cooling_down(self) -> None:
        self.store.set_manual_cooldown("primary", timedelta(hours=5))
        self.store.set_manual_cooldown("backup", timedelta(hours=5))

        with self.assertRaises(NoAvailableAccount):
            self.store.claim(token_accounts={"primary", "backup"})

    def test_config_and_state_do_not_contain_tokens(self) -> None:
        self.store.claim(token_accounts={"primary", "backup"})

        combined = (
            self.store.config_path.read_text() + self.store.state_path.read_text()
        )
        self.assertNotIn("oauth", combined.lower())
        self.assertNotIn("token-value", combined)


if __name__ == "__main__":
    unittest.main()
