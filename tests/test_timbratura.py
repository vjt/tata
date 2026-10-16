from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tata.models import EventKind, Punch
from tata.timbratura import (
    Action,
    Proposal,
    Shortfall,
    check_punches,
    overruns,
    propose,
    shortfalls,
    turni,
    unpunched,
)
from tests.helpers import event, span, standard_contract

TUE = date(2026, 10, 13)
WORKER = standard_contract(TUE)  # hired Tuesday 13/10
AFTERNOON = span("15:45-18:00")


def at(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Europe/Rome"))


def punch(day: date, when: str) -> Punch:
    s = span(when)
    return Punch(id=0, day=day, start=s.start, end=s.end)


def test_overrun_after_the_afternoon_span_is_extra() -> None:
    # Scheduled 15:45-18:00, worked 15:40-18:30: 15:40-15:45 is 5 min (= tolerance, dropped),
    # 18:00-18:30 is 30 min of EXTRA.
    assert overruns(WORKER, punch(TUE, "15:40-18:30")) == [span("18:00-18:30")]


def test_punch_on_a_sunday_is_all_extra() -> None:
    assert overruns(WORKER, punch(date(2026, 10, 18), "10:00-12:00")) == [span("10:00-12:00")]


def test_punch_on_a_festivita_is_all_extra() -> None:
    # 8/12 Immacolata, a Tuesday: nothing scheduled, every punched minute is extra.
    assert overruns(WORKER, punch(date(2026, 12, 8), "15:45-18:00")) == [span("15:45-18:00")]


def test_a_punch_over_a_scheduled_span_replaces_it() -> None:
    # Morning 08:00-08:45 punched 08:10-08:45 (vjt 16/10: the punch, often a confirmed wifi
    # proposal, is that turno): the 10 min before it are not worked, unpaid, not to confirm.
    punches = [punch(TUE, "08:10-08:45"), punch(TUE, "15:45-17:30")]
    assert shortfalls(WORKER, [], punches, at(2026, 10, 13, 20, 0)) == []
    # A span no punch touches still is one.
    later = at(2026, 10, 14, 20, 0)
    wed = date(2026, 10, 14)
    assert shortfalls(WORKER, [], [*punches, punch(wed, "15:45-18:00")], later) == [
        Shortfall(day=wed, span=span("08:00-08:45"))
    ]


def test_late_arrival_within_tolerance_is_nothing() -> None:
    punches = [punch(TUE, "08:05-08:45"), punch(TUE, "15:45-17:56")]
    assert shortfalls(WORKER, [], punches, at(2026, 10, 13, 20, 0)) == []
    assert overruns(WORKER, punches[0]) == []


def test_unpunched_span_counts_only_once_ended() -> None:
    morning = [punch(TUE, "08:00-08:45")]
    # 17:00: the afternoon span is still running, nothing pending yet.
    assert shortfalls(WORKER, [], morning, at(2026, 10, 13, 17, 0)) == []
    # 18:00: it has ended unpunched.
    assert shortfalls(WORKER, [], morning, at(2026, 10, 13, 18, 0)) == [
        Shortfall(day=TUE, span=span("15:45-18:00"))
    ]


def test_events_clear_shortfalls() -> None:
    morning = [punch(TUE, "08:00-08:45")]
    now = at(2026, 10, 13, 20, 0)
    for kind in (EventKind.MORA, EventKind.ASSENZA, EventKind.PERMESSO):
        assert shortfalls(WORKER, [event(TUE, kind, "15:45-18:00")], morning, now) == []
    # Wednesday on ferie, nothing punched that day: no shortfall.
    wed = date(2026, 10, 14)
    later = at(2026, 10, 14, 20, 0)
    assert shortfalls(WORKER, [event(wed, EventKind.FERIE, None)], morning, later) == [
        Shortfall(day=TUE, span=span("15:45-18:00"))  # Tuesday afternoon still pending
    ]


def test_every_unpunched_day_since_hire_is_pending() -> None:
    # Nothing punched at all: Tue 13 and Wed 14/10, both spans, are pending; Mon 12, before
    # the hire, is not.
    wed = date(2026, 10, 14)
    assert shortfalls(WORKER, [], [], at(2026, 10, 14, 20, 0)) == [
        Shortfall(day=TUE, span=span("08:00-08:45")),
        Shortfall(day=TUE, span=AFTERNOON),
        Shortfall(day=wed, span=span("08:00-08:45")),
        Shortfall(day=wed, span=AFTERNOON),
    ]


def test_open_punch_covers_the_rest_of_the_day() -> None:
    p = Punch(id=0, day=TUE, start=span("15:45-18:00").start, end=None)
    morning = punch(TUE, "08:00-08:45")
    assert shortfalls(WORKER, [], [morning, p], at(2026, 10, 13, 18, 30)) == []


def test_check_punches_refuses_overlaps_and_two_open() -> None:
    with pytest.raises(ValueError, match="sovrapposte"):
        check_punches([punch(TUE, "08:00-09:00"), punch(TUE, "08:30-10:00")])
    with pytest.raises(ValueError, match="aperta"):
        check_punches(
            [Punch(id=1, day=TUE, start=480, end=None), Punch(id=2, day=TUE, start=960, end=None)]
        )
    check_punches([punch(TUE, "08:00-08:45"), punch(TUE, "08:45-09:00")])  # touching is fine


def tue_at(hh: int, mm: int) -> datetime:
    return at(2026, 10, 13, hh, mm)


def test_open_punch_always_proposes_fine() -> None:
    yesterday = Punch(id=1, day=date(2026, 10, 12), start=945, end=None)
    assert propose(WORKER, [yesterday], tue_at(3, 0)) == Proposal(action=Action.FINE, span=None)


@pytest.mark.parametrize(
    ("hh", "mm", "expected"),
    [
        (7, 50, Proposal(action=Action.INIZIO, span=None)),  # near the morning start
        (8, 21, Proposal(action=Action.INIZIO, span=None)),  # before the 45-min span's midpoint
        (8, 22, Proposal(action=Action.CONFERMA, span=span("08:00-08:45"))),  # (480+525)//2=502
        (9, 15, Proposal(action=Action.CONFERMA, span=span("08:00-08:45"))),  # end + 30, inside
        (9, 16, Proposal(action=Action.INIZIO, span=None)),  # end + 31, outside
        (17, 30, Proposal(action=Action.CONFERMA, span=AFTERNOON)),  # end - 30, inside
        (17, 29, Proposal(action=Action.INIZIO, span=None)),  # end - 31
        (12, 0, Proposal(action=Action.INIZIO, span=None)),  # outside the schedule
    ],
)
def test_proposal_by_time_of_day(hh: int, mm: int, expected: Proposal) -> None:
    assert propose(WORKER, [], tue_at(hh, mm)) == expected


def test_recorded_span_is_not_proposed_again() -> None:
    done = punch(TUE, "15:50-18:00")
    assert propose(WORKER, [done], tue_at(18, 5)) == Proposal(action=Action.INIZIO, span=None)


def test_festivita_proposes_inizio() -> None:
    immacolata = at(2026, 12, 8, 17, 50)
    assert propose(WORKER, [], immacolata) == Proposal(action=Action.INIZIO, span=None)


def test_festivita_is_never_a_shortfall() -> None:
    # Mon 7/12 punched in full; Tue 8/12 Immacolata has nothing scheduled (festività), so at
    # 20:00 of 8/12 nothing is pending even though nothing was punched that day.
    mon = date(2026, 12, 7)
    punches = [punch(mon, "08:00-08:45"), punch(mon, "15:45-18:00")]
    hired_monday = standard_contract(mon)
    assert shortfalls(hired_monday, [], punches, at(2026, 12, 8, 20, 0)) == []


def test_unpunched_time_of_the_month_is_unpaid_assenza() -> None:
    # Hired Tue 13/10, punched that afternoon only: the 08:00-08:45 gap still to confirm,
    # then both spans of every working day 14-30/10 (14, 15, 16, 19-23, 26-30 = 13 days):
    # 1 + 13 x 2 = 27 spans.
    gaps = unpunched(WORKER, [], [punch(TUE, "15:45-18:00")], 2026, 10)
    assert all(e.kind is EventKind.ASSENZA for e in gaps)
    assert len(gaps) == 27
    assert [(e.day, e.span) for e in gaps[:2]] == [
        (TUE, span("08:00-08:45")),
        (date(2026, 10, 14), span("08:00-08:45")),
    ]
    assert gaps[-1].day == date(2026, 10, 30)


def test_a_confirmed_mora_is_not_unpunched() -> None:
    mora = event(TUE, EventKind.MORA, "08:00-08:45")
    gaps = unpunched(WORKER, [mora], [punch(TUE, "15:45-18:00")], 2026, 10)
    assert (TUE, span("08:00-08:45")) not in [(e.day, e.span) for e in gaps]


def test_no_punch_no_pay() -> None:
    # Nothing punched: every scheduled span since the hire is unpaid, 14 working days
    # 13-30/10 x 2 = 28; nothing before the hire.
    assert len(unpunched(WORKER, [], [], 2026, 10)) == 28
    assert unpunched(WORKER, [], [], 2026, 9) == []


def test_a_turno_is_a_half_day_with_a_closed_punch() -> None:
    wed = date(2026, 10, 14)
    punches = [
        Punch(id=1, day=TUE, start=480, end=525),  # morning
        Punch(id=2, day=TUE, start=945, end=990),  # afternoon...
        Punch(id=3, day=TUE, start=1000, end=1080),  # ...the same afternoon: no second trip
        Punch(id=4, day=wed, start=480, end=None),  # open: not yet a trip
        Punch(id=5, day=date(2026, 11, 2), start=480, end=525),  # another month
    ]
    assert [
        (t.day, t.morning, t.punch_id, t.own_car) for t in turni(punches, set(), {}, 2026, 10)
    ] == [
        (TUE, True, 1, True),
        (TUE, False, 2, True),
    ]
    # The afternoon done with the employer's car: still a turno, no longer hers.
    by_car = turni(punches, {(TUE, False)}, {}, 2026, 10)
    assert [t.own_car for t in by_car] == [True, False]
    # Its km corrected: carried by the turno, None (the configured km) for the others.
    by_km = turni(punches, set(), {(TUE, False): Decimal(30)}, 2026, 10)
    assert [t.km for t in by_km] == [None, Decimal(30)]


def test_the_rest_of_a_replaced_span_is_still_unpaid() -> None:
    # 08:10-08:45 over 08:00-08:45: nothing to confirm, but 08:00-08:10 was not worked.
    punches = [punch(TUE, "08:10-08:45"), punch(TUE, "15:45-18:00")]
    gaps = [(e.day, e.span) for e in unpunched(WORKER, [], punches, 2026, 10) if e.day == TUE]
    assert gaps == [(TUE, span("08:00-08:10"))]
