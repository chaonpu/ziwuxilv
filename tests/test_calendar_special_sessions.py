"""Contract regressions for cloud-delivered CME product-group holiday windows."""
import copy
from datetime import date
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from calendar_runtime import CalendarUnavailable, canonical, validate
from calendar_automation import future_record


class CmeSpecialWindowsTest(unittest.TestCase):
    def setUp(self):
        self.bundle = json.loads((ROOT / "tools/calendar-seed.json").read_bytes())
        record = self.bundle["calendars"]["CME_FUTURES"]["years"]["2026"]
        self.record = record
        record["specialSessions"] = {
            "2026-11-26": {
                "EQUITY_INDEX": [
                    {"startMinute": 0, "endMinute": 780, "tone": "OPEN"},
                    {"startMinute": 780, "endMinute": 1080, "tone": "WAITING"},
                    {"startMinute": 1080, "endMinute": 1440, "tone": "OPEN"},
                ],
                "METALS_ENERGY": [
                    {"startMinute": 0, "endMinute": 870, "tone": "OPEN"},
                    {"startMinute": 870, "endMinute": 1080, "tone": "WAITING"},
                    {"startMinute": 1080, "endMinute": 1440, "tone": "OPEN"},
                ],
            },
        }

    def test_group_specific_windows_validate_even_if_calendar_date_unknown(self):
        self.assertTrue(validate(canonical(self.bundle))["revision"])

    def test_incomplete_windows_and_wrong_product_group_rejected(self):
        first = self.record["specialSessions"]["2026-11-26"]
        first["METALS_ENERGY"][0]["endMinute"] = 855
        with self.assertRaises(CalendarUnavailable):
            validate(canonical(self.bundle))
        first["METALS_ENERGY"][0]["endMinute"] = 870
        first["ICE_BRENT"] = first.pop("METALS_ENERGY")
        with self.assertRaises(CalendarUnavailable):
            validate(canonical(self.bundle))

    def test_automatic_affected_day_refresh_keeps_audited_hours(self):
        parsed = copy.deepcopy(self.record)
        parsed.pop("specialSessions")
        merged = future_record(self.record, parsed, date(2026, 11, 26))
        self.assertEqual(merged["specialSessions"], self.record["specialSessions"])
        self.assertTrue(validate(canonical({
            **self.bundle,
            "calendars": {**self.bundle["calendars"], "CME_FUTURES": {
                **self.bundle["calendars"]["CME_FUTURES"], "years": {
                    **self.bundle["calendars"]["CME_FUTURES"]["years"], "2026": merged
                }
            }}
        }))["revision"])


if __name__ == "__main__":
    unittest.main()
