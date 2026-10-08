from datetime import date

import pytest

from tata.hours import DayWork, month_work, range_days
from tata.models import Bucket, EventKind, Span
from tests.helpers import STANDARD, contract, event, rates_2026, span, standard_contract

HIRED = date(2026, 1, 1)


def day_of(days: list[DayWork], day: date) -> DayWork:
    return next(d for d in days if d.day == day)


def test_standard_monday_is_three_ordinary_hours() -> None:
    days = month_work(standard_contract(HIRED), [], 2026, 10, rates_2026())
    monday = day_of(days, date(2026, 10, 5))
    assert monday.buckets == {Bucket.ORDINARIO: 180}


def test_october_2026_has_one_entry_per_calendar_day() -> None:
    days = month_work(standard_contract(HIRED), [], 2026, 10, rates_2026())
    assert [d.day.day for d in days] == list(range(1, 32))
    # 22 weekdays x 180 minutes
    assert sum(d.buckets.get(Bucket.ORDINARIO, 0) for d in days) == 22 * 180


def test_friday_extra_until_midnight_splits_notturno_and_straordinario() -> None:
    friday = date(2026, 10, 9)
    extra = event(friday, EventKind.EXTRA, "18:00-24:00")
    days = month_work(standard_contract(HIRED), [extra], 2026, 10, rates_2026())
    # 3 h schedule + 6 h extra = 9 h > 8 h/day (art. 14): the latest hour is straordinario.
    # 22-23 is ordinary night work (+20%, art. 14 c.6), 23-24 straordinario notturno (+50%).
    assert day_of(days, friday).buckets == {
        Bucket.ORDINARIO: 180 + 240,
        Bucket.NOTTURNO: 60,
        Bucket.STRAORDINARIO_NOTTURNO: 60,
    }


def test_festivita_on_a_weekday_is_rest_not_work() -> None:
    days = month_work(standard_contract(HIRED), [], 2026, 6, rates_2026())
    patrono = day_of(days, date(2026, 6, 24))  # Wednesday
    assert patrono.festivita
    assert patrono.buckets == {}


def test_work_on_sunday_is_festivo() -> None:
    sunday = date(2026, 10, 11)
    extra = event(sunday, EventKind.EXTRA, "10:00-12:00")
    days = month_work(standard_contract(HIRED), [extra], 2026, 10, rates_2026())
    assert day_of(days, sunday).buckets == {Bucket.FESTIVO: 120}


def test_permesso_span_removes_scheduled_minutes_and_counts_them_as_paid_leave() -> None:
    tuesday = date(2026, 10, 6)
    leave = event(tuesday, EventKind.PERMESSO, "15:45-17:00")
    work = day_of(month_work(standard_contract(HIRED), [leave], 2026, 10, rates_2026()), tuesday)
    assert work.buckets == {Bucket.ORDINARIO: 45 + 60}
    assert work.permesso == 75


def test_whole_day_permesso_pays_the_scheduled_minutes() -> None:
    tuesday = date(2026, 10, 6)
    leave = event(tuesday, EventKind.PERMESSO, None)
    work = day_of(month_work(standard_contract(HIRED), [leave], 2026, 10, rates_2026()), tuesday)
    assert work.buckets == {}
    assert work.permesso == 180


def test_unpaid_absence_removes_minutes() -> None:
    tuesday = date(2026, 10, 6)
    gone = event(tuesday, EventKind.ASSENZA, None)
    work = day_of(month_work(standard_contract(HIRED), [gone], 2026, 10, rates_2026()), tuesday)
    assert work.buckets == {}
    assert work.permesso == 0


def test_ferie_and_malattia_clear_the_schedule() -> None:
    mon, tue = date(2026, 10, 5), date(2026, 10, 6)
    events = [event(mon, EventKind.FERIE, None), event(tue, EventKind.MALATTIA, None)]
    days = month_work(standard_contract(HIRED), events, 2026, 10, rates_2026())
    assert day_of(days, mon).ferie
    assert day_of(days, mon).buckets == {}
    assert day_of(days, tue).malattia
    assert day_of(days, tue).buckets == {}


def test_ferie_on_sunday_is_rejected() -> None:
    with pytest.raises(ValueError, match="ferie"):
        month_work(
            standard_contract(HIRED),
            [event(date(2026, 10, 11), EventKind.FERIE, None)],
            2026,
            10,
            rates_2026(),
        )


def test_event_outside_employment_is_rejected() -> None:
    with pytest.raises(ValueError, match="rapporto"):
        month_work(
            standard_contract(date(2026, 10, 12)),
            [event(date(2026, 10, 5), EventKind.EXTRA, "10:00-11:00")],
            2026,
            10,
            rates_2026(),
        )


def test_no_schedule_before_hire_date() -> None:
    days = month_work(standard_contract(date(2026, 10, 12)), [], 2026, 10, rates_2026())
    assert day_of(days, date(2026, 10, 9)).buckets == {}
    assert day_of(days, date(2026, 10, 12)).buckets == {Bucket.ORDINARIO: 180}


def test_weekly_40_to_44_band_then_straordinario() -> None:
    full_time: tuple[tuple[Span, ...], ...] = tuple(
        (span("08:00-12:00"), span("13:00-17:00")) if d < 5 else () for d in range(7)
    )
    worker = contract(assunzione=HIRED, paga="8.86", cessazione=None, orario=full_time, tab_h=False)
    saturday = date(2026, 10, 10)
    extra = event(saturday, EventKind.EXTRA, "20:00-23:00")
    days = month_work(worker, [extra], 2026, 10, rates_2026())
    # 40 h done Mon-Fri. 20-22 diurne within 40-44 h: +10% (art. 15 c.4).
    # 22-23 is night, outside the 6-22 band: straordinario notturno.
    assert day_of(days, saturday).buckets == {
        Bucket.OLTRE_40: 120,
        Bucket.STRAORDINARIO_NOTTURNO: 60,
    }


def test_week_spanning_month_start_counts_previous_days() -> None:
    # Same full-time contract; Thursday 1 Oct 2026 starts mid-week, the 40 h count is per week.
    full_time: tuple[tuple[Span, ...], ...] = tuple(
        (span("08:00-12:00"), span("13:00-17:00")) if d < 5 else () for d in range(7)
    )
    worker = contract(assunzione=HIRED, paga="8.86", cessazione=None, orario=full_time, tab_h=False)
    saturday = date(2026, 10, 3)
    extra = event(saturday, EventKind.EXTRA, "09:00-10:00")
    days = month_work(worker, [extra], 2026, 10, rates_2026())
    assert day_of(days, saturday).buckets == {Bucket.OLTRE_40: 60}


def test_standard_schedule_fixture_is_fifteen_hours() -> None:
    assert sum(s.end - s.start for d in STANDARD for s in d) == 15 * 60


def test_ferie_range_skips_sundays_and_festivita() -> None:
    worker = standard_contract(HIRED)
    # Thu 31 Dec 2026 - Thu 7 Jan 2027: 1 and 6 Jan are festività, 3 Jan is Sunday
    days = range_days(worker, EventKind.FERIE, date(2026, 12, 31), date(2027, 1, 7))
    assert days == [
        date(2026, 12, 31),
        date(2027, 1, 2),
        date(2027, 1, 4),
        date(2027, 1, 5),
        date(2027, 1, 7),
    ]


def test_malattia_range_is_every_calendar_day() -> None:
    worker = standard_contract(HIRED)
    days = range_days(worker, EventKind.MALATTIA, date(2026, 10, 9), date(2026, 10, 12))
    assert len(days) == 4


def test_whole_day_absence_range_is_scheduled_days_only() -> None:
    worker = standard_contract(HIRED)
    days = range_days(worker, EventKind.ASSENZA, date(2026, 10, 9), date(2026, 10, 12))
    assert days == [date(2026, 10, 9), date(2026, 10, 12)]


def test_extra_hours_cannot_span_days() -> None:
    with pytest.raises(ValueError, match="un giorno"):
        range_days(standard_contract(HIRED), EventKind.EXTRA, date(2026, 10, 9), date(2026, 10, 10))


def test_range_outside_employment_is_rejected() -> None:
    with pytest.raises(ValueError, match="rapporto"):
        range_days(standard_contract(HIRED), EventKind.FERIE, date(2025, 12, 30), date(2026, 1, 2))
