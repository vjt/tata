from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tata.models import EventKind, Payment
from tata.payslip import compute_payslip
from tata.store import Store
from tests.helpers import rates_2026, span, standard_contract


def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "tata.sqlite")


def test_contract_round_trips(tmp_path: Path) -> None:
    s = store(tmp_path)
    assert s.contract() is None
    worker = standard_contract(date(2026, 10, 12))
    s.save_contract(worker, {})
    assert Store(tmp_path / "tata.sqlite").contract() == worker


def test_events_round_trip_and_delete(tmp_path: Path) -> None:
    s = store(tmp_path)
    extra = s.add_event(date(2026, 10, 9), EventKind.EXTRA, span("18:00-24:00"), "cena")
    ferie = s.add_event(date(2026, 10, 10), EventKind.FERIE, None, "")
    assert s.events() == [extra, ferie]
    assert s.delete_event(extra.id) == date(2026, 10, 9)
    assert s.events() == [ferie]


def test_deleting_a_missing_event_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        store(tmp_path).delete_event(42)


def test_finalized_month_is_immutable(tmp_path: Path) -> None:
    s = store(tmp_path)
    worker = standard_contract(date(2026, 10, 12))
    s.save_contract(worker, {})
    ev = s.add_event(date(2026, 10, 13), EventKind.ASSENZA, None, "")
    slip = compute_payslip(worker, s.events(), 2026, 10, rates_2026(), None)
    s.finalize(slip)
    assert s.final_payslip(2026, 10) == slip
    assert s.final_payslip(2026, 11) is None
    with pytest.raises(ValueError, match="finalizzato"):
        s.add_event(date(2026, 10, 14), EventKind.ASSENZA, None, "")
    with pytest.raises(ValueError, match="finalizzato"):
        s.delete_event(ev.id)
    with pytest.raises(ValueError, match="finalizzato"):
        s.finalize(slip)


def test_payments_replace_per_quarter(tmp_path: Path) -> None:
    s = store(tmp_path)
    first = Payment(
        year=2026,
        quarter=4,
        paid_on=date(2027, 1, 8),
        inps_datore=Decimal("229.86"),
        inps_lavoratore=Decimal("77.84"),
        cassa_colf=Decimal("10.86"),
        totale=Decimal("318.56"),
    )
    s.record_payment(first)
    fixed = first.model_copy(update={"paid_on": date(2027, 1, 9)})
    s.record_payment(fixed)
    assert s.payments() == [fixed]


def test_tfr_advances(tmp_path: Path) -> None:
    s = store(tmp_path)
    a = s.add_advance(date(2027, 5, 2), Decimal("100.00"))
    assert s.advances() == [a]


def test_punch_close_stores_its_extras_and_delete_removes_them(tmp_path: Path) -> None:
    s = store(tmp_path)
    day = date(2026, 10, 13)
    opened = s.add_punch(day, 945, None, [])  # 15:45
    assert s.punches() == [opened]
    closed = s.close_punch(opened.id, 1110, [span("18:00-18:30")])  # 18:30
    assert s.punches() == [closed]
    assert closed.end == 1110
    [extra] = s.events()
    assert (extra.kind, extra.span, extra.note) == (
        EventKind.EXTRA,
        span("18:00-18:30"),
        "timbratura",
    )
    assert s.delete_punch(closed.id) == day
    assert s.punches() == []
    assert s.events() == []


def test_deleting_a_generated_event_unlinks_it(tmp_path: Path) -> None:
    s = store(tmp_path)
    p = s.add_punch(date(2026, 10, 13), 945, 1110, [span("18:00-18:30")])
    [extra] = s.events()
    s.delete_event(extra.id)
    s.delete_punch(p.id)  # must not fail on the dangling link
    assert s.events() == []


def test_punch_in_a_finalized_month_is_refused(tmp_path: Path) -> None:
    s = store(tmp_path)
    worker = standard_contract(date(2026, 10, 12))
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), None))
    with pytest.raises(ValueError, match="finalizzato"):
        s.add_punch(date(2026, 10, 13), 480, None, [])


def test_missing_punch_is_a_key_error(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        store(tmp_path).delete_punch(42)


def test_close_and_delete_punch_in_a_finalized_month_are_refused(tmp_path: Path) -> None:
    s = store(tmp_path)
    worker = standard_contract(date(2026, 10, 12))
    opened = s.add_punch(date(2026, 10, 13), 945, None, [])
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), None))
    with pytest.raises(ValueError, match="finalizzato"):
        s.close_punch(opened.id, 1080, [])
    with pytest.raises(ValueError, match="finalizzato"):
        s.delete_punch(opened.id)
    assert s.punches() == [opened]


def test_a_second_open_punch_is_refused_by_the_store(tmp_path: Path) -> None:
    s = store(tmp_path)
    first = s.add_punch(date(2026, 10, 13), 480, None, [])
    with pytest.raises(ValueError, match="già una timbratura aperta"):
        s.add_punch(date(2026, 10, 14), 480, None, [])
    s.add_punch(date(2026, 10, 14), 480, 525, [])  # closed punches are not limited
    assert [p.id for p in s.punches() if p.end is None] == [first.id]


def test_saving_the_contract_replaces_the_punch_extras(tmp_path: Path) -> None:
    s = store(tmp_path)
    p = s.add_punch(date(2026, 10, 13), 945, 1110, [span("18:00-18:30")])
    manual = s.add_event(date(2026, 10, 14), EventKind.EXTRA, span("18:00-19:00"), "cena")
    worker = standard_contract(date(2026, 10, 12))
    s.save_contract(worker, {p.id: [span("15:45-16:00"), span("18:00-18:30")]})
    assert s.contract() == worker
    generated = [e for e in s.events() if e.id != manual.id]
    assert [e.span for e in generated] == [span("15:45-16:00"), span("18:00-18:30")]
    assert manual in s.events()  # hand-entered events are not touched
    s.delete_punch(p.id)  # the new extras are linked to the punch
    assert s.events() == [manual]


def test_saving_the_contract_refuses_extras_of_a_finalized_month(tmp_path: Path) -> None:
    s = store(tmp_path)
    worker = standard_contract(date(2026, 10, 12))
    p = s.add_punch(date(2026, 10, 13), 945, 1080, [])
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), None))
    with pytest.raises(ValueError, match="finalizzato"):
        s.save_contract(worker, {p.id: [span("15:45-16:00")]})
    assert s.contract() is None
    assert s.events() == []
