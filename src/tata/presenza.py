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
# Two shifts. A stay belongs to the shift of its arrival: morning before noon (or already home
# at midnight), afternoon from noon on.
NOON = 12 * 60
MORNING_FROM = 7 * 60 + 30  # morning start: the first arrival in MORNING_FROM..MORNING_TO
MORNING_TO = 8 * 60 + 15  # ...and a departure by then is the school run on foot: + WALK
WALK = 30  # minutes still worked after leaving the house, taking the children to school
AFTERNOON_START = 15 * 60 + 45  # she picks the children up; an earlier arrival counts from itself


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


class Anomaly(StrEnum):
    """Why a day does not fit the two shifts: the employer enters it by hand."""

    NO_MORNING = "nessun arrivo tra 7:30 e 8:15"
    STRAY = "uscita fuori turno"
    NO_END = "turno senza uscita"
    LOST = "uscita ignota (Home Assistant giù)"


class PresenceDay(Frozen):
    day: date
    sessions: list[Session]
    proposals: list[Span]
    anomalies: list[Anomaly]


class _Stay(Frozen):
    arrival: datetime | None
    departure: datetime | None  # the moment she left, or the restart that hid it if lost
    lost: bool


def presence_days(history: History, first: date, last: date) -> list[PresenceDay]:
    """One PresenceDay per calendar day first..last, with the punches the shifts propose.

    Morning: from the first arrival in 07:30-08:15 to the end of the last morning stay, + 30 min
    if that is by 08:15. Afternoon: from the first arrival, or 15:45 if later, to the end of
    the last afternoon stay. What lies in between is ignored; a shift whose end is unknown or
    past noon proposes nothing, and the day lists its anomalies.
    """
    stays = _stays(history)
    result: list[PresenceDay] = []
    day = first
    while day <= last:
        sessions = _sessions(stays, day)
        proposals, anomalies = _shifts(sessions)
        result.append(
            PresenceDay(day=day, sessions=sessions, proposals=proposals, anomalies=anomalies)
        )
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


def _shifts(sessions: list[Session]) -> tuple[list[Span], list[Anomaly]]:
    anomalies: list[Anomaly] = []
    proposals: list[Span] = []
    morning = [s for s in sessions if s.arrival is None or s.arrival < NOON]
    afternoon = [s for s in sessions if s.arrival is not None and s.arrival >= NOON]
    starts = [s.arrival for s in morning if s.arrival is not None and s.arrival >= MORNING_FROM]
    start = min(starts, default=None)
    if start is None or start > MORNING_TO:
        if sessions:  # a day without wifi is a day off, nothing to fix
            anomalies.append(Anomaly.NO_MORNING)
        start = MINUTES_PER_DAY  # every morning stay is before the shift
    shift = [s for s in morning if s.arrival is not None and s.arrival >= start]
    if any(s.departure is not None for s in morning if s not in shift):
        anomalies.append(Anomaly.STRAY)
    if shift and (end := _end(shift[-1], NOON, anomalies)) is not None:
        proposals.append(Span(start=start, end=end + WALK if end <= MORNING_TO else end))
    if afternoon and (end := _end(afternoon[-1], MINUTES_PER_DAY, anomalies)) is not None:
        start = min(afternoon[0].arrival or NOON, AFTERNOON_START)
        if end > start:  # in and out within a minute of 15:45
            proposals.append(Span(start=start, end=end))
    if any(s.lost for s in sessions):
        anomalies.append(Anomaly.LOST)
    return proposals, list(dict.fromkeys(anomalies))  # NO_END may come from both shifts


def _end(last: Session, before: int, anomalies: list[Anomaly]) -> int | None:
    """A shift's end: its last stay's departure, if known and before `before`."""
    if last.lost:
        return None  # Anomaly.LOST, once per day
    if last.departure is None or last.departure >= before:
        anomalies.append(Anomaly.NO_END)
        return None
    return last.departure


def _minute(moment: datetime) -> int:
    local = moment.astimezone(ROME)
    return local.hour * 60 + local.minute
