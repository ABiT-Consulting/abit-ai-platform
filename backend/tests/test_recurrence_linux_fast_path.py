"""The bounded common schedules must not wait for AnyIO worker startup."""
import time
import unittest
from unittest.mock import patch

from open_webui.utils import recurrence


class CommonRecurrenceFastPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_bounded_rules_do_not_spawn_workers(self):
        rules = (
            "RRULE:FREQ=HOURLY;BYMINUTE=0",
            "RRULE:FREQ=MINUTELY;INTERVAL=15",
            "RRULE:FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        )
        with patch.object(
            recurrence.to_process,
            "run_sync",
            side_effect=AssertionError("Common recurrence must not spawn a worker"),
        ):
            for rule in rules:
                with self.subTest(rule=rule):
                    next_run = await recurrence.next_run_ns(rule, tz="Asia/Dubai")
                    self.assertGreater(next_run, time.time_ns())

    def test_unbounded_or_nonstandard_rules_still_use_isolated_evaluation(self):
        for rule in (
            "RRULE:FREQ=WEEKLY;BYDAY=MO",
            "RRULE:FREQ=MINUTELY;INTERVAL=15;BYMONTH=2",
            "RRULE:FREQ=HOURLY;BYMINUTE=0;BYHOUR=4",
            "RRULE:FREQ=MINUTELY;INTERVAL=0",
        ):
            with self.subTest(rule=rule):
                self.assertIsNone(recurrence._FAST_COMMON_RRULE.fullmatch(rule))

    async def test_invalid_outside_fast_path_is_rejected(self):
        with self.assertRaises(ValueError):
            await recurrence.validate_rrule("RRULE:FREQ=MINUTELY;INTERVAL=0")
