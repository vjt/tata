import json
import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tata.models import EventKind, Gestione, Mileage, Payment
from tata.payslip import compute_payslip, rimborso_line
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
    slip = compute_payslip(worker, s.events(), 2026, 10, rates_2026(), [])
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
    closed = s.update_punch(opened.id, day, 945, 1110, [span("18:00-18:30")])  # 18:30
    assert s.punches() == [closed]
    assert closed.end == 1110
    [extra] = s.events()
    assert (extra.kind, extra.span, extra.note) == (
        EventKind.EXTRA,
        span("18:00-18:30"),
        "timbratura",
    )
    # Corrected to 16:00-18:15 the next day: the old EXTRA goes, the new one replaces it.
    wed = date(2026, 10, 14)
    fixed = s.update_punch(closed.id, wed, 960, 1095, [span("18:00-18:15")])
    assert s.punches() == [fixed] and (fixed.day, fixed.start, fixed.end) == (wed, 960, 1095)
    assert [(e.day, e.span) for e in s.events()] == [(wed, span("18:00-18:15"))]
    assert s.delete_punch(closed.id) == wed
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
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), []))
    with pytest.raises(ValueError, match="finalizzato"):
        s.add_punch(date(2026, 10, 13), 480, None, [])


def test_missing_punch_is_a_key_error(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        store(tmp_path).delete_punch(42)


def test_close_and_delete_punch_in_a_finalized_month_are_refused(tmp_path: Path) -> None:
    s = store(tmp_path)
    worker = standard_contract(date(2026, 10, 12))
    opened = s.add_punch(date(2026, 10, 13), 945, None, [])
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), []))
    with pytest.raises(ValueError, match="finalizzato"):
        s.update_punch(opened.id, opened.day, 945, 1080, [])
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
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), []))
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
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), []))
    with pytest.raises(ValueError, match="finalizzato"):
        s.set_employer_car(tue, True, True)


def test_turno_km_round_trip_and_finalized_month(tmp_path: Path) -> None:
    s = store(tmp_path)
    tue = date(2026, 10, 13)
    s.set_turno_km(tue, True, Decimal("30.5"))
    s.set_turno_km(tue, True, Decimal(31))  # replaced
    s.set_turno_km(tue, False, Decimal(0))  # no trip at all is a value too
    assert s.turno_km() == {(tue, True): Decimal(31), (tue, False): Decimal(0)}
    s.set_turno_km(tue, False, None)  # back to the configured km
    assert s.turno_km() == {(tue, True): Decimal(31)}
    with pytest.raises(ValueError, match="km"):
        s.set_turno_km(tue, True, Decimal(-1))
    worker = standard_contract(date(2026, 10, 12))
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), []))
    with pytest.raises(ValueError, match="finalizzato"):
        s.set_turno_km(tue, True, None)


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
    root.set_worker_login(cid, ("necchi", "scrypt$00$00"))
    [r] = root.rapporti()
    assert (r.ha_entity, r.worker_user) == ("device_tracker.necchi", "necchi")
    assert root.worker_hashes() == {cid: "scrypt$00$00"}
    assert root.worker_login("necchi") == (cid, "scrypt$00$00")
    other = root.add_rapporto(standard_contract(date(2026, 10, 12)))
    with pytest.raises(ValueError, match="già in uso"):
        root.set_worker_login(other, ("necchi", "scrypt$11$11"))
    root.set_worker_login(cid, None)
    assert root.worker_hashes() == {} and root.rapporti()[0].worker_user is None
    assert root.worker_login("necchi") is None
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
    assert (r.id, r.contract, r.ha_entity, r.worker_user) == (1, worker, "", None)
    rs = root.rapporto(1)
    assert [(p.id, p.start, p.end) for p in rs.punches()] == [(7, 470, 590)]
    assert [e.id for e in rs.events()] == [3] and rs.punch_links() == {3: 7}
    assert [a.amount for a in rs.advances()] == [Decimal("10.00")]
    assert rs.employer_car() == set()
    Store(path)  # reopening an upgraded database changes nothing
    assert root.rapporto(1).punches() == rs.punches()


def test_version_4_contracts_withhold_and_finals_get_rimborsi(tmp_path: Path) -> None:
    # Version 4 had no `gestione` (her share was always withheld) and one optional rimborso
    # line per payslip, plus a lordo_da_pagare: the upgrade rewrites the stored JSON.
    path = tmp_path / "tata.sqlite"
    worker = standard_contract(date(2026, 10, 12))
    s = Store(path).rapporto(Store(path).add_rapporto(worker))
    trips = rimborso_line(Mileage(km_turno=Decimal(12), euro_km=Decimal("0.4463")), [None])
    assert trips is not None
    with_trip = compute_payslip(worker, [], 2026, 10, rates_2026(), [trips])
    s.finalize(with_trip)
    s.finalize(compute_payslip(worker, [], 2026, 11, rates_2026(), []))
    with closing(sqlite3.connect(path)) as db, db:
        for (data,) in db.execute("SELECT data FROM contract").fetchall():
            old = json.loads(data)
            del old["gestione"]
            db.execute("UPDATE contract SET data = ?", (json.dumps(old),))
        for month, data in db.execute("SELECT month, data FROM payslip_final").fetchall():
            old = json.loads(data)
            del old["contract"]["gestione"]
            rimborsi = old.pop("rimborsi")
            old["rimborso"] = rimborsi[0] if rimborsi else None
            old["lordo_da_pagare"] = "1.00"
            db.execute(
                "UPDATE payslip_final SET data = ? WHERE month = ?", (json.dumps(old), month)
            )
        db.execute("DROP TABLE spesa")
        db.execute("DROP TABLE wage_payment")
        db.execute("PRAGMA user_version = 4")
    rs = Store(path).rapporto(1)
    assert rs.contract() == worker and worker.gestione is Gestione.NETTO
    assert rs.final_payslip(2026, 10) == with_trip
    final_nov = rs.final_payslip(2026, 11)
    assert final_nov is not None and final_nov.rimborsi == ()
    assert rs.spese() == [] and rs.wage_payments() == []


def test_version_5_trattiene_becomes_gestione(tmp_path: Path) -> None:
    path = tmp_path / "tata.sqlite"
    worker = standard_contract(date(2026, 10, 12)).model_copy(update={"gestione": Gestione.LORDO})
    s = Store(path).rapporto(Store(path).add_rapporto(worker))
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), []))
    with closing(sqlite3.connect(path)) as db, db:
        for table, key in (("contract", None), ("payslip_final", "contract")):
            for (data,) in db.execute(f"SELECT data FROM {table}").fetchall():
                old = json.loads(data)
                c = old[key] if key else old
                del c["gestione"]
                c["trattiene"] = False  # version 5: the lordo, as a boolean
                db.execute(f"UPDATE {table} SET data = ?", (json.dumps(old),))
        db.execute("PRAGMA user_version = 5")
    rs = Store(path).rapporto(1)
    assert rs.contract() == worker
    final = rs.final_payslip(2026, 10)
    assert final is not None and final.contract == worker


def test_spese_and_wage_payments(tmp_path: Path) -> None:
    s = store(tmp_path)
    tue = date(2026, 10, 13)
    merenda = s.add_spesa(tue, Decimal("7.50"), "merenda")
    assert s.spese() == [merenda] and merenda.importo == Decimal("7.50")
    with pytest.raises(ValueError, match="positivo"):
        s.add_spesa(tue, Decimal(0), "niente")
    with pytest.raises(ValueError, match="descrizione"):
        s.add_spesa(tue, Decimal(3), " ")
    acconto = s.add_wage_payment(tue, Decimal(100), "acconto")
    saldo = s.add_wage_payment(date(2026, 11, 2), Decimal("255.72"), "")
    assert s.wage_payments() == [acconto, saldo]
    assert s.delete_wage_payment(acconto.id) == tue and s.wage_payments() == [saldo]
    with pytest.raises(KeyError):
        s.delete_wage_payment(acconto.id)
    # A spesa belongs to its month's payslip: frozen with it. Payments have no month.
    worker = standard_contract(date(2026, 10, 12))
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026(), []))
    with pytest.raises(ValueError, match="finalizzato"):
        s.delete_spesa(merenda.id)
    with pytest.raises(ValueError, match="finalizzato"):
        s.add_spesa(tue, Decimal(2), "gelato")
    assert s.delete_wage_payment(saldo.id) == date(2026, 11, 2)


def test_version_2_worker_passwords_need_a_login_name(tmp_path: Path) -> None:
    # Version 2 had a password per rapporto and no login names: the upgrade adds the column
    # and drops the password, which no longer identifies anyone on its own.
    path = tmp_path / "tata.sqlite"
    Store(path).add_rapporto(standard_contract(date(2026, 10, 12)))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP INDEX one_worker_user")
        db.execute("ALTER TABLE contract DROP COLUMN worker_user")
        db.execute("UPDATE contract SET worker_hash = 'scrypt$00$00'")
        db.execute("PRAGMA user_version = 2")
    root = Store(path)
    assert root.worker_hashes() == {} and root.rapporti()[0].worker_user is None
    root.set_worker_login(1, ("necchi", "scrypt$00$00"))
    assert root.worker_login("necchi") == (1, "scrypt$00$00")
