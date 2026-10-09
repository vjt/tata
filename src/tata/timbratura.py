"""Cartellino: what the punches imply for payroll, and what the button proposes."""

from datetime import date, datetime, timedelta
from enum import StrEnum
from itertools import pairwise

from tata.festivita import festivita
from tata.hours import scheduled_minutes
from tata.models import MINUTES_PER_DAY, Contract, Event, EventKind, Frozen, Punch, Span

TOLERANCE = 5  # minutes per gap or overrun ignored: nobody gets docked for parking
WINDOW = 30  # minutes around a span's end in which the button proposes to confirm it
COVERING = frozenset({EventKind.ASSENZA, EventKind.PERMESSO, EventKind.MORA})


class Action(StrEnum):
    INIZIO = "inizio"
    FINE = "fine"
    CONFERMA = "conferma"


class Proposal(Frozen):
    action: Action
    span: Span | None  # the scheduled span to confirm (CONFERMA only)


class Shortfall(Frozen):
    """Scheduled time with no punch and no event: the employer must say what it was."""

    day: date
    span: Span


def check_punches(punches: list[Punch]) -> None:
    """Raise ValueError if more than one punch is open or two punches of a day overlap."""
    if len([p for p in punches if p.end is None]) > 1:
        raise ValueError("c'è già una timbratura aperta")
    by_day: dict[date, list[Punch]] = {}
    for p in punches:
        by_day.setdefault(p.day, []).append(p)
    for day, day_punches in by_day.items():
        ordered = sorted(day_punches, key=lambda p: p.start)
        for a, b in pairwise(ordered):
            if a.end is None or a.end > b.start:
                raise ValueError(f"timbrature sovrapposte il {day}")


def overruns(contract: Contract, punch: Punch) -> list[Span]:
    """Punched minutes outside the schedule: the EXTRA events a closed punch implies."""
    holiday = punch.day in _holidays(contract, punch.day.year)
    return _runs(punch.span().minutes() - scheduled_minutes(contract, punch.day, holiday))


def shortfalls(
    contract: Contract, events: list[Event], punches: list[Punch], now: datetime
) -> list[Shortfall]:
    """Ended scheduled time no punch and no event explains, from the first punch ever to now.

    Derived, never stored: an ASSENZA, PERMESSO or MORA over the gap, or any whole-day event,
    is what clears it. An open punch covers the rest of its day.
    """
    if not punches:
        return []
    today, minute = now.date(), now.hour * 60 + now.minute
    result: list[Shortfall] = []
    day = min(p.day for p in punches)
    while day <= today:
        ended: set[int] = set()
        for s in contract.orario[day.weekday()]:
            if day < today or s.end <= minute:
                ended |= s.minutes()
        holiday = day in _holidays(contract, day.year)
        missing = scheduled_minutes(contract, day, holiday) & ended
        for ev in events:
            if ev.day == day and ev.span is None:
                missing = set[int]()
            elif ev.day == day and ev.span is not None and ev.kind in COVERING:
                missing -= ev.span.minutes()
        for p in punches:
            if p.day == day:
                missing -= set(range(p.start, MINUTES_PER_DAY if p.end is None else p.end))
        result += [Shortfall(day=day, span=s) for s in _runs(missing)]
        day += timedelta(days=1)
    return result


def propose(contract: Contract, punches: list[Punch], now: datetime) -> Proposal:
    """What the big button offers at `now` (spec §3)."""
    if any(p.end is None for p in punches):
        return Proposal(action=Action.FINE, span=None)
    today, minute = now.date(), now.hour * 60 + now.minute
    if contract.employed_on(today) and today not in _holidays(contract, today.year):
        recorded: set[int] = set()
        for p in punches:
            if p.day == today:
                recorded |= p.span().minutes()
        for s in contract.orario[today.weekday()]:
            near_end = max(s.end - WINDOW, (s.start + s.end) // 2) <= minute <= s.end + WINDOW
            if near_end and not s.minutes() & recorded:
                return Proposal(action=Action.CONFERMA, span=s)
    return Proposal(action=Action.INIZIO, span=None)


def _holidays(contract: Contract, year: int) -> frozenset[date]:
    return festivita(year, contract.patrono_mese, contract.patrono_giorno)


def _runs(minutes: set[int]) -> list[Span]:
    """Contiguous runs of minutes longer than TOLERANCE, as spans."""
    ordered = sorted(minutes)
    result: list[Span] = []
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        if j - i + 1 > TOLERANCE:
            result.append(Span(start=ordered[i], end=ordered[j] + 1))
        i = j + 1
    return result
