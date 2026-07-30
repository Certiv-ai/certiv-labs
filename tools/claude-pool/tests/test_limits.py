from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from claude_pool.limits import LimitDetector, parse_reset_time


class ResetTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 28, 13, 30, tzinfo=timezone.utc)

    def test_parses_time_today(self) -> None:
        parsed, fallback = parse_reset_time(
            "3:15 PM UTC",
            now=self.now,
            kind="session",
        )

        self.assertFalse(fallback)
        self.assertEqual(parsed, datetime(2026, 7, 28, 15, 15, tzinfo=timezone.utc))

    def test_time_that_passed_rolls_to_tomorrow(self) -> None:
        parsed, fallback = parse_reset_time(
            "1:00 PM UTC",
            now=self.now,
            kind="session",
        )

        self.assertFalse(fallback)
        self.assertEqual(parsed, datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc))

    def test_parses_month_day_and_time(self) -> None:
        parsed, fallback = parse_reset_time(
            "Jul 31, 9:00 AM UTC",
            now=self.now,
            kind="weekly",
        )

        self.assertFalse(fallback)
        self.assertEqual(parsed, datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc))

    def test_weekly_fallback_is_seven_days(self) -> None:
        parsed, fallback = parse_reset_time(
            "presentation changed",
            now=self.now,
            kind="weekly",
        )

        self.assertTrue(fallback)
        self.assertEqual(parsed, self.now + timedelta(days=7, minutes=5))


class LimitDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 28, 13, 30, tzinfo=timezone.utc)

    def test_detects_fragmented_ansi_limit_card(self) -> None:
        detector = LimitDetector(now=lambda: self.now)

        first = detector.feed(b"\x1b[31mYou've hit your ses")
        second = detector.feed(
            b"sion limit\x1b[0m\r\nYour session limit resets at 3:00 PM UTC"
        )

        self.assertIsNone(first)
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.kind, "session")
        self.assertEqual(
            second.cooldown_until,
            datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(second.used_fallback)

    def test_does_not_trigger_for_warning_without_hit_message(self) -> None:
        detector = LimitDetector(now=lambda: self.now)

        event = detector.feed(b"Your session limit resets at 3:00 PM UTC")

        self.assertIsNone(event)

    def test_only_triggers_once(self) -> None:
        detector = LimitDetector(now=lambda: self.now)
        message = b"You've hit your weekly limit"

        first = detector.feed(message)
        second = detector.feed(message)

        self.assertIsNotNone(first)
        self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
