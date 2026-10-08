"""Turn the schedule plus the exceptions into classified worked minutes, day by day."""

import calendar
from collections import Counter
from datetime import date, timedelta

from tata.festivita import festivita
from tata.models import Bucket, Contract, Event, EventKind, Frozen
from tata.rates import Ccnl, Rates

NIGHT_START = 22 * 60  # art. 14 c.6: night work is 22:00-6:00
NIGHT_END = 6 * 60
SUNDAY = 6


class DayWork(Frozen):
    day: date
    buckets: dict[Bucket, int]  # worked minutes by pay bucket; only non-zero entries
    permesso: int  # paid-leave minutes (art. 19)
    ferie: bool
    malattia: bool
    festivita: bool


def month_work(
    contract: Contract, events: list[Event], year: int, month: int, rates: Rates
) -> list[DayWork]:
    """One DayWork per calendar day of the month. Raises ValueError on inconsistent events.

    Weekly limits are counted over whole Monday-Sunday weeks, so the weeks straddling the
    month boundary are computed in full and trimmed afterwards.
    """
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    start = first - timedelta(days=first.weekday())
    end = last + timedelta(days=SUNDAY - last.weekday())
    by_day: dict[date, list[Event]] = {}
    for ev in events:
        by_day.setdefault(ev.day, []).append(ev)
    holidays = festivita(start.year, contract.patrono_mese, contract.patrono_giorno) | festivita(
        end.year, contract.patrono_mese, contract.patrono_giorno
    )

    result: list[DayWork] = []
    week_minutes = 0
    day = start
    while day <= end:
        if day.weekday() == 0:
            week_minutes = 0
        work = _day_work(contract, day, by_day.get(day, []), day in holidays, week_minutes, rates)
        week_minutes += sum(work.buckets.values())
        if first <= day <= last:
            result.append(work)
        day += timedelta(days=1)
    return result


def _day_work(
    contract: Contract,
    day: date,
    events: list[Event],
    is_festivita: bool,
    week_minutes_before: int,
    rates: Rates,
) -> DayWork:
    if events and not contract.employed_on(day):
        raise ValueError(f"evento del {day} fuori dal rapporto di lavoro")
    whole = {ev.kind for ev in events if ev.span is None}
    if len([ev for ev in events if ev.span is None]) > 1:
        raise ValueError(f"più eventi di giornata intera il {day}")
    if EventKind.FERIE in whole and (day.weekday() == SUNDAY or is_festivita):
        raise ValueError(f"ferie il {day}: le ferie si contano da lunedì a sabato non festivi")

    scheduled: set[int] = set()
    if contract.employed_on(day) and not is_festivita:
        for s in contract.orario[day.weekday()]:
            scheduled |= s.minutes()

    permesso = 0
    if whole:
        if EventKind.PERMESSO in whole:
            permesso = len(scheduled)
        scheduled = set()
    for ev in events:
        if ev.span is not None and ev.kind in (EventKind.PERMESSO, EventKind.ASSENZA):
            removed = scheduled & ev.span.minutes()
            if ev.kind is EventKind.PERMESSO:
                permesso += len(removed)
            scheduled -= removed

    worked = set(scheduled)
    for ev in events:
        if ev.kind is EventKind.EXTRA and ev.span is not None:
            worked |= ev.span.minutes()

    festivo = is_festivita or day.weekday() == SUNDAY
    return DayWork(
        day=day,
        buckets=_classify(sorted(worked), festivo, week_minutes_before, rates.ccnl),
        permesso=permesso,
        ferie=EventKind.FERIE in whole,
        malattia=EventKind.MALATTIA in whole,
        festivita=is_festivita,
    )


def _classify(minutes: list[int], festivo: bool, week_before: int, ccnl: Ccnl) -> dict[Bucket, int]:
    """Each minute gets exactly one bucket; the latest minutes of the day/week are the overtime."""
    counts: Counter[Bucket] = Counter()
    for index, minute in enumerate(minutes):
        night = minute >= NIGHT_START or minute < NIGHT_END
        in_week = week_before + index
        if festivo:
            bucket = Bucket.FESTIVO
        elif index >= ccnl.max_minuti_giorno or in_week >= ccnl.max_minuti_settimana_banda:
            bucket = Bucket.STRAORDINARIO_NOTTURNO if night else Bucket.STRAORDINARIO_DIURNO
        elif in_week >= ccnl.max_minuti_settimana:
            bucket = Bucket.STRAORDINARIO_NOTTURNO if night else Bucket.OLTRE_40
        else:
            bucket = Bucket.NOTTURNO if night else Bucket.ORDINARIO
        counts[bucket] += 1
    return dict(counts)
