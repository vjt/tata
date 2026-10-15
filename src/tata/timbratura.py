"""Cartellino: what the punches imply for payroll, and what the button proposes."""

from datetime import date, datetime, time, timedelta
from enum import StrEnum
from itertools import pairwise

from tata.festivita import festivita
from tata.hours import scheduled_minutes
from tata.models import (
    MINUTES_PER_DAY,
    NOON,
    Contract,
    Event,
    EventKind,
    Frozen,
    Punch,
    Span,
)

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


class Turno(Frozen):
    """A half-day with a closed punch: one trip to school and back, one rimborso."""

    day: date
    morning: bool
    punch_id: int  # the punch that opened it
    own_car: bool  # hers by default; with the employer's car there is no rimborso


def turni(
    punches: list[Punch], employer_car: set[tuple[date, bool]], year: int, month: int
) -> list[Turno]:
    """The month's turni: at most a morning and an afternoon a day, however many punches.

    `employer_car`: the (day, morning) turni done with the employer's car.
    """
    result: dict[tuple[date, bool], Turno] = {}
    for p in sorted(punches, key=lambda p: (p.day, p.start)):
        if p.end is not None and (p.day.year, p.day.month) == (year, month):
            key = (p.day, p.start < NOON)
            own = key not in employer_car
            result.setdefault(key, Turno(day=p.day, morning=key[1], punch_id=p.id, own_car=own))
    return list(result.values())


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
    """Ended scheduled time no punch and no event explains, from the hire date to now.

    Derived, never stored: an ASSENZA, PERMESSO or MORA over the gap, or any whole-day event,
    is what clears it. An open punch covers the rest of its day.
    """
    today, minute = now.date(), now.hour * 60 + now.minute
    day_events: dict[date, list[Event]] = {}
    for ev in events:
        day_events.setdefault(ev.day, []).append(ev)
    day_punches: dict[date, list[Punch]] = {}
    for p in punches:
        day_punches.setdefault(p.day, []).append(p)
    result: list[Shortfall] = []
    day = contract.assunzione
    while day <= today and contract.employed_on(day):
        ended: set[int] = set()
        for s in contract.orario[day.weekday()]:
            if day < today or s.end <= minute:
                ended |= s.minutes()
        holiday = day in _holidays(contract, day.year)
        missing = scheduled_minutes(contract, day, holiday) & ended
        for ev in day_events.get(day, []):
            if ev.span is None:
                missing = set[int]()
            elif ev.kind in COVERING:
                missing -= ev.span.minutes()
        for p in day_punches.get(day, []):
            missing -= set(range(p.start, MINUTES_PER_DAY if p.end is None else p.end))
        result += [Shortfall(day=day, span=s) for s in _runs(missing)]
        day += timedelta(days=1)
    return result


def unpunched(
    contract: Contract, events: list[Event], punches: list[Punch], year: int, month: int
) -> list[Event]:
    """Only punched time is paid: the scheduled time from the hire to the end of the month no
    punch and no event covers (gaps still to confirm, days still to come), as unstored
    ASSENZA. No punch, no pay. The earlier months are there for the 13ma, which sums them.

    Confirming a gap as MORA or PERMESSO is what pays it.
    """
    next_month = date(year + month // 12, month % 12 + 1, 1)
    return [
        Event(id=0, day=s.day, kind=EventKind.ASSENZA, span=s.span, note="non timbrato")
        for s in shortfalls(contract, events, punches, datetime.combine(next_month, time(0)))
    ]


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
