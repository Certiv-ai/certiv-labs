from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

from claude_pool.limits import LimitDetector
from claude_pool.runner import run_interactive


class PtyRunnerTests(unittest.TestCase):
    def test_limit_event_requests_child_restart(self) -> None:
        script = (
            "import time\n"
            'print("You\'ve hit your session limit", flush=True)\n'
            'print("Your session limit resets at 3:00 PM UTC", flush=True)\n'
            "time.sleep(30)\n"
        )
        events = []
        saved_stdout = os.dup(sys.stdout.fileno())
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
            result = run_interactive(
                [sys.executable, "-c", script],
                env=os.environ.copy(),
                detector=LimitDetector(
                    now=lambda: datetime(
                        2026,
                        7,
                        28,
                        13,
                        30,
                        tzinfo=timezone.utc,
                    )
                ),
                on_limit=lambda event: events.append(event) is None,
            )
        finally:
            os.dup2(saved_stdout, sys.stdout.fileno())
            os.close(saved_stdout)
            os.close(devnull)

        self.assertTrue(result.restart_requested)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "session")


if __name__ == "__main__":
    unittest.main()
