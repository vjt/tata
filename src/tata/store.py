"""SQLite persistence. Stores facts only; never computes."""

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from tata.models import (
    Contract,
    Event,
    EventKind,
    Payment,
    Punch,
    Rapporto,
    Session,
    Span,
    Spesa,
    TfrAdvance,
    WagePayment,
)
from tata.payslip import Payslip

SCHEMA = """
CREATE TABLE IF NOT EXISTS contract (
    id INTEGER PRIMARY KEY,
    data TEXT NOT NULL,
    ha_entity TEXT NOT NULL,
    worker_hash TEXT,
    worker_user TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_worker_user ON contract (worker_user);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contract (id),
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    span_start INTEGER,
    span_end INTEGER,
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payslip_final (
    contract_id INTEGER NOT NULL REFERENCES contract (id),
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    data TEXT NOT NULL,
    finalized_at TEXT NOT NULL,
    PRIMARY KEY (contract_id, year, month)
);
CREATE TABLE IF NOT EXISTS payment (
    contract_id INTEGER NOT NULL REFERENCES contract (id),
    year INTEGER NOT NULL,
    quarter INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (contract_id, year, quarter)
);
CREATE TABLE IF NOT EXISTS tfr_advance (
    id INTEGER PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contract (id),
    day TEXT NOT NULL,
    amount TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spesa (
    id INTEGER PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contract (id),
    day TEXT NOT NULL,
    importo TEXT NOT NULL,
    descrizione TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wage_payment (
    id INTEGER PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contract (id),
    day TEXT NOT NULL,
    importo TEXT NOT NULL,
    nota TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS punch (
    id INTEGER PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contract (id),
    day TEXT NOT NULL,
    start INTEGER NOT NULL,
    stop INTEGER
);
CREATE TABLE IF NOT EXISTS punch_event (
    event_id INTEGER PRIMARY KEY,
    punch_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS employer_car (
    contract_id INTEGER NOT NULL REFERENCES contract (id),
    day TEXT NOT NULL,
    morning INTEGER NOT NULL,
    PRIMARY KEY (contract_id, day, morning)
);
CREATE TABLE IF NOT EXISTS wifi_rejected (
    contract_id INTEGER NOT NULL REFERENCES contract (id),
    day TEXT NOT NULL,
    PRIMARY KEY (contract_id, day)
);
CREATE TABLE IF NOT EXISTS turno_km (
    contract_id INTEGER NOT NULL REFERENCES contract (id),
    day TEXT NOT NULL,
    morning INTEGER NOT NULL,
    km TEXT NOT NULL,
    PRIMARY KEY (contract_id, day, morning)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_punch ON punch (contract_id) WHERE stop IS NULL;
CREATE TABLE IF NOT EXISTS session (
    token_hash TEXT PRIMARY KEY,
    contract_id INTEGER REFERENCES contract (id),
    credential_tag TEXT NOT NULL,
    expires TEXT NOT NULL
);
"""
# PRAGMA user_version. 1: rapporti. 2: login sessions (a new table: the schema adds it).
# 3: worker login names. 4: km per turno (a new table: the schema adds it).
# 5: spese, wage payments (new tables) and the contract's `trattiene` (REWRITES).
# 6: `trattiene` becomes `gestione` (REWRITES). 7: the contract's `rimborso_km` (REWRITES).
# 8: rejected wifi days (a new table: the schema adds it).
VERSION = 8

# Run on a database of version 1 or later, before the schema, to reach the given version.
UPGRADES = {
    # A password without a login name opens nothing: the employer gives both again.
    3: [
        "ALTER TABLE contract ADD COLUMN worker_user TEXT",
        "UPDATE contract SET worker_hash = NULL",
    ],
}

# Run after the schema and the copy from version 0, to reach the given version: rewrites of
# the stored JSON. Idempotent, so a version 0 database gets them all.
REWRITES = {
    # Contracts so far withheld her share; finalized payslips: rimborso -> rimborsi.
    5: [
        "UPDATE contract SET data = json_set(data, '$.trattiene', json('true'))",
        "UPDATE payslip_final SET data = json_set(json_remove(data, '$.rimborso', "
        "'$.lordo_da_pagare'), '$.rimborsi', CASE WHEN json_type(data, '$.rimborso') = 'object' "
        "THEN json_array(json(json_extract(data, '$.rimborso'))) ELSE json('[]') END, "
        "'$.contract.trattiene', json('true')) WHERE json_type(data, '$.rimborsi') IS NULL",
    ],
    # trattiene (bool) -> gestione: netto (withheld) or lordo.
    6: [
        f"UPDATE {table} SET data = json_remove(json_set(data, '{path}.gestione', "
        f"CASE json_extract(data, '{path}.trattiene') WHEN 0 THEN 'lordo' ELSE 'netto' END), "
        f"'{path}.trattiene') WHERE json_type(data, '{path}.trattiene') IS NOT NULL"
        for table, path in (("contract", "$"), ("payslip_final", "$.contract"))
    ],
    # Every rapporto so far paid the rimborso trasferte.
    7: [
        f"UPDATE {table} SET data = json_set(data, '{path}.rimborso_km', json('true')) "
        f"WHERE json_type(data, '{path}.rimborso_km') IS NULL"
        for table, path in (("contract", "$"), ("payslip_final", "$.contract"))
    ],
}

# Version 0 had one rapporto per installation: its rows all become rapporto 1.
COPY_FROM_V0 = {
    "contract": "INSERT INTO contract SELECT id, data, '', NULL, NULL FROM contract_v0",
    "event": "INSERT INTO event SELECT id, 1, day, kind, span_start, span_end, note FROM event_v0",
    "payslip_final": "INSERT INTO payslip_final SELECT 1, year, month, data, finalized_at "
    "FROM payslip_final_v0",
    "payment": "INSERT INTO payment SELECT 1, year, quarter, data FROM payment_v0",
    "tfr_advance": "INSERT INTO tfr_advance SELECT id, 1, day, amount FROM tfr_advance_v0",
    "punch": "INSERT INTO punch SELECT id, 1, day, start, stop FROM punch_v0",
    "employer_car": "INSERT INTO employer_car SELECT 1, day, morning FROM employer_car_v0",
}


def _upgrade(db: sqlite3.Connection) -> None:
    """Create or upgrade the schema to VERSION. One transaction: all or nothing."""
    (version,) = db.execute("PRAGMA user_version").fetchone()
    if version == VERSION:
        return
    if version > VERSION:
        raise RuntimeError(f"database versione {version}, questa tata conosce fino alla {VERSION}")
    tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    old = [t for t in COPY_FROM_V0 if t in tables] if version == 0 else []
    db.execute("BEGIN")
    try:
        db.execute("DROP INDEX IF EXISTS one_open_punch")
        for t in old:
            db.execute(f"ALTER TABLE {t} RENAME TO {t}_v0")
        steps = range(version + 1, VERSION + 1) if version > 0 else range(0)
        for statement in (s for step in steps for s in UPGRADES.get(step, [])):
            db.execute(statement)
        for statement in SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        for t in old:
            db.execute(COPY_FROM_V0[t])
            db.execute(f"DROP TABLE {t}_v0")
        for statement in (
            s for step in range(version + 1, VERSION + 1) for s in REWRITES.get(step, [])
        ):
            db.execute(statement)
        db.execute(f"PRAGMA user_version = {VERSION}")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


Row = tuple[object, ...]


def _insert_extras(
    db: sqlite3.Connection, contract_id: int, punch_id: int, day: date, extras: list[Span]
) -> None:
    for s in extras:
        event_id = db.execute(
            "INSERT INTO event (contract_id, day, kind, span_start, span_end, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (contract_id, day.isoformat(), EventKind.EXTRA.value, s.start, s.end, "timbratura"),
        ).lastrowid
        if event_id is None:  # would let punch_event auto-assign an unrelated id
            raise RuntimeError("sqlite non ha restituito un id")
        db.execute(
            "INSERT INTO punch_event (event_id, punch_id) VALUES (?, ?)", (event_id, punch_id)
        )


def _delete_extras(db: sqlite3.Connection, punch_id: int) -> None:
    db.execute(
        "DELETE FROM event WHERE id IN (SELECT event_id FROM punch_event WHERE punch_id = ?)",
        (punch_id,),
    )
    db.execute("DELETE FROM punch_event WHERE punch_id = ?", (punch_id,))


class RapportoStore:
    """One rapporto's rows. One short-lived connection per call: nothing to close."""

    def __init__(self, path: Path, contract_id: int) -> None:
        self._path = path
        self.id = contract_id

    def _read(self, sql: str, params: tuple[object, ...]) -> list[Row]:
        with closing(sqlite3.connect(self._path)) as db:
            return db.execute(sql, params).fetchall()

    def _write(self, sql: str, params: tuple[object, ...]) -> int:
        """Run one statement in a transaction; returns lastrowid."""
        with closing(sqlite3.connect(self._path)) as db, db:
            rowid = db.execute(sql, params).lastrowid
        if rowid is None:
            raise RuntimeError("sqlite non ha restituito un id")
        return rowid

    # ---- contract

    def contract(self) -> Contract:
        rows = self._read("SELECT data FROM contract WHERE id = ?", (self.id,))
        return Contract.model_validate_json(str(rows[0][0]))

    def save_contract(self, contract: Contract, punch_extras: dict[int, list[Span]]) -> None:
        """Saves the contract and, in the same transaction, replaces the EXTRA events of each
        punch in `punch_extras` with the given spans (the overruns under the new schedule)."""
        days = {p.id: p.day for p in self.punches()}
        for punch_id in punch_extras:
            self._refuse_if_final(days[punch_id])
        with closing(sqlite3.connect(self._path)) as db, db:
            for punch_id, extras in punch_extras.items():
                _delete_extras(db, punch_id)
                _insert_extras(db, self.id, punch_id, days[punch_id], extras)
            db.execute(
                "UPDATE contract SET data = ? WHERE id = ?", (contract.model_dump_json(), self.id)
            )

    # ---- events

    def events(self) -> list[Event]:
        rows = self._read(
            "SELECT id, day, kind, span_start, span_end, note FROM event "
            "WHERE contract_id = ? ORDER BY day, id",
            (self.id,),
        )
        return [
            Event.model_validate(
                {
                    "id": id_,
                    "day": day,
                    "kind": kind,
                    "span": None if start is None else {"start": start, "end": end},
                    "note": note,
                }
            )
            for id_, day, kind, start, end, note in rows
        ]

    def add_event(self, day: date, kind: EventKind, span: Span | None, note: str) -> Event:
        self._refuse_if_final(day)
        draft = Event(id=0, day=day, kind=kind, span=span, note=note)  # validates first
        new_id = self._write(
            "INSERT INTO event (contract_id, day, kind, span_start, span_end, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                self.id,
                day.isoformat(),
                kind.value,
                None if span is None else span.start,
                None if span is None else span.end,
                note,
            ),
        )
        return draft.model_copy(update={"id": new_id})

    def delete_event(self, event_id: int) -> date:
        """Deletes the event and returns its day. Raises KeyError if it does not exist."""
        rows = self._read(
            "SELECT day FROM event WHERE id = ? AND contract_id = ?", (event_id, self.id)
        )
        if not rows:
            raise KeyError(event_id)
        day = date.fromisoformat(str(rows[0][0]))
        self._refuse_if_final(day)
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("DELETE FROM punch_event WHERE event_id = ?", (event_id,))
            db.execute("DELETE FROM event WHERE id = ?", (event_id,))
        return day

    # ---- punches (cartellino)

    def punches(self) -> list[Punch]:
        rows = self._read(
            "SELECT id, day, start, stop FROM punch WHERE contract_id = ? ORDER BY day, start",
            (self.id,),
        )
        return [
            Punch.model_validate({"id": id_, "day": day, "start": start, "end": stop})
            for id_, day, start, stop in rows
        ]

    def add_punch(self, day: date, start: int, end: int | None, extras: list[Span]) -> Punch:
        """Stores a punch and the EXTRA events it implies, in one transaction."""
        self._refuse_if_final(day)
        draft = Punch(id=0, day=day, start=start, end=end)  # validates first
        with closing(sqlite3.connect(self._path)) as db, db:
            try:
                new_id = db.execute(
                    "INSERT INTO punch (contract_id, day, start, stop) VALUES (?, ?, ?, ?)",
                    (self.id, day.isoformat(), start, end),
                ).lastrowid
            except sqlite3.IntegrityError as e:
                if "punch.contract_id" not in str(e):  # one_open_punch, per rapporto
                    raise
                raise ValueError("c'è già una timbratura aperta") from e
            if new_id is None:
                raise RuntimeError("sqlite non ha restituito un id")
            _insert_extras(db, self.id, new_id, day, extras)
        return draft.model_copy(update={"id": new_id})

    def update_punch(
        self, punch_id: int, day: date, start: int, end: int, extras: list[Span]
    ) -> Punch:
        """Closes or corrects a punch: replaces its times and the EXTRA events it implies, in
        one transaction. Raises KeyError if there is no such punch."""
        old = self._punch(punch_id)
        self._refuse_if_final(old.day)
        self._refuse_if_final(day)
        new = Punch(id=punch_id, day=day, start=start, end=end)  # validates
        with closing(sqlite3.connect(self._path)) as db, db:
            _delete_extras(db, punch_id)
            db.execute(
                "UPDATE punch SET day = ?, start = ?, stop = ? WHERE id = ?",
                (day.isoformat(), start, end, punch_id),
            )
            _insert_extras(db, self.id, punch_id, day, extras)
        return new

    def delete_punch(self, punch_id: int) -> date:
        """Deletes the punch and the EXTRA events it generated; returns its day."""
        punch = self._punch(punch_id)
        self._refuse_if_final(punch.day)
        with closing(sqlite3.connect(self._path)) as db, db:
            _delete_extras(db, punch_id)
            db.execute("DELETE FROM punch WHERE id = ?", (punch_id,))
        return punch.day

    def punch_links(self) -> dict[int, int]:
        """The EXTRA events a punch generated: event id -> punch id."""
        rows = self._read(
            "SELECT event_id, punch_id FROM punch_event "
            "JOIN punch ON punch.id = punch_id WHERE contract_id = ?",
            (self.id,),
        )
        return {int(str(e)): int(str(p)) for e, p in rows}

    def _punch(self, punch_id: int) -> Punch:
        found = [p for p in self.punches() if p.id == punch_id]
        if not found:
            raise KeyError(punch_id)
        return found[0]

    # ---- finalized payslips

    def final_payslip(self, year: int, month: int) -> Payslip | None:
        rows = self._read(
            "SELECT data FROM payslip_final WHERE contract_id = ? AND year = ? AND month = ?",
            (self.id, year, month),
        )
        return Payslip.model_validate_json(str(rows[0][0])) if rows else None

    def finalize(self, payslip: Payslip) -> None:
        self._refuse_if_final(date(payslip.year, payslip.month, 1))
        self._write(
            "INSERT INTO payslip_final (contract_id, year, month, data, finalized_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                self.id,
                payslip.year,
                payslip.month,
                payslip.model_dump_json(),
                datetime.now(UTC).isoformat(),
            ),
        )

    def _refuse_if_final(self, day: date) -> None:
        if self.final_payslip(day.year, day.month) is not None:
            raise ValueError(f"il mese {day.month}/{day.year} è già finalizzato")

    # ---- turni done with the employer's car: no rimborso trasferte

    def employer_car(self) -> set[tuple[date, bool]]:
        """(day, morning) of the turni done with the employer's car."""
        rows = self._read("SELECT day, morning FROM employer_car WHERE contract_id = ?", (self.id,))
        return {(date.fromisoformat(str(day)), bool(morning)) for day, morning in rows}

    def set_employer_car(self, day: date, morning: bool, employer: bool) -> None:
        self._refuse_if_final(day)
        key = (self.id, day.isoformat(), int(morning))
        with closing(sqlite3.connect(self._path)) as db, db:
            if employer:
                db.execute(
                    "INSERT OR IGNORE INTO employer_car (contract_id, day, morning) "
                    "VALUES (?, ?, ?)",
                    key,
                )
            else:
                db.execute(
                    "DELETE FROM employer_car WHERE contract_id = ? AND day = ? AND morning = ?",
                    key,
                )

    # ---- days whose wifi presence the employer will not record

    def wifi_rejected(self) -> set[date]:
        rows = self._read("SELECT day FROM wifi_rejected WHERE contract_id = ?", (self.id,))
        return {date.fromisoformat(str(day)) for (day,) in rows}

    def set_wifi_rejected(self, day: date, rejected: bool) -> None:
        key = (self.id, day.isoformat())
        with closing(sqlite3.connect(self._path)) as db, db:
            if rejected:
                db.execute(
                    "INSERT OR IGNORE INTO wifi_rejected (contract_id, day) VALUES (?, ?)", key
                )
            else:
                db.execute("DELETE FROM wifi_rejected WHERE contract_id = ? AND day = ?", key)

    # ---- km of a turno other than the configured km per turno

    def turno_km(self) -> dict[tuple[date, bool], Decimal]:
        """(day, morning) -> km, for the turni whose km the employer corrected."""
        rows = self._read("SELECT day, morning, km FROM turno_km WHERE contract_id = ?", (self.id,))
        return {(date.fromisoformat(str(d)), bool(m)): Decimal(str(km)) for d, m, km in rows}

    def set_turno_km(self, day: date, morning: bool, km: Decimal | None) -> None:
        """Sets a turno's km; None: back to the configured km per turno."""
        self._refuse_if_final(day)
        if km is not None and (not km.is_finite() or km < 0):
            raise ValueError(f"km del turno non validi: {km}")
        key = (self.id, day.isoformat(), int(morning))
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute(
                "DELETE FROM turno_km WHERE contract_id = ? AND day = ? AND morning = ?", key
            )
            if km is not None:
                db.execute("INSERT INTO turno_km VALUES (?, ?, ?, ?)", (*key, str(km)))

    # ---- INPS payments

    def payments(self) -> list[Payment]:
        rows = self._read(
            "SELECT data FROM payment WHERE contract_id = ? ORDER BY year, quarter", (self.id,)
        )
        return [Payment.model_validate_json(str(data)) for (data,) in rows]

    def record_payment(self, payment: Payment) -> None:
        self._write(
            "INSERT INTO payment (contract_id, year, quarter, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (contract_id, year, quarter) DO UPDATE SET data = excluded.data",
            (self.id, payment.year, payment.quarter, payment.model_dump_json()),
        )

    # ---- TFR advances

    def advances(self) -> list[TfrAdvance]:
        rows = self._read(
            "SELECT id, day, amount FROM tfr_advance WHERE contract_id = ? ORDER BY day, id",
            (self.id,),
        )
        return [
            TfrAdvance.model_validate({"id": id_, "day": day, "amount": amount})
            for id_, day, amount in rows
        ]

    def add_advance(self, day: date, amount: Decimal) -> TfrAdvance:
        new_id = self._write(
            "INSERT INTO tfr_advance (contract_id, day, amount) VALUES (?, ?, ?)",
            (self.id, day.isoformat(), str(amount)),
        )
        return TfrAdvance(id=new_id, day=day, amount=amount)

    # ---- spese: reimbursed in the payslip of their month

    def spese(self) -> list[Spesa]:
        rows = self._read(
            "SELECT id, day, importo, descrizione FROM spesa WHERE contract_id = ? "
            "ORDER BY day, id",
            (self.id,),
        )
        return [
            Spesa.model_validate({"id": i, "day": d, "importo": a, "descrizione": t})
            for i, d, a, t in rows
        ]

    def add_spesa(self, day: date, importo: Decimal, descrizione: str) -> Spesa:
        self._refuse_if_final(day)
        draft = Spesa(id=0, day=day, importo=importo, descrizione=descrizione)  # validates
        new_id = self._write(
            "INSERT INTO spesa (contract_id, day, importo, descrizione) VALUES (?, ?, ?, ?)",
            (self.id, day.isoformat(), str(importo), descrizione),
        )
        return draft.model_copy(update={"id": new_id})

    def delete_spesa(self, spesa_id: int) -> date:
        """Deletes the spesa and returns its day. Raises KeyError if it does not exist."""
        found = [s for s in self.spese() if s.id == spesa_id]
        if not found:
            raise KeyError(spesa_id)
        self._refuse_if_final(found[0].day)
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("DELETE FROM spesa WHERE id = ?", (spesa_id,))
        return found[0].day

    # ---- what the employer paid her: no month, the balance sums them

    def wage_payments(self) -> list[WagePayment]:
        rows = self._read(
            "SELECT id, day, importo, nota FROM wage_payment WHERE contract_id = ? "
            "ORDER BY day, id",
            (self.id,),
        )
        return [
            WagePayment.model_validate({"id": i, "day": d, "importo": a, "nota": n})
            for i, d, a, n in rows
        ]

    def add_wage_payment(self, day: date, importo: Decimal, nota: str) -> WagePayment:
        draft = WagePayment(id=0, day=day, importo=importo, nota=nota)  # validates
        new_id = self._write(
            "INSERT INTO wage_payment (contract_id, day, importo, nota) VALUES (?, ?, ?, ?)",
            (self.id, day.isoformat(), str(importo), nota),
        )
        return draft.model_copy(update={"id": new_id})

    def delete_wage_payment(self, payment_id: int) -> date:
        """Deletes the payment and returns its day. Raises KeyError if it does not exist."""
        found = [p for p in self.wage_payments() if p.id == payment_id]
        if not found:
            raise KeyError(payment_id)
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("DELETE FROM wage_payment WHERE id = ?", (payment_id,))
        return found[0].day


class Store:
    """The employer's rapporti, and what spans them. One short-lived connection per call."""

    def __init__(self, path: Path) -> None:
        self._path = path
        with closing(sqlite3.connect(path, isolation_level=None)) as db:
            _upgrade(db)

    def _read(self, sql: str, params: tuple[object, ...]) -> list[Row]:
        with closing(sqlite3.connect(self._path)) as db:
            return db.execute(sql, params).fetchall()

    def _update(self, sql: str, params: tuple[object, ...]) -> None:
        with closing(sqlite3.connect(self._path)) as db, db:
            if db.execute(sql, params).rowcount != 1:
                raise KeyError(params[-1])

    def rapporti(self) -> list[Rapporto]:
        rows = self._read("SELECT id, data, ha_entity, worker_user FROM contract ORDER BY id", ())
        return [
            Rapporto(
                id=int(str(id_)),
                contract=Contract.model_validate_json(str(data)),
                ha_entity=str(entity),
                worker_user=None if user is None else str(user),
            )
            for id_, data, entity, user in rows
        ]

    def add_rapporto(self, contract: Contract) -> int:
        with closing(sqlite3.connect(self._path)) as db, db:
            new_id = db.execute(
                "INSERT INTO contract (data, ha_entity, worker_hash) VALUES (?, '', NULL)",
                (contract.model_dump_json(),),
            ).lastrowid
        if new_id is None:
            raise RuntimeError("sqlite non ha restituito un id")
        return new_id

    def rapporto(self, contract_id: int) -> RapportoStore:
        """Raises KeyError if there is no such rapporto."""
        if not self._read("SELECT 1 FROM contract WHERE id = ?", (contract_id,)):
            raise KeyError(contract_id)
        return RapportoStore(self._path, contract_id)

    def set_ha_entity(self, contract_id: int, entity: str) -> None:
        self._update("UPDATE contract SET ha_entity = ? WHERE id = ?", (entity, contract_id))

    def set_worker_login(self, contract_id: int, login: tuple[str, str] | None) -> None:
        """The worker's login name and password hash (computed by the caller); None closes
        her access. Raises ValueError if another rapporto has that name."""
        user, worker_hash = login if login else (None, None)
        try:
            self._update(
                "UPDATE contract SET worker_user = ?, worker_hash = ? WHERE id = ?",
                (user, worker_hash, contract_id),
            )
        except sqlite3.IntegrityError as e:
            if "contract.worker_user" not in str(e):
                raise
            raise ValueError(f"nome utente {user} già in uso") from e
        with closing(sqlite3.connect(self._path)) as db, db:  # her old password logs her out
            db.execute("DELETE FROM session WHERE contract_id = ?", (contract_id,))

    def worker_login(self, user: str) -> tuple[int, str] | None:
        """The rapporto with that login name and its password hash, if any."""
        rows = self._read(
            "SELECT id, worker_hash FROM contract "
            "WHERE worker_user = ? AND worker_hash IS NOT NULL",
            (user,),
        )
        return next(((int(str(id_)), str(h)) for id_, h in rows), None)

    def worker_hashes(self) -> dict[int, str]:
        rows = self._read("SELECT id, worker_hash FROM contract WHERE worker_hash IS NOT NULL", ())
        return {int(str(id_)): str(h) for id_, h in rows}

    # ---- login sessions: the cookie's token is never stored, only its hash

    def add_session(self, session: Session, purge_before: datetime) -> None:
        """Stores the session, dropping those expired before `purge_before`."""
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("DELETE FROM session WHERE expires < ?", (purge_before.isoformat(),))
            db.execute(
                "INSERT INTO session VALUES (?, ?, ?, ?)",
                (
                    session.token_hash,
                    session.contract_id,
                    session.credential_tag,
                    session.expires.isoformat(),
                ),
            )

    def session(self, token_hash: str) -> Session | None:
        rows = self._read(
            "SELECT contract_id, credential_tag, expires FROM session WHERE token_hash = ?",
            (token_hash,),
        )
        if not rows:
            return None
        contract_id, tag, expires = rows[0]
        return Session.model_validate(
            {
                "token_hash": token_hash,
                "contract_id": contract_id,
                "credential_tag": tag,
                "expires": expires,
            }
        )

    def delete_session(self, token_hash: str) -> None:
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("DELETE FROM session WHERE token_hash = ?", (token_hash,))

    def all_payments(self) -> list[Payment]:
        """Every rapporto's INPS payments: the employer's deduction sums them."""
        rows = self._read("SELECT data FROM payment ORDER BY year, quarter, contract_id", ())
        return [Payment.model_validate_json(str(data)) for (data,) in rows]
