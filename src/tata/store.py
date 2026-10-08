"""SQLite persistence. Stores facts only; never computes."""

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from tata.models import Contract, Event, EventKind, Payment, Span, TfrAdvance
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
"""


Row = tuple[object, ...]


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

    def save_contract(self, contract: Contract) -> None:
        self._write(
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
        self._write("DELETE FROM event WHERE id = ?", (event_id,))
        return day

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
