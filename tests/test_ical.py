"""iCalendar parsing and recurrence expansion (spec clock.md §2, §6, §7)."""
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from clocksupport import set_tz

from atree import ical

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def vevent(*lines, uid="e"):
    return ["BEGIN:VEVENT", "UID:" + uid, *lines, "END:VEVENT"]


def cal(*events, head=()):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", *head]
    for ev in events:
        lines += ev
    return "\r\n".join(lines + ["END:VCALENDAR", ""])


def d(y, m, dd, zone=NY, h=0, mi=0, fold=0):
    return datetime(y, m, dd, h, mi, tzinfo=zone, fold=fold)


class TzCase(unittest.TestCase):
    tz = "America/New_York"

    def setUp(self):
        set_tz(self, self.tz)

    def run_cal(self, text, lo, hi):
        return ical.expand(ical.parse(text), lo, hi)

    def days(self, text, lo, hi):
        return [i["start"] if i["allday"] else i["start"].date() for i in self.run_cal(text, lo, hi)]

    def titles(self, text, lo, hi):
        return [i["title"] for i in self.run_cal(text, lo, hi)]


class LocalZone(unittest.TestCase):
    def test_R_tz_01_empty_is_utc(self):
        set_tz(self, "")
        self.assertIs(ical.local_zone(), timezone.utc)

    def test_R_tz_01_iana_with_colon(self):
        set_tz(self, ":Europe/Berlin")
        self.assertEqual(str(ical.local_zone()), "Europe/Berlin")

    def test_R_tz_01_absolute_path_read_once(self):
        with open(_tzfile("America/New_York"), "rb") as f:
            data = f.read()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "local-zone")
            with open(path, "wb") as f:
                f.write(data)
            set_tz(self, ":" + path)
            opened = []
            real_open = open

            def spy(p, mode="r", *a, **k):
                opened.append((p, mode))
                return real_open(p, mode, *a, **k)
            with mock.patch("builtins.open", spy):
                zone = ical.local_zone()
            self.assertEqual(opened, [(path, "rb")])
            self.assertEqual(datetime(2026, 7, 1, tzinfo=zone).utcoffset(), timedelta(hours=-4))
            self.assertEqual(datetime(2026, 1, 1, tzinfo=zone).utcoffset(), timedelta(hours=-5))

    def test_R_tz_01_posix_rule_falls_back_without_localtime(self):
        set_tz(self, "EST5EDT,M3.2.0,M11.1.0")
        with mock.patch("builtins.open", side_effect=AssertionError("opened")):
            zone = ical.local_zone()
        self.assertEqual(zone.utcoffset(None), datetime.now().astimezone().utcoffset())

    def test_R_tz_01_unset_reads_localtime(self):
        set_tz(self, None)
        with mock.patch.object(ical, "LOCALTIME", _tzfile("Asia/Tokyo")):
            zone = ical.local_zone()
        self.assertEqual(datetime(2026, 1, 1, tzinfo=zone).utcoffset(), timedelta(hours=9))
        with mock.patch.object(ical, "LOCALTIME", "/nonexistent/zone"):
            self.assertEqual(ical.local_zone().utcoffset(None), datetime.now().astimezone().utcoffset())


def _tzfile(name):
    import zoneinfo
    for root in zoneinfo.TZPATH:
        p = os.path.join(root, name)
        if os.path.exists(p):
            return p
    raise unittest.SkipTest("no system tz database")


class ContentLines(TzCase):
    def test_R_ical_01_unfold(self):
        self.assertEqual(ical.unfold("A:x\r\n  y\r\n\tz\nB:1"), ["A:x yz", "B:1"])

    def test_R_ical_02_split(self):
        self.assertEqual(ical.split_line('LOCATION;ALTREP="http://x:1":Room'),
                         ("LOCATION", {"ALTREP": "http://x:1"}, "Room"))
        name, params, value = ical.split_line('dtstart;tzid="W. Europe Standard Time":20261102T100000')
        self.assertEqual((name, params["TZID"]), ("DTSTART", "W. Europe Standard Time"))
        self.assertIsNone(ical.split_line("no colon here"))
        self.assertEqual(ical.split_line("X: raw ")[2], " raw ")

    def test_R_ical_03_unescape(self):
        text = cal(vevent("DTSTART:20261102T150000Z", "SUMMARY:A long", "  title\\nwith a break"),
                   vevent("DTSTART:20261102T150000Z", "SUMMARY:Stand\\, up\\;\\\\", uid="b"))
        evs = ical.parse(text)
        self.assertEqual([e.title for e in evs], ["A long title with a break", "Stand, up;\\"])


class Components(TzCase):
    def test_R_ical_04_nested_valarm_and_outside_lines(self):
        text = cal(["SUMMARY:outside"], vevent("DTSTART:20261102T150000Z", "SUMMARY:Main", "BEGIN:VALARM",
                                               "SUMMARY:Alarm", "END:VALARM", "STATUS:confirmed"))
        [ev] = ical.parse(text)
        self.assertEqual((ev.title, ev.cancelled, ev.rrule, ev.exdates), ("Main", False, None, []))

    def test_R_ical_05_R_ical_07_R_ical_13_properties(self):
        text = cal(vevent("DTSTART;VALUE=DATE:20261101", "DURATION:P2D", "STATUS: cancelled ",
                          "RRULE:freq=daily;count=2;junk", "EXDATE;VALUE=DATE:20261102,,20261103",
                          "RECURRENCE-ID;VALUE=DATE:20261101", "LOCATION:ignored"))
        [ev] = ical.parse(text)
        self.assertTrue(ev.cancelled)
        self.assertEqual(ev.rrule, {"FREQ": "DAILY", "COUNT": "2"})
        self.assertEqual(ev.exdates, [date(2026, 11, 2), date(2026, 11, 3)])
        self.assertEqual(ev.recurrence_id, date(2026, 11, 1))
        self.assertEqual((ev.start, ev.end, ev.duration), (date(2026, 11, 1), date(2026, 11, 3), (2, timedelta(0))))

    def test_R_ical_06_values(self):
        local = ical.local_zone()
        self.assertEqual(ical.parse_value(" 20261101 ", {}, local), date(2026, 11, 1))
        self.assertEqual(ical.parse_value("20261101T090000Z", {}, local), datetime(2026, 11, 1, 9, tzinfo=UTC))
        floating = ical.parse_value("20260701T090000", {}, local)
        self.assertEqual(floating.utcoffset(), timedelta(hours=-4))
        for bad in ("2026110", "20261301", "20261101T25", "garbage"):
            with self.assertRaises(ValueError):
                ical.parse_value(bad, {}, local)
        text = cal(vevent("DTSTART:bad"), vevent("DTSTART:20261101T090000Z", "DTEND:bad", uid="ok"))
        [ev] = ical.parse(text)
        self.assertEqual((ev.uid, ev.end), ("ok", ev.start))

    def test_R_ical_08_tzid(self):
        for tzid in ("Mars/Olympus_Mons", "/etc/localtime", "..", "Europe/", "x" * 300, ""):
            text = cal(vevent('DTSTART;TZID="%s":20261102T100000' % tzid))
            self.assertEqual(ical.parse(text)[0].start.isoformat(), "2026-11-02T10:00:00-05:00", tzid)
        text = cal(vevent('DTSTART;TZID="W. Europe Standard Time":20261102T100000'),
                   vevent("DTSTART;TZID=/mozilla.org/20050126_1/Europe/Berlin:20261102T100000", uid="b"),
                   vevent("DTSTART;TZID=Asia/Tokyo:20261102T100000", uid="c"))
        a, b, c = ical.parse(text)
        self.assertEqual(a.start.astimezone(UTC).hour, 9)
        self.assertEqual(str(b.start.tzinfo), "Europe/Berlin")
        self.assertEqual(c.start.utcoffset(), timedelta(hours=9))

    def test_R_ical_09_durations(self):
        self.assertEqual(ical.parse_duration("P1W2DT3H4M5S"), (9, timedelta(hours=3, minutes=4, seconds=5)))
        self.assertEqual(ical.parse_duration("-P1DT1H"), (-1, -timedelta(hours=1)))
        self.assertEqual(ical.parse_duration("junk"), (0, timedelta(0)))

    def test_R_ical_10_ends(self):
        text = cal(vevent("DTSTART;VALUE=DATE:20261101"),
                   vevent("DTSTART:20261101T090000Z", uid="t"),
                   vevent("DTSTART:20261101T090000Z", "DURATION:PT1H", "DTEND:20261101T093000Z", uid="both"),
                   vevent("SUMMARY:no start", uid="none"))
        a, t, both = ical.parse(text)
        self.assertEqual(a.end, date(2026, 11, 2))
        self.assertEqual(t.end, t.start)
        self.assertEqual((both.end.minute, both.duration), (30, None))

    def test_R_ical_12_overflow_isolation(self):
        bad = [["DTSTART;VALUE=DATE:99991231"],
               ["DTSTART;VALUE=DATE:20261101", "DURATION:P999999999D"],
               ["DTSTART;VALUE=DATE:20261101", "DURATION:-P999999999D"],
               ["DTSTART:20261101T090000Z", "DURATION:PT999999999H"],
               ["DTSTART;VALUE=DATE:20261101", "DURATION:P999999999999999999D"]]
        for lines in bad:
            text = cal(vevent("DTSTART;VALUE=DATE:20261101", "SUMMARY:before", uid="before"),
                       vevent(*lines, "SUMMARY:bad", uid="bad"),
                       vevent("DTSTART;VALUE=DATE:20261102", "SUMMARY:after", uid="after"))
            self.assertEqual([e.uid for e in ical.parse(text)], ["before", "after"], lines)
            self.assertEqual(self.titles(text, d(2026, 10, 30), d(2026, 11, 5)), ["before", "after"])

    def test_R_ical_12_malformed_exdate_drops_whole_property(self):
        text = cal(vevent("DTSTART;VALUE=DATE:20261101", "RRULE:FREQ=DAILY;COUNT=3",
                          "EXDATE;VALUE=DATE:20261102,bogus", "EXDATE;VALUE=DATE:20261103"))
        [ev] = ical.parse(text)
        self.assertEqual(ev.exdates, [date(2026, 11, 3)])
        self.assertEqual(self.days(text, d(2026, 11, 1), d(2026, 11, 5)), [date(2026, 11, 1), date(2026, 11, 2)])

    def test_R_ical_14_calname(self):
        self.assertEqual(ical.calname(cal(head=["X-WR-CALNAME:Work\\, home"])), "Work, home")
        self.assertEqual(ical.calname(cal(vevent("X-WR-CALNAME:inside"))), "")
        self.assertEqual(ical.calname("x" * 20000 + "\nX-WR-CALNAME:late"), "")


class Expansion(TzCase):
    def test_R_exp_01_R_exp_04_allday_overlap(self):
        text = cal(vevent("DTSTART;VALUE=DATE:20261101", "DTEND;VALUE=DATE:20261104", "SUMMARY:Trip"),
                   vevent("DTSTART;VALUE=DATE:20261103", "SUMMARY:Holiday", uid="h"),
                   vevent("DTSTART;VALUE=DATE:20261102", "SUMMARY:Ended", uid="x"))
        got = [(i["title"], i["allday"], i["start"], i["end"])
               for i in self.run_cal(text, d(2026, 11, 3), d(2026, 11, 4))]
        self.assertEqual(got, [("Trip", True, date(2026, 11, 1), date(2026, 11, 4)),
                               ("Holiday", True, date(2026, 11, 3), date(2026, 11, 4))])

    def test_R_exp_02_order_and_no_title(self):
        text = cal(vevent("DTSTART:20261103T050000Z", "SUMMARY:b", uid="1"),
                   vevent("DTSTART:20261103T050000Z", "SUMMARY:a", uid="2"),
                   vevent("DTSTART;VALUE=DATE:20261103", uid="3"),
                   vevent("DTSTART:20261103T060000Z", "SUMMARY:0 late", uid="4"))
        self.assertEqual(self.titles(text, d(2026, 11, 3), d(2026, 11, 4)), ["(no title)", "a", "b", "0 late"])

    def test_R_exp_03_R_exp_05_cancelled_and_duration(self):
        text = cal(vevent("DTSTART:20261102T150000Z", "DURATION:PT1H30M", "SUMMARY:x"),
                   vevent("DTSTART:20261102T150000Z", "STATUS:CANCELLED", uid="c"))
        [i] = self.run_cal(text, d(2026, 11, 2), d(2026, 11, 3))
        self.assertEqual((i["start"].hour, i["end"].hour, i["end"].minute), (15, 16, 30))
        self.assertEqual(i["start"].tzinfo, UTC)

    def test_R_exp_06_invalid_span(self):
        for end in ("DTEND:20261102T120000Z", "DTEND;VALUE=DATE:20261031"):
            text = cal(vevent("DTSTART;VALUE=DATE:20261101", end, "RRULE:FREQ=DAILY", "SUMMARY:bad"),
                       vevent("DTSTART;VALUE=DATE:20261101", "SUMMARY:ok", uid="ok"))
            self.assertEqual(self.titles(text, d(2026, 10, 30), d(2026, 11, 5)), ["ok"])

    STANDUP = vevent("DTSTART;TZID=America/New_York:20261005T093000", "DTEND;TZID=America/New_York:20261005T094500",
                     "SUMMARY:Stand\\, up", "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261231T235959Z",
                     "EXDATE;TZID=America/New_York:20261014T093000",
                     "BEGIN:VALARM", "SUMMARY:alarm text", "END:VALARM", uid="standup")
    MOVED = vevent("RECURRENCE-ID;TZID=America/New_York:20261019T093000",
                   "DTSTART;TZID=America/New_York:20261019T110000", "DTEND;TZID=America/New_York:20261019T113000",
                   "SUMMARY:Stand up (moved)", uid="standup")
    CANCELLED = vevent("RECURRENCE-ID;TZID=America/New_York:20261021T093000",
                       "DTSTART;TZID=America/New_York:20261021T093000", "STATUS:CANCELLED", uid="standup")

    def test_R_exp_07_R_exp_08_R_exp_09_overrides(self):
        text = cal(self.STANDUP, self.MOVED, self.CANCELLED)
        got = self.run_cal(text, d(2026, 10, 12), d(2026, 10, 27))
        self.assertEqual([i["start"].strftime("%a %d %H:%M ") + i["title"] for i in got],
                         ["Mon 12 09:30 Stand, up", "Mon 19 11:00 Stand up (moved)", "Mon 26 09:30 Stand, up"])
        self.assertEqual(got[0]["end"] - got[0]["start"], timedelta(minutes=15))

    def test_R_exp_08_invalid_override_skipped(self):
        bad = vevent("RECURRENCE-ID;TZID=America/New_York:20261019T093000",
                     "DTSTART;TZID=America/New_York:20261019T110000", "DTEND;VALUE=DATE:20261020", uid="standup")
        got = self.run_cal(cal(self.STANDUP, bad), d(2026, 10, 19), d(2026, 10, 20))
        self.assertEqual(got, [])

    def test_R_exp_10_R_exp_16_wall_time_across_dst(self):
        got = self.run_cal(cal(self.STANDUP), d(2026, 10, 26), d(2026, 11, 3))
        self.assertEqual([i["start"].astimezone(NY).hour for i in got], [9, 9, 9])
        self.assertEqual([i["start"].astimezone(UTC).hour for i in got], [13, 13, 14])

    def test_R_exp_30_until(self):
        self.assertEqual([x.day for x in self.days(cal(self.STANDUP), d(2026, 12, 28), d(2027, 1, 10))], [28, 30])

    def test_R_exp_11_R_exp_12_R_exp_13_count_with_exdate(self):
        for n, want in ((304, []), (305, [1]), (306, [1, 2])):
            text = cal(vevent("DTSTART;VALUE=DATE:20260101", "RRULE:FREQ=DAILY;COUNT=%d" % n,
                              "EXDATE;VALUE=DATE:20260102"))
            self.assertEqual([x.day for x in self.days(text, d(2026, 11, 1, UTC), d(2026, 11, 4, UTC))], want)

    def test_R_exp_14_seek_bounds(self):
        text = cal(vevent("DTSTART;VALUE=DATE:20260101", "RRULE:FREQ=DAILY;COUNT=306", "EXDATE;VALUE=DATE:20260102"))
        checked, ended = [], []
        real_excluded, real_add = ical._excluded, ical.add

        def excluded(s, xs):
            checked.append(s)
            return real_excluded(s, xs)

        def add(s, n, e):
            ended.append(s)
            return real_add(s, n, e)
        events = ical.parse(text)
        with mock.patch.object(ical, "_excluded", excluded), mock.patch.object(ical, "add", add):
            ical.expand(events, d(2026, 11, 1, UTC), d(2026, 11, 4, UTC))
        self.assertLessEqual(len(checked), 3)
        self.assertLessEqual(len(ended), 3)
        self.assertTrue(all(x >= date(2026, 10, 31) for x in checked + ended))

    def test_R_exp_12_until_kinds(self):
        text = cal(vevent("DTSTART:20261101T090000Z", "RRULE:FREQ=DAILY;UNTIL=20261103"),
                   vevent("DTSTART;VALUE=DATE:20261101", "RRULE:FREQ=DAILY;UNTIL=20261103T000000Z", uid="a"))
        got = self.run_cal(text, d(2026, 11, 1, UTC), d(2026, 11, 10, UTC))
        self.assertEqual(sorted((i["allday"], i["start"].day) for i in got),
                         [(False, 1), (False, 2), (False, 3), (True, 1), (True, 2), (True, 3)])

    def test_R_exp_15_error_isolation(self):
        text = cal(vevent("DTSTART;VALUE=DATE:20261101", "RRULE:FREQ=DAILY;COUNT=abc", "SUMMARY:bad"),
                   vevent("DTSTART;VALUE=DATE:20261101", "SUMMARY:ok", uid="ok"))
        self.assertEqual(self.titles(text, d(2026, 11, 1), d(2026, 11, 3)), ["ok"])

    def test_R_exp_17_floating_keeps_wall_time(self):
        got = self.run_cal(cal(vevent("DTSTART:20260301T090000", "RRULE:FREQ=WEEKLY;COUNT=3")),
                           d(2026, 3, 1), d(2026, 3, 16))
        self.assertEqual([i["start"].hour for i in got], [9, 9, 9])
        self.assertEqual([i["start"].astimezone(UTC).hour for i in got], [14, 13, 13])
        got = self.run_cal(cal(vevent("DTSTART:20261026T090000", "RRULE:FREQ=WEEKLY;COUNT=3")),
                           d(2026, 10, 26), d(2026, 11, 10))
        self.assertEqual([i["start"].astimezone(UTC).hour for i in got], [13, 14, 14])

    def test_R_exp_18_gap_instances_do_not_count(self):
        for start in ("DTSTART:20260301T023000", "DTSTART;TZID=America/New_York:20260301T023000"):
            text = cal(vevent(start, "RRULE:FREQ=WEEKLY;COUNT=3"))
            got = self.run_cal(text, d(2026, 3, 1), d(2026, 4, 1))
            self.assertEqual([(i["start"].day, i["start"].hour) for i in got], [(1, 2), (15, 2), (22, 2)])
            self.assertEqual([x.day for x in self.days(text, d(2026, 3, 9), d(2026, 4, 1))], [15, 22])

    def test_R_exp_19_nonexistent_dtstart_counts(self):
        for c, n in ((1, 1), (55, 55)):
            text = cal(vevent("DTSTART:20260308T023000", "RRULE:FREQ=WEEKLY;COUNT=%d" % c))
            got = self.run_cal(text, d(2026, 3, 8), d(2027, 4, 1))
            self.assertEqual(len(got), n)
            self.assertEqual(got[0]["start"].isoformat(), "2026-03-08T02:30:00-05:00")
        self.assertEqual([i["start"].date() for i in got[-3:]],
                         [date(2027, 3, 7), date(2027, 3, 21), date(2027, 3, 28)])

    def test_R_exp_20_fall_back_first_occurrence(self):
        base = ("DTSTART;TZID=America/New_York:20261025T013000", "RRULE:FREQ=WEEKLY;COUNT=3")
        got = self.run_cal(cal(vevent(*base)), d(2026, 10, 25), d(2026, 11, 9))
        self.assertEqual([i["start"].astimezone(UTC).hour for i in got], [5, 5, 6])
        got = self.run_cal(cal(vevent(*base, "EXDATE:20261101T053000Z")), d(2026, 10, 25), d(2026, 11, 9))
        self.assertEqual([i["start"].day for i in got], [25, 8])

    def test_R_exp_21_exact_hours(self):
        for start, end in (("20260308T013000", "2026-03-08T04:30:00-04:00"),
                           ("20261101T003000", "2026-11-01T01:30:00-05:00")):
            [i] = self.run_cal(cal(vevent("DTSTART:" + start, "DURATION:PT2H")), d(2026, 1, 1), d(2027, 1, 1))
            self.assertEqual(i["end"].isoformat(), end)
            self.assertEqual(i["end"].timestamp() - i["start"].timestamp(), 7200)

    def test_R_exp_22_dtend_durations_exact(self):
        rows = (("20260301T013000", "20260301T033000", "2026-03-08T04:30:00-04:00", 2),
                ("20261025T003000", "20261025T023000", "2026-11-01T01:30:00-05:00", 2),
                ("20260308T013000", "20260308T033000", "2026-03-15T02:30:00-04:00", 1),
                ("20261101T003000", "20261101T023000", "2026-11-08T03:30:00-05:00", 3))
        for start, end, second, hours in rows:
            text = cal(vevent("DTSTART:" + start, "DTEND:" + end, "RRULE:FREQ=WEEKLY;COUNT=2"))
            s = ical.parse(text)[0].start
            got = self.run_cal(text, s, s + timedelta(days=8))
            self.assertEqual(len(got), 2)
            self.assertEqual(got[1]["end"].isoformat(), second)
            for i in got:
                self.assertEqual(i["end"].timestamp() - i["start"].timestamp(), hours * 3600, start)

    def test_R_exp_23_nominal_vs_exact(self):
        for start, delta in (("20260301T013000", -1), ("20261025T003000", 1)):
            for dur, hours, wall_days in (("P1D", 24 + delta, 1), ("PT24H", 24, None),
                                          ("P1W", 168 + delta, 7), ("PT168H", 168, None)):
                text = cal(vevent("DTSTART:" + start, "DURATION:" + dur, "RRULE:FREQ=WEEKLY;COUNT=2"))
                s = ical.parse(text)[0].start
                second = self.run_cal(text, s, s + timedelta(days=8))[1]
                elapsed = (second["end"].timestamp() - second["start"].timestamp()) / 3600
                self.assertEqual(elapsed, hours, (start, dur))
                if wall_days:
                    self.assertEqual(second["end"].replace(tzinfo=None),
                                     second["start"].replace(tzinfo=None) + timedelta(days=wall_days))

    def test_R_exp_24_R_ical_11_mixed_duration(self):
        for start, end in (("20260307T013000", "2026-03-08T04:30:00-04:00"),
                           ("20261031T003000", "2026-11-01T01:30:00-05:00")):
            text = cal(vevent("DTSTART;TZID=America/New_York:" + start, "DURATION:P1DT2H"))
            [i] = self.run_cal(text, d(2026, 1, 1), d(2027, 1, 1))
            self.assertEqual(i["end"].isoformat(), end)

    def test_R_exp_25_overlap_in_repeated_hour(self):
        text = cal(vevent("DTSTART:20261101T013000", "DURATION:PT45M"))
        lo = d(2026, 11, 1, h=1, fold=1)
        [i] = self.run_cal(text, lo, lo.astimezone(UTC) + timedelta(hours=1))
        self.assertEqual(i["end"].isoformat(), "2026-11-01T01:15:00-05:00")

    def test_R_exp_26_long_instances_before_window(self):
        for count in ("", ";COUNT=30000"):
            text = cal(vevent("DTSTART;VALUE=DATE:19700101", "DURATION:P3D", "RRULE:FREQ=DAILY" + count))
            self.assertEqual(self.days(text, d(2026, 11, 1), d(2026, 11, 3)),
                             [date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)])

    def test_R_exp_27_nominal_end_in_skipped_date(self):
        apia = ZoneInfo("Pacific/Apia")
        for count in ("", ";COUNT=1000"):
            text = cal(vevent("DTSTART;TZID=Pacific/Apia:20111215T210200", "DURATION:P1D", "RRULE:FREQ=DAILY" + count))
            got = self.run_cal(text, datetime(2011, 12, 31, 15, tzinfo=apia), datetime(2012, 1, 2, 15, tzinfo=apia))
            self.assertEqual([i["start"].date() for i in got],
                             [date(2011, 12, 29), date(2011, 12, 31), date(2012, 1, 1)])
            self.assertEqual(got[0]["end"].isoformat(), "2011-12-31T21:02:00+14:00")

    def test_R_exp_28_zero_length_at_window_start(self):
        for start in ("DTSTART;VALUE=DATE:20261031", "DTSTART:20261031T000000Z"):
            text = cal(vevent(start, "DURATION:P0D", "RRULE:FREQ=DAILY;COUNT=2"))
            got = self.run_cal(text, d(2026, 11, 1, UTC), d(2026, 11, 2, UTC))
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["start"], got[0]["end"])
            self.assertEqual((got[0]["start"].month, got[0]["start"].day), (11, 1))

    def test_R_exp_29_moved_override_with_count(self):
        text = cal(vevent("DTSTART;VALUE=DATE:20261101", "RRULE:FREQ=DAILY;COUNT=3", "EXDATE;VALUE=DATE:20261101"),
                   vevent("RECURRENCE-ID;VALUE=DATE:20261102", "DTSTART;VALUE=DATE:20261104"))
        self.assertEqual(self.days(text, d(2026, 11, 3), d(2026, 11, 5)), [date(2026, 11, 3), date(2026, 11, 4)])


class Rules(TzCase):
    tz = "UTC"

    def window(self, rule, start, lo, hi, extra=()):
        text = cal(vevent(start, "RRULE:" + rule, *extra))
        return self.days(text, lo, hi)

    def test_R_rrule_01_unsupported_parts(self):
        for rule in ("FREQ=DAILY;BYHOUR=9", "FREQ=DAILY;BYMINUTE=0", "FREQ=DAILY;BYSECOND=0",
                     "FREQ=YEARLY;BYWEEKNO=45", "FREQ=YEARLY;BYYEARDAY=305", "FREQ=WEEKLY;RSCALE=GREGORIAN",
                     "FREQ=HOURLY;INTERVAL=6"):
            text = cal(vevent("DTSTART:20261101T090000Z", "RRULE:%s;COUNT=10" % rule))
            got = self.run_cal(text, d(2026, 11, 1, UTC), d(2026, 11, 10, UTC))
            self.assertEqual([i["start"] for i in got], [datetime(2026, 11, 1, 9, tzinfo=UTC)], rule)
            self.assertEqual(self.run_cal(text, d(2026, 11, 2, UTC), d(2026, 11, 10, UTC)), [], rule)

    def test_R_rrule_02_validation(self):
        for part in ("INTERVAL=abc", "INTERVAL=0", "COUNT=abc", "UNTIL=broken", "BYMONTH=0", "BYMONTH=13",
                     "BYMONTH=abc", "BYMONTHDAY=32", "BYSETPOS=0", "BYSETPOS=367", "BYSETPOS=abc"):
            text = cal(vevent("DTSTART;VALUE=DATE:20261101", "RRULE:FREQ=DAILY;" + part, "SUMMARY:bad"),
                       vevent("DTSTART;VALUE=DATE:20261101", "SUMMARY:ok", uid="ok"))
            self.assertEqual(self.titles(text, d(2026, 11, 1, UTC), d(2026, 11, 3, UTC)), ["ok"], part)
        got = self.window("FREQ=WEEKLY;BYDAY=XX,MO,0TU;WKST=ZZ", "DTSTART;VALUE=DATE:20261102",
                          d(2026, 11, 1, UTC), d(2026, 11, 15, UTC))
        self.assertEqual([x.day for x in got], [2, 9])

    def test_R_rrule_02_unsupported_rules_skip_validation(self):
        # G-core-2: only COUNT and UNTIL are checked when the rule yields DTSTART alone
        for rule in ("FREQ=HOURLY;INTERVAL=0", "FREQ=WEEKLY;INTERVAL=0;BYHOUR=9", "FREQ=HOURLY;BYMONTHDAY=0",
                     "FREQ=SECONDLY;BYMONTH=13;BYSETPOS=abc", "FREQ=DAILY;BYMINUTE=0;BYSETPOS=0"):
            got = self.window(rule, "DTSTART;VALUE=DATE:20261101", d(2026, 11, 1, UTC), d(2026, 11, 3, UTC))
            self.assertEqual(got, [date(2026, 11, 1)], rule)
        for rule in ("FREQ=HOURLY;COUNT=0", "FREQ=HOURLY;COUNT=abc", "FREQ=DAILY;BYHOUR=9;UNTIL=broken"):
            text = cal(vevent("DTSTART;VALUE=DATE:20261101", "RRULE:" + rule, "SUMMARY:bad"),
                       vevent("DTSTART;VALUE=DATE:20261101", "SUMMARY:ok", uid="ok"))
            self.assertEqual(self.titles(text, d(2026, 11, 1, UTC), d(2026, 11, 3, UTC)), ["ok"], rule)

    def test_R_rrule_03_huge_interval(self):
        for freq in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
            got = self.window("FREQ=%s;INTERVAL=999999999999999999999999" % freq, "DTSTART;VALUE=DATE:20261101",
                              d(2026, 11, 1, UTC), d(2026, 11, 3, UTC))
            self.assertEqual(got, [date(2026, 11, 1)], freq)

    def test_R_rrule_04_not_before_dtstart_and_not_forced(self):
        got = self.window("FREQ=WEEKLY;BYDAY=MO", "DTSTART;VALUE=DATE:20261104",
                          d(2026, 10, 1, UTC), d(2026, 11, 17, UTC))
        self.assertEqual(got, [date(2026, 11, 9), date(2026, 11, 16)])

    def test_R_rrule_05_arithmetic_period_range(self):
        seen = []
        real = ical._candidates

        def spy(rule, start, period):
            seen.append(period)
            return real(rule, start, period)
        with mock.patch.object(ical, "_candidates", spy):
            got = self.window("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30", "DTSTART;VALUE=DATE:00010201",
                              d(2026, 1, 1, UTC), d(2028, 1, 1, UTC))
        self.assertEqual(got, [])
        self.assertLessEqual(len(seen), 3)
        self.assertTrue(all(y <= 2027 for _, y in seen))

    def test_R_rrule_06_R_rrule_07_R_rrule_08_examples(self):
        U = UTC
        cases = [
            ("FREQ=MONTHLY;BYDAY=-1FR;COUNT=3", "DTSTART:20260925T120000Z", (2026, 9, 1), (2027, 2, 1),
             ["2026-09-25", "2026-10-30", "2026-11-27"]),
            ("FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=1,-1", "DTSTART;VALUE=DATE:20260115", (2026, 9, 1), (2027, 2, 1),
             ["2026-09-01", "2026-09-30", "2026-11-01", "2026-11-30", "2027-01-01", "2027-01-31"]),
            ("FREQ=DAILY;INTERVAL=3", "DTSTART:20261101T080000Z", (2026, 11, 1), (2026, 11, 10),
             ["2026-11-01", "2026-11-04", "2026-11-07"]),
            ("FREQ=YEARLY", "DTSTART;VALUE=DATE:20200229", (2027, 1, 1), (2029, 1, 1), ["2028-02-29"]),
            ("FREQ=DAILY;BYMONTHDAY=31", "DTSTART;VALUE=DATE:20260131", (2026, 2, 1), (2026, 3, 1), []),
            ("FREQ=DAILY;BYMONTHDAY=-1", "DTSTART;VALUE=DATE:20260131", (2026, 2, 1), (2026, 3, 1), ["2026-02-28"]),
            ("FREQ=WEEKLY;BYMONTH=2", "DTSTART;VALUE=DATE:20260202", (2026, 3, 1), (2026, 3, 10), []),
            ("FREQ=WEEKLY;BYMONTH=2", "DTSTART;VALUE=DATE:20260202", (2026, 2, 1), (2026, 2, 10),
             ["2026-02-02", "2026-02-09"]),
            ("FREQ=YEARLY;BYMONTH=12,1,1;COUNT=3", "DTSTART;VALUE=DATE:20250101", (2026, 1, 1), (2026, 2, 1),
             ["2026-01-01"]),
            ("FREQ=YEARLY;BYMONTH=12,1,1;COUNT=3", "DTSTART;VALUE=DATE:20250101", (2025, 1, 1), (2027, 1, 1),
             ["2025-01-01", "2025-12-01", "2026-01-01"]),
            ("FREQ=WEEKLY;INTERVAL=2;BYDAY=SU,MO", "DTSTART;VALUE=DATE:20260104", (2026, 1, 1), (2026, 1, 20),
             ["2026-01-04", "2026-01-12", "2026-01-18"]),
            ("FREQ=WEEKLY;INTERVAL=2;BYDAY=SU,MO;WKST=SU", "DTSTART;VALUE=DATE:20260104", (2026, 1, 1),
             (2026, 1, 20), ["2026-01-04", "2026-01-05", "2026-01-18", "2026-01-19"]),
            ("FREQ=DAILY", "DTSTART;VALUE=DATE:19700101", (2026, 11, 1), (2026, 11, 3), ["2026-11-01", "2026-11-02"]),
            ("FREQ=DAILY;COUNT=30000", "DTSTART;VALUE=DATE:19700101", (2026, 11, 1), (2026, 11, 3),
             ["2026-11-01", "2026-11-02"]),
            ("FREQ=DAILY;BYMONTH=2;BYMONTHDAY=-1;COUNT=58", "DTSTART;VALUE=DATE:19700228", (2026, 2, 1),
             (2028, 3, 1), ["2026-02-28", "2027-02-28"]),
            ("FREQ=YEARLY;BYDAY=20MO;COUNT=2", "DTSTART;VALUE=DATE:20260518", (2026, 1, 1), (2029, 1, 1),
             ["2026-05-18", "2027-05-17"]),
            ("FREQ=YEARLY;BYDAY=-1MO;COUNT=2", "DTSTART;VALUE=DATE:20260518", (2026, 1, 1), (2029, 1, 1),
             ["2026-12-28", "2027-12-27"]),
            ("FREQ=YEARLY;BYDAY=53MO;COUNT=2", "DTSTART;VALUE=DATE:20241230", (2025, 1, 1), (2031, 1, 1),
             ["2029-12-31"]),
            ("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=3", "DTSTART;VALUE=DATE:20260930", (2026, 10, 1),
             (2027, 6, 1), ["2026-10-30", "2026-11-30"]),
            ("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=1", "DTSTART;VALUE=DATE:20260901", (2026, 9, 22),
             (2026, 10, 6), ["2026-09-28", "2026-10-05"]),
            ("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYMONTH=9;BYSETPOS=-1", "DTSTART;VALUE=DATE:20260901", (2026, 9, 21),
             (2026, 10, 10), ["2026-09-25", "2026-09-30"]),
            ("FREQ=YEARLY;BYMONTH=3,9;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=1,-1", "DTSTART;VALUE=DATE:20260302",
             (2026, 1, 1), (2028, 1, 1), ["2026-03-02", "2026-09-30", "2027-03-01", "2027-09-30"]),
        ]
        for rule, start, lo, hi, want in cases:
            got = self.window(rule, start, d(*lo, U), d(*hi, U))
            self.assertEqual([x.isoformat() for x in got], want, rule)
        for freq in ("FREQ=MONTHLY", "FREQ=YEARLY;BYMONTH=11"):
            for parts, want in (("BYDAY=FR;BYMONTHDAY=13", [13]), ("BYDAY=FR;BYMONTHDAY=-4", [27]),
                                ("BYDAY=2FR;BYMONTHDAY=12", []), ("BYDAY=-1FR;BYMONTHDAY=-4", [27])):
                got = self.window("%s;%s" % (freq, parts), "DTSTART;VALUE=DATE:20261113",
                                  d(2026, 11, 1, U), d(2026, 12, 1, U))
                self.assertEqual([x.day for x in got], want, (freq, parts))
        got = self.window("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=3", "DTSTART;VALUE=DATE:20260930",
                          d(2026, 10, 1, U), d(2027, 6, 1, U), ["EXDATE;VALUE=DATE:20261030"])
        self.assertEqual(got, [date(2026, 11, 30)])

    def test_R_rrule_08_monthly_bysetpos_in_zone(self):
        start = 'DTSTART;TZID="W. Europe Standard Time":20260930T170000'
        for parts, want in (("BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1", ["10-30", "11-30", "12-31"]),
                            ("BYDAY=MO;BYSETPOS=1", ["10-05", "11-02", "12-07"]),
                            ("BYDAY=MO,TU,WE,TH,FR;BYSETPOS=1,-1",
                             ["10-01", "10-30", "11-02", "11-30", "12-01", "12-31"]),
                            ("BYDAY=MO,TU,WE,TH,FR;BYSETPOS=23", ["12-31"])):
            text = cal(vevent(start, "RRULE:FREQ=MONTHLY;" + parts))
            got = self.run_cal(text, d(2026, 10, 1, UTC), d(2027, 1, 1, UTC))
            self.assertEqual([i["start"].strftime("%m-%d") for i in got], want, parts)
            self.assertTrue(all(i["start"].hour == 17 and str(i["start"].tzinfo) == "Europe/Berlin" for i in got))


class Instances(TzCase):
    def test_R_inst_01_R_inst_02_window_and_output(self):
        text = cal(vevent("DTSTART;VALUE=DATE:20260307", "RRULE:FREQ=DAILY", "SUMMARY:daily"),
                   vevent("DTSTART:20260307T150000Z", "DURATION:PT1H", "SUMMARY:timed", uid="t"))
        now = datetime(2026, 3, 7, 12, tzinfo=NY).timestamp()
        got = ical.instances(text, days=3, now=now)
        self.assertEqual([i["start"] for i in got if i["allday"]], ["2026-03-07", "2026-03-08", "2026-03-09"])
        timed = [i for i in got if not i["allday"]][0]
        self.assertEqual(timed, {"uid": "t", "title": "timed", "start": datetime(2026, 3, 7, 15, tzinfo=UTC)
                                 .timestamp(), "end": datetime(2026, 3, 7, 16, tzinfo=UTC).timestamp(),
                                 "allday": False})

    def test_R_inst_02_window_length(self):
        spans = []
        real = ical.expand

        def spy(events, lo, hi, zone):
            spans.append(((hi - ical.EPOCH) - (lo - ical.EPOCH)) / timedelta(hours=1))
            return real(events, lo, hi, zone)
        with mock.patch.object(ical, "expand", spy):
            ical.instances(cal(), 3, datetime(2026, 3, 7, 12, tzinfo=NY).timestamp())
            ical.instances(cal(), 3, datetime(2026, 10, 31, 12, tzinfo=NY).timestamp())
        self.assertEqual(spans, [71, 73])


if __name__ == "__main__":
    unittest.main()
