"""Time in the demo: the UK time zone, and the demo clock (guide ch 2, 9).

Two rules, both kept here so nothing else has to know them:

1. Facts are stored as instants in UTC (TIMESTAMPTZ in the warehouse).
   People read UK time, so every time shown is converted to Europe/London
   and printed with its zone: "06:00 BST on Friday 4 September 2026".

2. The demo has a fixed "today", the case's go-live and replay date, Friday
   2 October 2026. Version status (version 1 in force until 5 October,
   version 2 from then) and holds are decided against this date, never the
   computer's clock, so the demo answers the same way whenever it is run.
   `sl ask --as-of 2026-10-06` moves the clock for one command.
"""

from datetime import date, datetime

from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")

DEMO_AS_OF = date(2026, 10, 2)          # the one place the demo's "today" is set

_moved_to = None                         # set by --as-of for one command


def today():
    """The demo clock: DEMO_AS_OF unless a command set another date."""
    return _moved_to or DEMO_AS_OF


def set_as_of(day):
    """Move the demo clock (a date or 'YYYY-MM-DD'); None puts it back."""
    global _moved_to
    _moved_to = date.fromisoformat(day) if isinstance(day, str) else day


def describe_clock():
    """'As of 2 October 2026 (demo clock)', for the top of an answer."""
    moved = "" if today() == DEMO_AS_OF else ", set by --as-of"
    return f"As of {long_date(today())} (demo clock{moved})"


def long_date(day):
    """date(2026, 10, 2) -> '2 October 2026'."""
    return f"{day.day} {day:%B %Y}"


def local(local_text, zone="Europe/London"):
    """A wall-clock time in a zone ('2026-09-04 06:00:00') as an aware datetime."""
    return datetime.fromisoformat(local_text).replace(tzinfo=ZoneInfo(zone))


def uk_time(moment):
    """An instant (aware datetime or ISO text) in UK time, with its zone:
    '2026-09-04T05:00:00+00:00' -> '06:00 BST on Friday 4 September 2026'."""
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    uk = moment.astimezone(UK)
    return f"{uk:%H:%M} {uk.tzname()} on {uk:%A} {long_date(uk)}"
