import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tata.models import EventKind, Payment
from tata.payslip import compute_payslip
from tata.store import RapportoStore, Store
from tests.helpers import rates_2026, span, standard_contract


def store(tmp_path: Path) -> RapportoStore:
    """A fresh installation with one rapporto, hired 12/10/2026."""
    root = Store(tmp_path / "tata.sqlite")
    return root.rapporto(root.add_rapporto(standard_contract(date(2026, 10, 12))))


def test_contract_round_trips(tmp_path: Path) -> None:
    s = store(tmp_path)
    later = standard_contract(date(2026, 11, 2))
    s.save_contract(later, {})
    assert Store(tmp_path / "tata.sqlite").rapporto(s.id).contract() == later


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
    later = standard_contract(date(2026, 10, 13))
    with pytest.raises(ValueError, match="finalizzato"):
        s.save_contract(later, {p.id: [span("15:45-16:00")]})
    assert s.contract() == worker  # nothing written
    assert s.events() == []


def test_employer_car_round_trip_and_finalized_month(tmp_path: Path) -> None:
    s = store(tmp_path)
    tue = date(2026, 10, 13)
    s.set_employer_car(tue, False, True)
    s.set_employer_car(tue, False, True)  # idempotent
    assert s.employer_car() == {(tue, False)}
    s.set_employer_car(tue, False, False)
    assert s.employer_car() == set()
    worker = standard_contract(date(2026, 10, 12))
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), None))
    with pytest.raises(ValueError, match="finalizzato"):
        s.set_employer_car(tue, True, True)


def test_rapporti_are_isolated_and_may_overlap(tmp_path: Path) -> None:
    root = Store(tmp_path / "tata.sqlite")
    colf = root.rapporto(root.add_rapporto(standard_contract(date(2026, 10, 12))))
    babysitter = root.rapporto(root.add_rapporto(standard_contract(date(2026, 10, 1))))
    tue = date(2026, 10, 13)
    colf.add_punch(tue, 480, None, [])
    babysitter.add_punch(tue, 480, None, [])  # an open punch each: one per rapporto
    babysitter.add_event(tue, EventKind.FERIE, None, "")
    babysitter.set_employer_car(tue, True, True)
    assert len(colf.punches()) == len(babysitter.punches()) == 1
    assert colf.events() == [] and colf.employer_car() == set()
    with pytest.raises(KeyError):
        colf.delete_punch(babysitter.punches()[0].id)
    with pytest.raises(ValueError, match="aperta"):
        colf.add_punch(tue, 900, None, [])
    assert [r.id for r in root.rapporti()] == [colf.id, babysitter.id]
    with pytest.raises(KeyError):
        root.rapporto(99)


def test_all_payments_sum_every_rapporto(tmp_path: Path) -> None:
    root = Store(tmp_path / "tata.sqlite")
    a = root.rapporto(root.add_rapporto(standard_contract(date(2026, 10, 12))))
    b = root.rapporto(root.add_rapporto(standard_contract(date(2026, 10, 12))))
    for r, datore in ((a, "100.00"), (b, "50.00")):
        r.record_payment(
            Payment(
                year=2026,
                quarter=4,
                paid_on=date(2027, 1, 9),
                inps_datore=Decimal(datore),
                inps_lavoratore=Decimal(10),
                cassa_colf=Decimal(5),
                totale=Decimal(datore) + 15,
            )
        )
    assert len(a.payments()) == len(b.payments()) == 1  # each its own quarter 4
    assert sorted(p.inps_datore for p in root.all_payments()) == [Decimal(50), Decimal(100)]


def test_worker_access_and_tracker(tmp_path: Path) -> None:
    root = Store(tmp_path / "tata.sqlite")
    cid = root.add_rapporto(standard_contract(date(2026, 10, 12)))
    root.set_ha_entity(cid, "device_tracker.necchi")
    root.set_worker_hash(cid, "scrypt$00$00")
    [r] = root.rapporti()
    assert (r.ha_entity, r.worker_access) == ("device_tracker.necchi", True)
    assert root.worker_hashes() == {cid: "scrypt$00$00"}
    root.set_worker_hash(cid, None)
    assert root.worker_hashes() == {} and not root.rapporti()[0].worker_access
    with pytest.raises(KeyError):
        root.set_ha_entity(99, "")


V0_SCHEMA = """
CREATE TABLE contract (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL);
CREATE TABLE event (id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,
    span_start INTEGER, span_end INTEGER, note TEXT NOT NULL);
CREATE TABLE payslip_final (year INTEGER NOT NULL, month INTEGER NOT NULL, data TEXT NOT NULL,
    finalized_at TEXT NOT NULL, PRIMARY KEY (year, month));
CREATE TABLE payment (year INTEGER NOT NULL, quarter INTEGER NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (year, quarter));
CREATE TABLE tfr_advance (id INTEGER PRIMARY KEY, day TEXT NOT NULL, amount TEXT NOT NULL);
CREATE TABLE punch (id INTEGER PRIMARY KEY, day TEXT NOT NULL, start INTEGER NOT NULL,
    stop INTEGER);
CREATE TABLE punch_event (event_id INTEGER PRIMARY KEY, punch_id INTEGER NOT NULL);
CREATE UNIQUE INDEX one_open_punch ON punch ((stop IS NULL)) WHERE stop IS NULL;
"""


def test_version_0_becomes_rapporto_1(tmp_path: Path) -> None:
    # A database from before rapporti (no employer_car table either): everything is kept.
    path = tmp_path / "tata.sqlite"
    worker = standard_contract(date(2026, 10, 12))
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(V0_SCHEMA)
        db.execute("INSERT INTO contract VALUES (1, ?)", (worker.model_dump_json(),))
        db.execute("INSERT INTO punch VALUES (7, '2026-10-15', 470, 590)")
        db.execute("INSERT INTO event VALUES (3, '2026-10-15', 'extra', 525, 590, 'timbratura')")
        db.execute("INSERT INTO punch_event VALUES (3, 7)")
        db.execute("INSERT INTO tfr_advance VALUES (1, '2026-12-20', '10.00')")
    root = Store(path)
    [r] = root.rapporti()
    assert (r.id, r.contract, r.ha_entity, r.worker_access) == (1, worker, "", False)
    rs = root.rapporto(1)
    assert [(p.id, p.start, p.end) for p in rs.punches()] == [(7, 470, 590)]
    assert [e.id for e in rs.events()] == [3] and rs.punch_links() == {3: 7}
    assert [a.amount for a in rs.advances()] == [Decimal("10.00")]
    assert rs.employer_car() == set()
    Store(path)  # reopening an upgraded database changes nothing
    assert root.rapporto(1).punches() == rs.punches()
