"""Wifi presence (eve, through Home Assistant's history) -> proposed punches, day by day.

The phone joining the home wifi is not a punch: these are proposals the employer confirms
one by one, recorded through the ordinary punch path (validation, EXTRA, shortfalls).
"""

from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from tata.models import MINUTES_PER_DAY, Frozen, Span

ROME = ZoneInfo("Europe/Rome")
DEPARTURE = timedelta(seconds=120)  # eve's departure_timeout: not_home comes this long after
# the phone was last seen on the exit AP. Must match eve's config.
RESTART_GRACE = timedelta(minutes=5)  # not_home after unknown, home again within this: restart
NOON = 12 * 60  # arrivals before noon are the morning (school run), after it the afternoon
WALK = 30  # morning: minutes still worked after leaving the house (taking the children to school)
AFTERNOON_START = 16 * 60  # afternoon: she picks the children up at school, then comes home


class Seen(StrEnum):
    HOME = "home"
    AWAY = "not_home"
    UNKNOWN = "unknown"  # unavailable/unknown: Home Assistant or eve restarting


class Change(Frozen):
    at: datetime  # timezone-aware
    seen: Seen


class History(Frozen):
    initial: Seen  # the state at the start of the period
    changes: list[Change]


class Session(Frozen):
    """One stay at home within a day, in minutes from midnight."""

    arrival: int | None  # None: already home at midnight
    departure: int | None  # None: still home at midnight or now, or `lost`
    lost: bool  # she left while Home Assistant was down: the time is unknown


class PresenceDay(Frozen):
    day: date
    sessions: list[Session]
    proposals: list[Span]


class _Stay(Frozen):
    arrival: datetime | None
    departure: datetime | None  # the moment she left, or the restart that hid it if lost
    lost: bool


def presence_days(history: History, first: date, last: date) -> list[PresenceDay]:
    """One PresenceDay per calendar day first..last, with the punches the rules propose.

    Morning (arrival before 12:00): from the arrival to the departure + 30 min. Afternoon: the
    first arrival counts from 16:00 (or earlier, if she came earlier), later ones from their
    own arrival; each ends at the departure. Stays with an unknown end are never proposed.
    """
    stays = _stays(history)
    result: list[PresenceDay] = []
    day = first
    while day <= last:
        sessions = _sessions(stays, day)
        result.append(PresenceDay(day=day, sessions=sessions, proposals=_proposals(sessions)))
        day += timedelta(days=1)
    return result


def _stays(history: History) -> list[_Stay]:
    changes = sorted(history.changes, key=lambda c: c.at)
    stays: list[_Stay] = []
    home = history.initial is Seen.HOME
    since: datetime | None = None
    prev = history.initial
    for i, c in enumerate(changes):
        if c.seen is Seen.HOME and not home:
            home, since = True, c.at
        elif c.seen is Seen.AWAY and home and prev is Seen.HOME:
            left = c.at - DEPARTURE
            stays.append(_Stay(arrival=since, departure=max(left, since or left), lost=False))
            home = False
        elif c.seen is Seen.AWAY and home and prev is Seen.UNKNOWN:
            back = changes[i + 1] if i + 1 < len(changes) else None
            if not (back and back.seen is Seen.HOME and back.at - c.at <= RESTART_GRACE):
                stays.append(_Stay(arrival=since, departure=c.at, lost=True))
                home = False
        prev = c.seen
    if home:
        stays.append(_Stay(arrival=since, departure=None, lost=False))
    return stays


def _sessions(stays: list[_Stay], day: date) -> list[Session]:
    start = datetime.combine(day, time(0), ROME)
    end = datetime.combine(day + timedelta(days=1), time(0), ROME)
    sessions: list[Session] = []
    for s in stays:
        if (s.arrival is not None and s.arrival >= end) or (
            s.departure is not None and s.departure < start
        ):
            continue
        arrival = _minute(s.arrival) if s.arrival is not None and s.arrival >= start else None
        ends_today = s.departure is not None and s.departure < end
        departure = _minute(s.departure) if s.departure and ends_today and not s.lost else None
        sessions.append(Session(arrival=arrival, departure=departure, lost=s.lost and ends_today))
    return sessions


def _proposals(sessions: list[Session]) -> list[Span]:
    minutes: set[int] = set()
    first_afternoon = True
    for s in sessions:
        afternoon = s.arrival is not None and s.arrival >= NOON
        if s.arrival is not None and s.departure is not None:
            if not afternoon:
                start, end = s.arrival, min(s.departure + WALK, MINUTES_PER_DAY)
            elif first_afternoon:
                start, end = min(s.arrival, AFTERNOON_START), s.departure
            else:
                start, end = s.arrival, s.departure
            minutes |= set(range(start, end))
        if afternoon:
            first_afternoon = False
    return _runs(minutes)


def _runs(minutes: set[int]) -> list[Span]:
    ordered = sorted(minutes)
    result: list[Span] = []
    for m in ordered:
        if result and result[-1].end == m:
            result[-1] = Span(start=result[-1].start, end=m + 1)
        else:
            result.append(Span(start=m, end=m + 1))
    return result


def _minute(moment: datetime) -> int:
    local = moment.astimezone(ROME)
    return local.hour * 60 + local.minute
