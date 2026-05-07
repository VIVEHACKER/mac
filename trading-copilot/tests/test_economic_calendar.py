from datetime import date, datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from trading_copilot.economic_calendar import (
    EconomicEvent,
    KnownBls2026CalendarProvider,
    StaticEconomicCalendarProvider,
    build_economic_calendar_report,
    collect_economic_events,
    format_economic_calendar_report,
    parse_bls_ics,
    parse_fomc_calendar_html,
)


class EconomicCalendarTests(unittest.TestCase):
    def test_parse_bls_ics_filters_market_moving_releases(self):
        events = parse_bls_ics(BLS_ICS_SAMPLE, source="https://www.bls.gov/schedule/news_release/bls.ics")

        self.assertEqual(len(events), 3)
        self.assertEqual(events[0].name, "Employment Situation")
        self.assertEqual(events[0].category, "labor")
        self.assertEqual(events[0].scheduled_for, datetime(2026, 6, 5, 8, 30, tzinfo=timezone.utc))
        self.assertEqual(events[1].name, "Consumer Price Index")
        self.assertEqual(events[2].name, "Job Openings and Labor Turnover Survey")

    def test_parse_fomc_calendar_html_uses_decision_day(self):
        events = parse_fomc_calendar_html(FOMC_HTML_SAMPLE, year=2026)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].name, "FOMC Rate Decision")
        self.assertEqual(events[0].category, "fed")
        self.assertEqual(events[0].scheduled_for, datetime(2026, 6, 17, 14, 0, tzinfo=ZoneInfo("America/New_York")))
        self.assertIn("June 16-17", events[0].notes)

    def test_collect_economic_events_sorts_and_filters_horizon(self):
        provider = StaticEconomicCalendarProvider(
            (
                event("FOMC Rate Decision", "fed", datetime(2026, 6, 17, 14, 0, tzinfo=timezone.utc)),
                event("Employment Situation", "labor", datetime(2026, 6, 5, 8, 30, tzinfo=timezone.utc)),
                event("Too Late", "labor", datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)),
            )
        )

        result = collect_economic_events(
            providers=(provider,),
            start=date(2026, 5, 7),
            days=60,
        )

        self.assertEqual([item.name for item in result.events], ["Employment Situation", "FOMC Rate Decision"])
        self.assertEqual(result.data_gaps, ())

    def test_known_bls_2026_provider_covers_critical_release_types(self):
        provider = KnownBls2026CalendarProvider()

        events = provider.upcoming_events(start=date(2026, 5, 7), days=40)

        names = {event.name for event in events}
        self.assertIn("Employment Situation", names)
        self.assertIn("Consumer Price Index", names)
        self.assertIn("Producer Price Index", names)
        self.assertIn("Job Openings and Labor Turnover Survey", names)
        self.assertTrue(all("bls.gov/schedule/news_release" in event.source for event in events))

    def test_format_economic_calendar_report(self):
        report = format_economic_calendar_report(
            build_economic_calendar_report(
                events=(event("Employment Situation", "labor", datetime(2026, 6, 5, 8, 30, tzinfo=timezone.utc)),),
                data_gaps=("BLS unavailable",),
                start=date(2026, 5, 7),
                end=date(2026, 7, 6),
            )
        )

        self.assertIn("# Economic Release Calendar", report)
        self.assertIn("Employment Situation", report)
        self.assertIn("BLS unavailable", report)
        self.assertIn("Not investment advice", report)


def event(name: str, category: str, scheduled_for: datetime) -> EconomicEvent:
    return EconomicEvent(
        name=name,
        category=category,
        importance="high",
        scheduled_for=scheduled_for,
        source="fixture",
        notes="fixture note",
    )


BLS_ICS_SAMPLE = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Employment Situation
DTSTART:20260605T083000Z
URL:https://www.bls.gov/news.release/empsit.nr0.htm
END:VEVENT
BEGIN:VEVENT
SUMMARY:Consumer Price Index
DTSTART:20260610T083000Z
URL:https://www.bls.gov/news.release/cpi.nr0.htm
END:VEVENT
BEGIN:VEVENT
SUMMARY:Job Openings and Labor Turnover Survey
DTSTART:20260630T100000Z
URL:https://www.bls.gov/news.release/jolts.nr0.htm
END:VEVENT
BEGIN:VEVENT
SUMMARY:County Employment and Wages
DTSTART:20260630T100000Z
URL:https://www.bls.gov/news.release/cewqtr.nr0.htm
END:VEVENT
END:VCALENDAR
"""


FOMC_HTML_SAMPLE = """
<div class="panel-heading"><h4><a id="42828">2026 FOMC Meetings</a></h4></div>
<div class="row fomc-meeting">
  <div class="fomc-meeting__month"><strong>June</strong></div>
  <div class="fomc-meeting__date">16-17*</div>
</div>
<div class="row fomc-meeting">
  <div class="fomc-meeting__month"><strong>July</strong></div>
  <div class="fomc-meeting__date">28-29</div>
</div>
"""


if __name__ == "__main__":
    unittest.main()
