"""SQLite persistence. Stores facts only; never computes."""

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from tata.models import Contract, Event, EventKind, Payment, Punch, Span, TfrAdvance
from tata.payslip import Payslip

SCHEMA = """
CREATE TABLE IF NOT EXISTS contract (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    span_start INTEGER,
    span_end INTEGER,
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payslip_final (
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    data TEXT NOT NULL,
    finalized_at TEXT NOT NULL,
    PRIMARY KEY (year, month)
);
CREATE TABLE IF NOT EXISTS payment (
    year INTEGER NOT NULL,
    quarter INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (year, quarter)
);
CREATE TABLE IF NOT EXISTS tfr_advance (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    amount TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS punch (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    start INTEGER NOT NULL,
    stop INTEGER
);
CREATE TABLE IF NOT EXISTS punch_event (
    event_id INTEGER PRIMARY KEY,
    punch_id INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_punch ON punch ((stop IS NULL)) WHERE stop IS NULL;
"""


Row = tuple[object, ...]


def _insert_extras(db: sqlite3.Connection, punch_id: int, day: date, extras: list[Span]) -> None:
    for s in extras:
        event_id = db.execute(
            "INSERT INTO event (day, kind, span_start, span_end, note) VALUES (?, ?, ?, ?, ?)",
            (day.isoformat(), EventKind.EXTRA.value, s.start, s.end, "timbratura"),
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


class Store:
    """One short-lived connection per call: no shared handle, nothing to close."""

    def __init__(self, path: Path) -> None:
        self._path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(SCHEMA)

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

    def contract(self) -> Contract | None:
        rows = self._read("SELECT data FROM contract WHERE id = 1", ())
        return Contract.model_validate_json(str(rows[0][0])) if rows else None

    def save_contract(self, contract: Contract, punch_extras: dict[int, list[Span]]) -> None:
        """Saves the contract and, in the same transaction, replaces the EXTRA events of each
        punch in `punch_extras` with the given spans (the overruns under the new schedule)."""
        days = {p.id: p.day for p in self.punches()}
        for punch_id in punch_extras:
            self._refuse_if_final(days[punch_id])
        with closing(sqlite3.connect(self._path)) as db, db:
            for punch_id, extras in punch_extras.items():
                _delete_extras(db, punch_id)
                _insert_extras(db, punch_id, days[punch_id], extras)
            db.execute(
                "INSERT INTO contract (id, data) VALUES (1, ?) "
                "ON CONFLICT (id) DO UPDATE SET data = excluded.data",
                (contract.model_dump_json(),),
            )

    # ---- events

    def events(self) -> list[Event]:
        rows = self._read(
            "SELECT id, day, kind, span_start, span_end, note FROM event ORDER BY day, id", ()
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
            "INSERT INTO event (day, kind, span_start, span_end, note) VALUES (?, ?, ?, ?, ?)",
            (
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
        rows = self._read("SELECT day FROM event WHERE id = ?", (event_id,))
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
        rows = self._read("SELECT id, day, start, stop FROM punch ORDER BY day, start", ())
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
                    "INSERT INTO punch (day, start, stop) VALUES (?, ?, ?)",
                    (day.isoformat(), start, end),
                ).lastrowid
            except sqlite3.IntegrityError as e:
                if "one_open_punch" not in str(e):
                    raise
                raise ValueError("c'è già una timbratura aperta") from e
            if new_id is None:
                raise RuntimeError("sqlite non ha restituito un id")
            _insert_extras(db, new_id, day, extras)
        return draft.model_copy(update={"id": new_id})

    def close_punch(self, punch_id: int, end: int, extras: list[Span]) -> Punch:
        """Sets the exit time and stores the EXTRA events it implies, in one transaction."""
        punch = self._punch(punch_id)
        self._refuse_if_final(punch.day)
        closed = Punch(id=punch.id, day=punch.day, start=punch.start, end=end)  # validates
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("UPDATE punch SET stop = ? WHERE id = ?", (end, punch_id))
            _insert_extras(db, punch_id, punch.day, extras)
        return closed

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
        rows = self._read("SELECT event_id, punch_id FROM punch_event", ())
        return {int(str(e)): int(str(p)) for e, p in rows}

    def _punch(self, punch_id: int) -> Punch:
        found = [p for p in self.punches() if p.id == punch_id]
        if not found:
            raise KeyError(punch_id)
        return found[0]

    # ---- finalized payslips

    def final_payslip(self, year: int, month: int) -> Payslip | None:
        rows = self._read(
            "SELECT data FROM payslip_final WHERE year = ? AND month = ?", (year, month)
        )
        return Payslip.model_validate_json(str(rows[0][0])) if rows else None

    def finalize(self, payslip: Payslip) -> None:
        self._refuse_if_final(date(payslip.year, payslip.month, 1))
        self._write(
            "INSERT INTO payslip_final (year, month, data, finalized_at) VALUES (?, ?, ?, ?)",
            (payslip.year, payslip.month, payslip.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def _refuse_if_final(self, day: date) -> None:
        if self.final_payslip(day.year, day.month) is not None:
            raise ValueError(f"il mese {day.month}/{day.year} è già finalizzato")

    # ---- INPS payments

    def payments(self) -> list[Payment]:
        rows = self._read("SELECT data FROM payment ORDER BY year, quarter", ())
        return [Payment.model_validate_json(str(data)) for (data,) in rows]

    def record_payment(self, payment: Payment) -> None:
        self._write(
            "INSERT INTO payment (year, quarter, data) VALUES (?, ?, ?) "
            "ON CONFLICT (year, quarter) DO UPDATE SET data = excluded.data",
            (payment.year, payment.quarter, payment.model_dump_json()),
        )

    # ---- TFR advances

    def advances(self) -> list[TfrAdvance]:
        rows = self._read("SELECT id, day, amount FROM tfr_advance ORDER BY day, id", ())
        return [
            TfrAdvance.model_validate({"id": id_, "day": day, "amount": amount})
            for id_, day, amount in rows
        ]

    def add_advance(self, day: date, amount: Decimal) -> TfrAdvance:
        new_id = self._write(
            "INSERT INTO tfr_advance (day, amount) VALUES (?, ?)", (day.isoformat(), str(amount))
        )
        return TfrAdvance(id=new_id, day=day, amount=amount)
