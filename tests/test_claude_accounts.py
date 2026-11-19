import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atree import claude_usage  # noqa: E402
from atree.qt import layout as L  # noqa: E402

NOW = 1_800_000_000


class Accounts(unittest.TestCase):
    def test_api_reply_bars_and_credits(self):
        reply = {"five_hour": {"utilization": 4.0, "resets_at": "2027-02-01T08:00:00+00:00"},
                 "seven_day": {"utilization": 38.0, "resets_at": None},
                 "extra_usage": {"is_enabled": True, "monthly_limit": 25000, "used_credits": 25023.0,
                                 "currency": "USD", "decimal_places": 2}}
        bars, credits = claude_usage.from_api(reply, NOW)
        self.assertEqual([(b["label"], b["used"]) for b in bars], [("session (5h)", 4.0), ("week", 38.0)])
        self.assertEqual(credits, {"used": 250.23, "limit": 250.0, "currency": "USD"})
        off = {"extra_usage": {"is_enabled": False, "credits_ever_enabled": True,
                               "disabled_reason": "out_of_credits"}}
        self.assertEqual(claude_usage.from_api(off, NOW)[1], {"off": "out of credits"})
        self.assertIsNone(claude_usage.from_api({"extra_usage": {"is_enabled": False}}, NOW)[1])

    def test_rows_overlap_accounts_and_credits_form_the_legend(self):
        week = lambda used: {"label": "week", "used": used, "window": 604800, "resets_at": NOW + 3600}
        accounts = [{"name": "claude", "bars": [week(39)], "credits": {"used": 250.23, "limit": 250}},
                    {"name": "claude1", "bars": [week(57)], "credits": {"off": "out of credits"}}]
        rows, legend, credit = L.account_bars(accounts, NOW)
        self.assertEqual([(s["account"], s["left"], s["slot"]) for s in rows[0]["series"]],
                         [("claude", 61, 0), ("claude1", 43, 1)])
        self.assertEqual(rows[0]["left"], 52)  # the accounts' average
        self.assertEqual(legend, [{"account": "claude", "slot": 0}, {"account": "claude1", "slot": 1}])
        self.assertEqual((credit["series"], credit["text"], credit["full"]),
                         ([{"slot": 0, "frac": 0.0}, {"slot": 1, "frac": 0.0}], "$0", True))
        accounts[0]["credits"] = {"used": 100, "limit": 250}
        self.assertEqual(L.account_bars(accounts, NOW)[2]["text"], "$150")
        self.assertIn("claude1: off (out of credits)", credit["tip"])

    def test_short_numbers_and_week_tokens(self):
        import datetime
        self.assertEqual([L.short_number(n) for n in (100, 120_400, 32_000_000, 2_500_000_000_000)],
                         ["100", "120K", "32M", "2.5T"])
        today = datetime.date(2026, 11, 19)
        talks = [{"tool": "claude", "tdays": {"2026-11-19": 5, "2026-11-13": 7, "2026-11-12": 100}},
                 {"tool": "codex", "tdays": {"2026-11-18": 3}}]
        self.assertEqual(L.week_tokens(talks, today), {"claude": 12, "codex": 3})


if __name__ == "__main__":
    unittest.main()
