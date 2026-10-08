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
    s.save_contract(worker)
    assert Store(tmp_path / "tata.sqlite").contract() == worker


def test_events_round_trip_and_delete(tmp_path: Path) -> None:
    s = store(tmp_path)
    extra = s.add_event(date(2026, 10, 9), EventKind.EXTRA, span("18:00-24:00"), "cena")
    ferie = s.add_event(date(2026, 10, 10), EventKind.FERIE, None, "")
    assert s.events() == [extra, ferie]
    s.delete_event(extra.id)
    assert s.events() == [ferie]


def test_deleting_a_missing_event_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        store(tmp_path).delete_event(42)


def test_finalized_month_is_immutable(tmp_path: Path) -> None:
    s = store(tmp_path)
    worker = standard_contract(date(2026, 10, 12))
    s.save_contract(worker)
    ev = s.add_event(date(2026, 10, 13), EventKind.ASSENZA, None, "")
    slip = compute_payslip(worker, s.events(), 2026, 10, rates_2026())
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
