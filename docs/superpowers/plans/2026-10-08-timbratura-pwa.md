# Timbratura PWA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An installable one-button page (`/timbra`) that records the worker's entry/exit
(cartellino) and turns the difference from the schedule into payroll exceptions: overruns become
`EXTRA` automatically, shortfalls are proposed and confirmed by the employer.

**Architecture:** Punches are a new stored fact (`punch` table). A new pure calc module
`timbratura.py` derives from them the button proposal, the `EXTRA` spans of a closed punch and the
pending shortfalls. The payslip calculators keep reading only contract + events; the only change
there is the new `EventKind.MORA` (paid, no effect on hours). The web layer stays server-rendered,
no JavaScript; the PWA is a manifest + icons, no service worker.

**Tech Stack:** Python 3.13, FastAPI + Jinja2, stdlib sqlite3, Pydantic v2, pytest. Everything runs
in the dev container: `scripts/test.sh`, `scripts/lint.sh`, `scripts/format.sh`, `scripts/check.sh`.

**Spec:** `docs/superpowers/specs/2026-10-08-timbratura-pwa-design.md`

## Global Constraints

- Read `CLAUDE.md` first. AST-enforced: no `float`, no default arguments, no `@dataclass`, imports
  at module top only, calc modules (`timbratura.py`, `hours.py`) do no I/O, env read only in
  `app_from_env`.
- Pyright strict, ruff, zero warnings. Run `scripts/format.sh` before `scripts/check.sh`.
- Times are integer minutes from midnight (`Span`, 0..1440). Never `datetime` arithmetic in calc
  beyond reading `now.date()`, `now.hour`, `now.minute`.
- `WINDOW = 30` minutes, `TOLERANCE = 5` minutes (named constants in `timbratura.py`).
- Invented people and times only in tests and docs. Never the real household's data.
- User-facing strings and error messages in Italian, code and comments in English.
- Commit message via a temp file written with the Write tool, `git commit -F`; neutral register;
  end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. The pre-commit
  hook runs `scripts/check.sh`.

## Review Focus

1. Finalizing the current month before it ends: spans after the finalization date later become
   shortfalls in a month that refuses events. Expected: they are not shown nor counted (the web
   layer drops shortfalls of finalized months). Test in Task 6.
2. Tapping "Fine" in the same minute as "Inizio": expected a 400 page with the reason, nothing
   stored. Test in Task 6.
3. Open punch left over from yesterday: the button page must ask for the exit time, never close
   it at today's clock. Test in Task 6.
4. A punch on a festività or Sunday: the whole punch is `EXTRA` (festivo bucket), no shortfall.
   Test in Task 3.
5. Late arrival within tolerance (≤ 5 min) produces neither a shortfall nor an `EXTRA`. Test in
   Task 3.

---

### Task 1: `EventKind.MORA`

The employer sent the worker home (art. 1206 ss. c.c.): the time stays paid as scheduled.
Recording it is what clears a shortfall.

**Files:**
- Modify: `src/tata/models.py` (EventKind, `SPAN_ONLY`, `Event._check`)
- Modify: `src/tata/hours.py` (`range_days`, `_day_work` validation)
- Modify: `src/tata/static/style.css` (badge colour)
- Modify: `src/tata/templates/mese.html` (hint text)
- Test: `tests/test_hours.py`, `tests/test_payslip.py`

**Interfaces:**
- Produces: `EventKind.MORA = "mora"`; `SPAN_ONLY: frozenset[EventKind]` = {EXTRA, MORA}.

- [ ] **Step 1: Write the failing tests**

In `tests/test_payslip.py` (imports already there: `compute_payslip`, helpers):

```python
def test_mora_pays_exactly_as_the_schedule() -> None:
    # Art. 1206 c.c.: the worker sent home keeps the pay. A MORA over the afternoon span of
    # Tuesday 13/10 must leave the October payslip identical to the one with no events.
    worker = standard_contract(date(2026, 10, 12))
    mora = event(date(2026, 10, 13), EventKind.MORA, "15:45-18:00")
    rates = rates_2026()
    assert compute_payslip(worker, [mora], 2026, 10, rates) == compute_payslip(
        worker, [], 2026, 10, rates
    )
```

In `tests/test_hours.py`:

```python
def test_mora_requires_a_span() -> None:
    with pytest.raises(ValueError, match="richiede un orario"):
        event(date(2026, 10, 13), EventKind.MORA, None)


def test_mora_outside_the_schedule_is_refused() -> None:
    # Nothing is scheduled at 12:00 on a weekday of the STANDARD schedule.
    worker = standard_contract(date(2026, 10, 12))
    mora = event(date(2026, 10, 13), EventKind.MORA, "12:00-13:00")
    with pytest.raises(ValueError, match="fuori dall'orario"):
        month_work(worker, [mora], 2026, 10, rates_2026())
```

(Add `import pytest` / helper imports to each file if missing.)

- [ ] **Step 2: Run, verify they fail**

Run: `scripts/test.sh tests/test_hours.py tests/test_payslip.py`
Expected: FAIL, `AttributeError: MORA`.

- [ ] **Step 3: Implement**

`src/tata/models.py`:

```python
class EventKind(StrEnum):
    """Exceptions to the standard schedule."""

    FERIE = "ferie"  # whole working day (Mon-Sat), paid 1/6 of weekly hours
    MALATTIA = "malattia"  # whole calendar day, paid per art. 27
    PERMESSO = "permesso"  # paid leave (art. 19), whole day or a time span
    ASSENZA = "assenza"  # unpaid absence, whole day or a time span
    EXTRA = "extra"  # hours worked beyond the schedule (time span required)
    MORA = "mora"  # sent home by the employer: paid as scheduled (art. 1206 c.c.), span required


WHOLE_DAY_ONLY = frozenset({EventKind.FERIE, EventKind.MALATTIA})
SPAN_ONLY = frozenset({EventKind.EXTRA, EventKind.MORA})
```

In `Event._check` replace the EXTRA check with:

```python
        if self.kind in SPAN_ONLY and self.span is None:
            raise ValueError(f"{self.kind} richiede un orario")
```

`src/tata/hours.py`, in `range_days` replace the EXTRA check with (import `SPAN_ONLY`):

```python
    if kind in SPAN_ONLY and first != last:
        raise ValueError(f"{kind} si registra un giorno alla volta")
```

In `_day_work`, right after `foreseen = len(scheduled)`:

```python
    for ev in events:
        if ev.kind is EventKind.MORA and ev.span is not None and not ev.span.minutes() <= scheduled:
            raise ValueError(f"mora fuori dall'orario del {day}")
```

MORA needs nothing else in `_day_work`: its minutes stay in `scheduled`, hence worked and paid.

`style.css`: add `--mora: #2a6f97;` to `:root` and
`.badge.mora { background: var(--mora); }` next to `.badge.extra`.

`mese.html` hint: replace "Extra: sempre un orario, un giorno alla volta." with
"Extra e mora (mandata a casa, pagata): sempre un orario, un giorno alla volta."

- [ ] **Step 4: Run, verify pass; grep for the old message**

Run: `grep -rn "le ore extra" src tests` → no hits expected (fix any test asserting the old text).
Run: `scripts/format.sh && scripts/check.sh` → all green.

- [ ] **Step 5: Commit** — "Add the mora event: time the employer sends the worker home is paid".

---

### Task 2: `Punch` model and stored punches

**Files:**
- Modify: `src/tata/models.py` (new `Punch`)
- Modify: `src/tata/store.py` (tables `punch`, `punch_event`; methods)
- Test: `tests/test_store.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `class Punch(Frozen)`: `id: int`, `day: date`, `start: int`, `end: int | None`;
    `span() -> Span` (raises `ValueError` if open).
  - `Store.punches() -> list[Punch]` ordered by day, start.
  - `Store.add_punch(day: date, start: int, end: int | None, extras: list[Span]) -> Punch`
  - `Store.close_punch(punch_id: int, end: int, extras: list[Span]) -> Punch` (KeyError if missing)
  - `Store.delete_punch(punch_id: int) -> date` (KeyError if missing)
  - `extras` are stored as `EXTRA` events with note `"timbratura"`, linked in `punch_event`.
  - All writes refuse a finalized month (`ValueError`, same as events).

- [ ] **Step 1: Failing tests**

`tests/test_models.py`:

```python
def test_punch_bounds() -> None:
    open_ = Punch(id=1, day=date(2026, 10, 13), start=480, end=None)
    with pytest.raises(ValueError, match="aperta"):
        open_.span()
    assert Punch(id=1, day=date(2026, 10, 13), start=480, end=525).span() == span("08:00-08:45")
    with pytest.raises(ValueError):
        Punch(id=1, day=date(2026, 10, 13), start=480, end=480)  # empty
    with pytest.raises(ValueError):
        Punch(id=1, day=date(2026, 10, 13), start=1440, end=None)  # starts at midnight
```

`tests/test_store.py`:

```python
def test_punch_close_stores_its_extras_and_delete_removes_them(tmp_path: Path) -> None:
    s = store(tmp_path)
    day = date(2026, 10, 13)
    opened = s.add_punch(day, 945, None, [])  # 15:45
    assert s.punches() == [opened]
    closed = s.close_punch(opened.id, 1110, [span("18:00-18:30")])  # 18:30
    assert s.punches() == [closed]
    assert closed.end == 1110
    [extra] = s.events()
    assert (extra.kind, extra.span, extra.note) == (EventKind.EXTRA, span("18:00-18:30"), "timbratura")
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
    s.finalize(compute_payslip(worker, [], 2026, 10, rates_2026()))
    with pytest.raises(ValueError, match="finalizzato"):
        s.add_punch(date(2026, 10, 13), 480, None, [])


def test_missing_punch_is_a_key_error(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        store(tmp_path).delete_punch(42)
```

- [ ] **Step 2: Run, verify fail** — `scripts/test.sh tests/test_models.py tests/test_store.py`.

- [ ] **Step 3: Implement**

`models.py`, after `Event`:

```python
class Punch(Frozen):
    """A cartellino entry: when the worker actually started and stopped. end None = still open."""

    id: int
    day: date
    start: int
    end: int | None

    @model_validator(mode="after")
    def _check(self) -> "Punch":
        if not 0 <= self.start < MINUTES_PER_DAY:
            raise ValueError(f"ora di ingresso non valida: {self.start}")
        if self.end is not None:
            self.span()  # same bounds as any span: start < end <= 24:00
        return self

    def span(self) -> Span:
        if self.end is None:
            raise ValueError(f"timbratura del {self.day} ancora aperta")
        return Span(start=self.start, end=self.end)
```

`store.py`: append to `SCHEMA`:

```sql
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
```

(`stop`, not `end`: END is an SQL keyword. New tables only, so `CREATE IF NOT EXISTS` is the whole
migration for the live database.)

Methods (import `Punch`):

```python
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
            new_id = db.execute(
                "INSERT INTO punch (day, start, stop) VALUES (?, ?, ?)",
                (day.isoformat(), start, end),
            ).lastrowid
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
            db.execute(
                "DELETE FROM event WHERE id IN (SELECT event_id FROM punch_event WHERE punch_id = ?)",
                (punch_id,),
            )
            db.execute("DELETE FROM punch_event WHERE punch_id = ?", (punch_id,))
            db.execute("DELETE FROM punch WHERE id = ?", (punch_id,))
        return punch.day

    def _punch(self, punch_id: int) -> Punch:
        found = [p for p in self.punches() if p.id == punch_id]
        if not found:
            raise KeyError(punch_id)
        return found[0]
```

Module-level helper:

```python
def _insert_extras(db: sqlite3.Connection, punch_id: int, day: date, extras: list[Span]) -> None:
    for s in extras:
        event_id = db.execute(
            "INSERT INTO event (day, kind, span_start, span_end, note) VALUES (?, ?, ?, ?, ?)",
            (day.isoformat(), EventKind.EXTRA.value, s.start, s.end, "timbratura"),
        ).lastrowid
        db.execute("INSERT INTO punch_event (event_id, punch_id) VALUES (?, ?)", (event_id, punch_id))
```

`delete_event`: replace the single `_write` with one transaction that also unlinks:

```python
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("DELETE FROM punch_event WHERE event_id = ?", (event_id,))
            db.execute("DELETE FROM event WHERE id = ?", (event_id,))
```

- [ ] **Step 4: Run, verify pass** — `scripts/format.sh && scripts/check.sh`.

- [ ] **Step 5: Commit** — "Store cartellino punches with the EXTRA events they generate".

---

### Task 3: `timbratura.py` — overruns and shortfalls

**Files:**
- Modify: `src/tata/hours.py` (extract `scheduled_minutes`, reused by `_day_work`)
- Create: `src/tata/timbratura.py`
- Test: `tests/test_timbratura.py`

**Interfaces:**
- Consumes: `Punch`, `SPAN_ONLY` (Task 1-2), `festivita(year, patrono_mese, patrono_giorno)`.
- Produces:
  - `hours.scheduled_minutes(contract: Contract, day: date, is_festivita: bool) -> set[int]`
  - `timbratura.TOLERANCE = 5`, `timbratura.WINDOW = 30`
  - `class Shortfall(Frozen)`: `day: date`, `span: Span`
  - `check_punches(punches: list[Punch]) -> None` (ValueError: overlap, >1 open)
  - `overruns(contract: Contract, punch: Punch) -> list[Span]`
  - `shortfalls(contract: Contract, events: list[Event], punches: list[Punch], now: datetime) -> list[Shortfall]`

- [ ] **Step 1: Failing tests** — `tests/test_timbratura.py`, schedule = `STANDARD` helper
(Mon-Fri 08:00-08:45 + 15:45-18:00, invented), hire Mon 12/10/2026, patrono 24/06.

```python
from datetime import date, datetime

import pytest

from tata.models import EventKind, Punch
from tata.timbratura import Shortfall, check_punches, overruns, shortfalls
from tests.helpers import event, span, standard_contract

WORKER = standard_contract(date(2026, 10, 12))
TUE = date(2026, 10, 13)


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


def test_late_arrival_beyond_tolerance_is_a_shortfall() -> None:
    # Morning 08:00-08:45 punched 08:10-08:45 -> 10 min missing. Afternoon punched in full.
    punches = [punch(TUE, "08:10-08:45"), punch(TUE, "15:45-18:00")]
    now = datetime(2026, 10, 13, 20, 0)
    assert shortfalls(WORKER, [], punches, now) == [Shortfall(day=TUE, span=span("08:00-08:10"))]


def test_late_arrival_within_tolerance_is_nothing() -> None:
    punches = [punch(TUE, "08:05-08:45"), punch(TUE, "15:45-17:56")]
    assert shortfalls(WORKER, [], punches, datetime(2026, 10, 13, 20, 0)) == []
    assert overruns(WORKER, punches[0]) == []


def test_unpunched_span_counts_only_once_ended() -> None:
    morning = [punch(TUE, "08:00-08:45")]
    # 17:00: the afternoon span is still running, nothing pending yet.
    assert shortfalls(WORKER, [], morning, datetime(2026, 10, 13, 17, 0)) == []
    # 18:00: it has ended unpunched.
    assert shortfalls(WORKER, [], morning, datetime(2026, 10, 13, 18, 0)) == [
        Shortfall(day=TUE, span=span("15:45-18:00"))
    ]


def test_events_clear_shortfalls() -> None:
    morning = [punch(TUE, "08:00-08:45")]
    now = datetime(2026, 10, 13, 20, 0)
    for kind in (EventKind.MORA, EventKind.ASSENZA, EventKind.PERMESSO):
        assert shortfalls(WORKER, [event(TUE, kind, "15:45-18:00")], morning, now) == []
    # Wednesday on ferie, nothing punched that day: no shortfall.
    wed = date(2026, 10, 14)
    later = datetime(2026, 10, 14, 20, 0)
    assert shortfalls(WORKER, [event(wed, EventKind.FERIE, None)], morning, later) == [
        Shortfall(day=TUE, span=span("15:45-18:00"))  # Tuesday afternoon still pending
    ]


def test_days_before_the_first_punch_are_never_pending() -> None:
    # First punch Wednesday 14/10: Monday and Tuesday (unpunched) are not pending.
    wed = date(2026, 10, 14)
    punches = [punch(wed, "08:00-08:45"), punch(wed, "15:45-18:00")]
    assert shortfalls(WORKER, [], punches, datetime(2026, 10, 14, 20, 0)) == []
    assert shortfalls(WORKER, [], [], datetime(2026, 10, 14, 20, 0)) == []


def test_open_punch_covers_the_rest_of_the_day() -> None:
    p = Punch(id=0, day=TUE, start=span("15:45-18:00").start, end=None)
    morning = punch(TUE, "08:00-08:45")
    assert shortfalls(WORKER, [], [morning, p], datetime(2026, 10, 13, 18, 30)) == []


def test_check_punches_refuses_overlaps_and_two_open() -> None:
    with pytest.raises(ValueError, match="sovrapposte"):
        check_punches([punch(TUE, "08:00-09:00"), punch(TUE, "08:30-10:00")])
    with pytest.raises(ValueError, match="aperta"):
        check_punches(
            [Punch(id=1, day=TUE, start=480, end=None), Punch(id=2, day=TUE, start=960, end=None)]
        )
    check_punches([punch(TUE, "08:00-08:45"), punch(TUE, "08:45-09:00")])  # touching is fine
```

- [ ] **Step 2: Run, verify fail** — `scripts/test.sh tests/test_timbratura.py`.

- [ ] **Step 3: Implement**

`hours.py`, public helper and its use in `_day_work` (replaces the inline loop that builds
`scheduled`):

```python
def scheduled_minutes(contract: Contract, day: date, is_festivita: bool) -> set[int]:
    """Minutes the schedule foresees on day: none on a festività or outside the employment."""
    minutes: set[int] = set()
    if contract.employed_on(day) and not is_festivita:
        for s in contract.orario[day.weekday()]:
            minutes |= s.minutes()
    return minutes
```

```python
    scheduled = scheduled_minutes(contract, day, is_festivita)
```

`src/tata/timbratura.py`:

```python
"""Cartellino: what the punches imply for payroll, and what the button proposes."""

from datetime import date, datetime, timedelta

from tata.festivita import festivita
from tata.hours import scheduled_minutes
from tata.models import MINUTES_PER_DAY, Contract, Event, EventKind, Frozen, Punch, Span

TOLERANCE = 5  # minutes per gap or overrun ignored: nobody gets docked for parking
WINDOW = 30  # minutes around a span's end in which the button proposes to confirm it
COVERING = frozenset({EventKind.ASSENZA, EventKind.PERMESSO, EventKind.MORA})


class Shortfall(Frozen):
    """Scheduled time with no punch and no event: the employer must say what it was."""

    day: date
    span: Span


def check_punches(punches: list[Punch]) -> None:
    """Raise ValueError if more than one punch is open or two punches of a day overlap."""
    if len([p for p in punches if p.end is None]) > 1:
        raise ValueError("c'è già una timbratura aperta")
    by_day: dict[date, list[Punch]] = {}
    for p in punches:
        by_day.setdefault(p.day, []).append(p)
    for day, day_punches in by_day.items():
        ordered = sorted(day_punches, key=lambda p: p.start)
        for a, b in zip(ordered, ordered[1:], strict=False):
            if a.end is None or a.end > b.start:
                raise ValueError(f"timbrature sovrapposte il {day}")


def overruns(contract: Contract, punch: Punch) -> list[Span]:
    """Punched minutes outside the schedule: the EXTRA events a closed punch implies."""
    holiday = punch.day in _holidays(contract, punch.day.year)
    return _runs(punch.span().minutes() - scheduled_minutes(contract, punch.day, holiday))


def shortfalls(
    contract: Contract, events: list[Event], punches: list[Punch], now: datetime
) -> list[Shortfall]:
    """Ended scheduled time no punch and no event explains, from the first punch ever to now.

    Derived, never stored: an ASSENZA, PERMESSO or MORA over the gap, or any whole-day event,
    is what clears it. An open punch covers the rest of its day.
    """
    if not punches:
        return []
    today, minute = now.date(), now.hour * 60 + now.minute
    result: list[Shortfall] = []
    day = min(p.day for p in punches)
    while day <= today:
        ended: set[int] = set()
        for s in contract.orario[day.weekday()]:
            if day < today or s.end <= minute:
                ended |= s.minutes()
        holiday = day in _holidays(contract, day.year)
        missing = scheduled_minutes(contract, day, holiday) & ended
        for ev in events:
            if ev.day == day and ev.span is None:
                missing = set()
            elif ev.day == day and ev.span is not None and ev.kind in COVERING:
                missing -= ev.span.minutes()
        for p in punches:
            if p.day == day:
                missing -= set(range(p.start, MINUTES_PER_DAY if p.end is None else p.end))
        result += [Shortfall(day=day, span=s) for s in _runs(missing)]
        day += timedelta(days=1)
    return result


def _holidays(contract: Contract, year: int) -> frozenset[date]:
    return festivita(year, contract.patrono_mese, contract.patrono_giorno)


def _runs(minutes: set[int]) -> list[Span]:
    """Contiguous runs of minutes longer than TOLERANCE, as spans."""
    ordered = sorted(minutes)
    result: list[Span] = []
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        if j - i + 1 > TOLERANCE:
            result.append(Span(start=ordered[i], end=ordered[j] + 1))
        i = j + 1
    return result
```

`MINUTES_PER_DAY` is a module constant of `models.py`. Add `"timbratura"` to the `pure` set in
`tests/test_architecture.py::test_calculators_do_no_io`.

- [ ] **Step 4: Run, verify pass** — `scripts/format.sh && scripts/check.sh` (also proves the
`_day_work` refactor changed nothing: the whole payslip suite still passes).

- [ ] **Step 5: Commit** — "Derive overruns and pending shortfalls from cartellino punches".

---

### Task 4: `propose` — the button's choice

**Files:**
- Modify: `src/tata/timbratura.py`
- Test: `tests/test_timbratura.py`

**Interfaces:**
- Produces:
  - `class Action(StrEnum)`: `INIZIO = "inizio"`, `FINE = "fine"`, `CONFERMA = "conferma"`
  - `class Proposal(Frozen)`: `action: Action`, `span: Span | None` (set only for CONFERMA)
  - `propose(contract: Contract, punches: list[Punch], now: datetime) -> Proposal`

Rules (spec §3): an open punch anywhere → FINE. Else, for today's scheduled spans (not on a
festività, only while employed): the span is unrecorded (no punch of today overlaps it) and
`max(end - WINDOW, (start + end) // 2) <= minute <= end + WINDOW` → CONFERMA that span. The
midpoint bound keeps short spans from proposing "confirm" while they have barely started.
Otherwise INIZIO.

- [ ] **Step 1: Failing tests** (append to `tests/test_timbratura.py`)

```python
from tata.timbratura import Action, Proposal, propose

AFTERNOON = span("15:45-18:00")


def at(hh: int, mm: int) -> datetime:
    return datetime(2026, 10, 13, hh, mm)


def test_open_punch_always_proposes_fine() -> None:
    yesterday = Punch(id=1, day=date(2026, 10, 12), start=945, end=None)
    assert propose(WORKER, [yesterday], at(3, 0)) == Proposal(action=Action.FINE, span=None)


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
    assert propose(WORKER, [], at(hh, mm)) == expected


def test_recorded_span_is_not_proposed_again() -> None:
    done = punch(TUE, "15:50-18:00")
    assert propose(WORKER, [done], at(18, 5)) == Proposal(action=Action.INIZIO, span=None)


def test_festivita_proposes_inizio() -> None:
    immacolata = datetime(2026, 12, 8, 17, 50)
    assert propose(WORKER, [], immacolata) == Proposal(action=Action.INIZIO, span=None)
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** (in `timbratura.py`; `from enum import StrEnum`)

```python
class Action(StrEnum):
    INIZIO = "inizio"
    FINE = "fine"
    CONFERMA = "conferma"


class Proposal(Frozen):
    action: Action
    span: Span | None  # the scheduled span to confirm (CONFERMA only)


def propose(contract: Contract, punches: list[Punch], now: datetime) -> Proposal:
    """What the big button offers at `now` (spec §3)."""
    if any(p.end is None for p in punches):
        return Proposal(action=Action.FINE, span=None)
    today, minute = now.date(), now.hour * 60 + now.minute
    if contract.employed_on(today) and today not in _holidays(contract, today.year):
        recorded: set[int] = set()
        for p in punches:
            if p.day == today:
                recorded |= p.span().minutes()
        for s in contract.orario[today.weekday()]:
            near_end = max(s.end - WINDOW, (s.start + s.end) // 2) <= minute <= s.end + WINDOW
            if near_end and not s.minutes() & recorded:
                return Proposal(action=Action.CONFERMA, span=s)
    return Proposal(action=Action.INIZIO, span=None)
```

- [ ] **Step 4: Run, verify pass** — `scripts/format.sh && scripts/check.sh`.

- [ ] **Step 5: Commit** — "Propose the button action from the schedule and the open punch".

---

### Task 5: The clock carries the time of day

**Files:**
- Modify: `src/tata/web.py` (`Clock`, `rome_today` → `rome_now`, `create_app` param, 3 uses)
- Modify: `tests/test_web.py` (`end_of_2026`)

**Interfaces:**
- Produces: `Clock = Callable[[], datetime]`; `create_app(settings: Settings, now: Clock)`;
  `rome_now() -> datetime` (aware, Europe/Rome).

- [ ] **Step 1: Change it**

```python
Clock = Callable[[], datetime]


def rome_now() -> datetime:
    return datetime.now(ROME)
```

`app_from_env` passes `rome_now`; `create_app(settings: Settings, now: Clock)`; every
`today()` becomes `now().date()` (the `home`, `this_year` and year-page uses; grep `today()`).

`tests/test_web.py`:

```python
def end_of_2026() -> datetime:
    return datetime(2026, 12, 31, 12, 0)
```

(import `datetime` alongside `date`).

- [ ] **Step 2: Run** — `scripts/format.sh && scripts/check.sh`: all existing tests pass unchanged
otherwise. This is a pure refactor; no new test.

- [ ] **Step 3: Commit** — "Give the web clock the time of day, needed by the punch button".

---

### Task 6: `/timbra` page, punch routes, cartellino on the month page

**Files:**
- Modify: `src/tata/web.py`
- Create: `src/tata/templates/timbra.html`
- Modify: `src/tata/templates/mese.html`, `src/tata/templates/base.html` (nav link),
  `src/tata/static/style.css`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces routes:
  - `GET /timbra` — the button page.
  - `POST /timbra/inizio` — opens a punch at `now`.
  - `POST /timbra/fine` — closes the open punch; form field `alle` (`""` = now, only allowed when
    the open punch is today's; else `HH:MM`, `24:00` allowed).
  - `POST /timbrature` — a closed punch: fields `giorno`, `inizio`, `fine` (used by the CONFERMA
    button and by the manual form on the month page). Redirects to `/timbra` if `giorno` is today,
    else to the month page.
  - `POST /timbrature/{id}/elimina` → month page.
  - `POST /mese/{y}/{m}/finalizza` refuses while the month has shortfalls.

- [ ] **Step 1: Failing tests** (append to `tests/test_web.py`)

```python
class FakeClock:
    """A settable clock: punch tests move time forward between taps."""

    def __init__(self, start: datetime) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value


def punching(tmp_path: Path, start: datetime) -> tuple[TestClient, FakeClock]:
    clock = FakeClock(start)
    c = TestClient(create_app(settings(tmp_path), clock), headers=basic(PASSWORD),
                   follow_redirects=False)
    assert c.post("/contratto", data=CONTRACT_FORM).status_code == 303
    return c, clock


def test_button_start_then_stop_with_overrun(tmp_path: Path) -> None:
    # Tue 13/10, schedule 15:45-18:00. In at 15:44 (1 min early, tolerated), out at 18:40.
    c, clock = punching(tmp_path, datetime(2026, 10, 13, 15, 44))
    assert "Inizio turno" in c.get("/timbra").text
    assert c.post("/timbra/inizio").headers["location"] == "/timbra"
    clock.value = datetime(2026, 10, 13, 18, 40)
    assert "Fine turno" in c.get("/timbra").text
    assert c.post("/timbra/fine", data={"alle": ""}).status_code == 303
    page = c.get("/mese/2026/10").text
    assert "15:44-18:40" in page  # cartellino
    assert "extra 18:00-18:40" in page  # generated event


def test_confirm_records_the_scheduled_span(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, datetime(2026, 10, 13, 18, 10))
    page = c.get("/timbra").text
    assert "Conferma turno 15:45-18:00" in page
    r = c.post("/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    assert r.headers["location"] == "/timbra"
    assert "Inizio turno" in c.get("/timbra").text  # span recorded, not proposed again


def test_shortfall_is_proposed_and_blocks_finalize_until_confirmed(tmp_path: Path) -> None:
    # Tue 13/10 punched only in the afternoon; the 08:00-08:45 span is pending.
    c, _ = punching(tmp_path, datetime(2026, 10, 13, 18, 30))
    c.post("/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    page = c.get("/mese/2026/10").text
    assert "Da confermare" in page and "08:00-08:45" in page
    r = c.post("/mese/2026/10/finalizza")
    assert r.status_code == 400 and "da confermare" in r.text
    c.post("/eventi", data={"kind": "mora", "dal": "2026-10-13", "al": "", "inizio": "08:00",
                            "fine": "08:45", "note": ""})
    assert "Da confermare" not in c.get("/mese/2026/10").text


def test_stale_open_punch_asks_for_the_exit_time(tmp_path: Path) -> None:
    c, clock = punching(tmp_path, datetime(2026, 10, 13, 15, 45))
    c.post("/timbra/inizio")
    clock.value = datetime(2026, 10, 14, 7, 55)
    page = c.get("/timbra").text
    assert 'name="alle"' in page and "13/10/2026" in page
    r = c.post("/timbra/fine", data={"alle": ""})
    assert r.status_code == 400 and "ora di uscita" in r.text
    assert c.post("/timbra/fine", data={"alle": "18:00"}).status_code == 303


def test_stop_in_the_same_minute_is_refused(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, datetime(2026, 10, 13, 15, 45))
    c.post("/timbra/inizio")
    r = c.post("/timbra/fine", data={"alle": ""})
    assert r.status_code == 400
    assert "Fine turno" in c.get("/timbra").text  # still open, nothing stored


def test_shortfalls_of_a_finalized_month_are_ignored(tmp_path: Path) -> None:
    # October finalized on 20/10; later October spans must not haunt the month page.
    c, clock = punching(tmp_path, datetime(2026, 10, 20, 18, 30))
    c.post("/timbrature", data={"giorno": "2026-10-20", "inizio": "15:45", "fine": "18:00"})
    c.post("/eventi", data={"kind": "mora", "dal": "2026-10-20", "al": "", "inizio": "08:00",
                            "fine": "08:45", "note": ""})
    assert c.post("/mese/2026/10/finalizza").status_code == 303
    clock.value = datetime(2026, 11, 3, 9, 0)
    assert "Da confermare" not in c.get("/mese/2026/10").text
    assert "Da confermare" in c.get("/mese/2026/11").text  # 2/11 unpunched, 3/11 morning


def test_deleting_a_punch_removes_its_extra(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, datetime(2026, 10, 13, 20, 0))
    c.post("/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "19:00"})
    page = c.get("/mese/2026/10").text
    punch_id = page.split('action="/timbrature/')[1].split("/")[0]
    assert c.post(f"/timbrature/{punch_id}/elimina").status_code == 303
    assert "extra 18:00-19:00" not in c.get("/mese/2026/10").text
```

Note: hire date in `CONTRACT_FORM` is Mon 12/10/2026; ruff format will reflow the dict literals.

- [ ] **Step 2: Run, verify fail** — `scripts/test.sh tests/test_web.py`.

- [ ] **Step 3: Implement — web.py**

Imports: `from tata.timbratura import Action, check_punches, overruns, propose, shortfalls`,
`from tata.models import Punch`.

Inside `create_app`, helpers next to `check_events`:

```python
    def minute_of(moment: datetime) -> int:
        return moment.hour * 60 + moment.minute

    def pending(contract: Contract, year: int, month: int) -> list[Shortfall]:
        """Shortfalls of an open month; a finalized month has nothing left to confirm."""
        if store.final_payslip(year, month) is not None:
            return []
        return [
            s
            for s in shortfalls(contract, store.events(), store.punches(), now())
            if (s.day.year, s.day.month) == (year, month)
        ]

    def save_punch(contract: Contract, draft: Punch) -> None:
        """Validate a closed punch and its EXTRA against everything stored, then write it."""
        if not contract.employed_on(draft.day):
            raise ValueError(f"il {draft.day} è fuori dal rapporto di lavoro")
        others = [p for p in store.punches() if p.id != draft.id]
        check_punches([*others, draft])
        extras = overruns(contract, draft)
        drafts = [Event(id=0, day=draft.day, kind=EventKind.EXTRA, span=s, note="") for s in extras]
        check_events(contract, store.events() + drafts)
        if draft.id == 0:
            store.add_punch(draft.day, draft.start, draft.end, extras)
        else:
            if draft.end is None:
                raise ValueError("ora di uscita mancante")
            store.close_punch(draft.id, draft.end, extras)
```

(Import `Shortfall` too.)

Routes:

```python
    # ---- cartellino

    @app.get("/timbra", response_class=HTMLResponse)
    def punch_page(request: Request) -> HTMLResponse:
        contract = require_contract()
        punches = store.punches()
        moment = now()
        open_ = next((p for p in punches if p.end is None), None)
        return templates.TemplateResponse(
            request,
            "timbra.html",
            {
                "proposal": propose(contract, punches, moment),
                "open": open_,
                "stale": open_ is not None and open_.day != moment.date(),
                "today": moment.date(),
                "actions": Action,
            },
        )

    @app.post("/timbra/inizio")
    def punch_in() -> RedirectResponse:
        contract = require_contract()
        moment = now()
        draft = Punch(id=0, day=moment.date(), start=minute_of(moment), end=None)
        if not contract.employed_on(draft.day):
            raise ValueError(f"il {draft.day} è fuori dal rapporto di lavoro")
        check_punches([*store.punches(), draft])
        store.add_punch(draft.day, draft.start, None, [])
        return RedirectResponse("/timbra", status_code=303)

    @app.post("/timbra/fine")
    async def punch_out(request: Request) -> RedirectResponse:
        contract = require_contract()
        form = await request.form()
        alle, moment = _field(form, "alle"), now()
        open_ = next((p for p in store.punches() if p.end is None), None)
        if open_ is None:
            raise ValueError("nessuna timbratura aperta")
        if alle:
            end = Span.parse(f"00:00-{alle}").end
        elif open_.day == moment.date():
            end = minute_of(moment)
        else:
            raise ValueError(f"indica l'ora di uscita del {open_.day:%d/%m/%Y}")
        # Built explicitly, not model_copy (which skips validation): a same-minute exit must
        # raise the Span error (400) before anything is written.
        save_punch(contract, Punch(id=open_.id, day=open_.day, start=open_.start, end=end))
        return RedirectResponse("/timbra", status_code=303)
```

```python
    @app.post("/timbrature")
    async def add_punch(request: Request) -> RedirectResponse:
        contract = require_contract()
        form = await request.form()
        day = date.fromisoformat(_field(form, "giorno"))
        s = Span.parse(f"{_field(form, 'inizio')}-{_field(form, 'fine')}")
        save_punch(contract, Punch(id=0, day=day, start=s.start, end=s.end))
        if day == now().date():
            return RedirectResponse("/timbra", status_code=303)
        return RedirectResponse(f"/mese/{day.year}/{day.month}", status_code=303)

    @app.post("/timbrature/{punch_id}/elimina")
    def delete_punch(punch_id: int) -> RedirectResponse:
        day = store.delete_punch(punch_id)
        return RedirectResponse(f"/mese/{day.year}/{day.month}", status_code=303)
```

`finalize`: before `store.finalize(...)`:

```python
        if pending(contract, year, month):
            raise ValueError(f"ci sono mancanze da confermare in {MESI[month]} {year}")
```

`month_page` context: add
`"punches": [p for p in store.punches() if (p.day.year, p.day.month) == (year, month)]`,
`"pending": pending(contract, year, month)`.

- [ ] **Step 4: Implement — templates**

`templates/timbra.html`:

```html
{% extends "base.html" %}
{% block title %}timbra · tata{% endblock %}
{% block content %}
<section class="punch">
{% if stale %}
  <form method="post" action="/timbra/fine" class="card form">
    <h2>Turno del {{ open.day | dmy }} ancora aperto</h2>
    <label>Uscita alle <input name="alle" required placeholder="hh:mm" pattern="\d{1,2}:\d{2}"></label>
    <button class="big">Chiudi turno</button>
  </form>
{% elif proposal.action == actions.FINE %}
  <form method="post" action="/timbra/fine">
    <input type="hidden" name="alle" value="">
    <button class="big">Fine turno</button>
  </form>
  <p class="hint">In servizio dalle {{ open.start | ore }}</p>
{% elif proposal.action == actions.CONFERMA %}
  <form method="post" action="/timbrature">
    <input type="hidden" name="giorno" value="{{ today.isoformat() }}">
    <input type="hidden" name="inizio" value="{{ proposal.span.start | ore }}">
    <input type="hidden" name="fine" value="{{ proposal.span.end | ore }}">
    <button class="big">Conferma turno {{ proposal.span }}</button>
  </form>
  <form method="post" action="/timbra/inizio"><button class="link">oppure: inizio turno adesso</button></form>
{% else %}
  <form method="post" action="/timbra/inizio"><button class="big">Inizio turno</button></form>
{% endif %}
</section>
{% endblock %}
```

Note: the `ore` filter renders `8:00` (no leading zero); `Span.parse` accepts it. `Span.__str__`
renders `08:00-08:45`, which is what the tests look for.

`mese.html`, after the "Eccezioni del mese" section:

```html
{% if pending %}
<section class="card error">
  <h2>Da confermare</h2>
  <ul class="events">
  {% for s in pending %}
    <li>
      <span>{{ s.day | dmy }} · {{ s.span }} non timbrato</span>
      <span class="actions">
      {% for kind, label in [("assenza", "assente"), ("permesso", "permesso"), ("mora", "mandata a casa")] %}
        <form method="post" action="/eventi">
          <input type="hidden" name="kind" value="{{ kind }}">
          <input type="hidden" name="dal" value="{{ s.day.isoformat() }}">
          <input type="hidden" name="al" value="">
          <input type="hidden" name="inizio" value="{{ s.span.start | ore }}">
          <input type="hidden" name="fine" value="{{ s.span.end | ore }}">
          <input type="hidden" name="note" value="">
          <button class="small">{{ label }}</button>
        </form>
      {% endfor %}
      </span>
    </li>
  {% endfor %}
  </ul>
</section>
{% endif %}

<section class="card">
  <h2>Cartellino</h2>
  {% if punches %}
  <ul class="events">
  {% for p in punches %}
    <li>
      <span>{{ p.day | dmy }} · {% if p.end is none %}dalle {{ p.start | ore }} (aperta){% else %}{{ p.span() }}{% endif %}</span>
      {% if not final %}
      <form method="post" action="/timbrature/{{ p.id }}/elimina"><button class="link">elimina</button></form>
      {% endif %}
    </li>
  {% endfor %}
  </ul>
  {% endif %}
  {% if not final %}
  <form method="post" action="/timbrature" class="form inline">
    <input type="date" name="giorno" required value="{{ selected }}">
    <input name="inizio" required placeholder="entrata hh:mm" pattern="\d{1,2}:\d{2}">
    <input name="fine" required placeholder="uscita hh:mm" pattern="\d{1,2}:\d{2}">
    <button>Aggiungi timbratura</button>
  </form>
  {% endif %}
</section>
```

The `Span` in the "Eccezioni del mese" list already prints as `extra 18:00-18:40` via
`{{ e.kind }} {{ e.span }}`, which `test_button_start_then_stop_with_overrun` relies on.

`base.html` nav: add `<a href="/timbra">timbra</a>` first.

`style.css`:

```css
.punch { display: flex; flex-direction: column; align-items: center; gap: 1rem; padding-top: 12vh; }
.punch form { width: 100%; max-width: 24rem; margin: 0; }
button.big { width: 100%; min-height: 40vh; font-size: 2rem; font-weight: 700; border-radius: 1rem; }
button.small { padding: .25rem .6rem; font-size: .85rem; }
```

- [ ] **Step 5: Run, verify pass** — `scripts/format.sh && scripts/check.sh`. Check the suite
stays under 10 s.

- [ ] **Step 6: Commit** — "Add the punch button page and the cartellino on the month page".

---

### Task 7: Installable PWA

**Files:**
- Create: `src/tata/static/manifest.webmanifest`, `src/tata/static/logo-512.png`
- Modify: `src/tata/templates/base.html`
- Modify (only if Step 2 shows it is needed): `src/tata/web.py` (media type registration)
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: manifest at `/static/manifest.webmanifest`, `start_url` `/timbra`.

- [ ] **Step 1: Failing test**

```python
def test_manifest_is_public_and_linked(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), end_of_2026)
    # Browsers fetch the manifest and icons without credentials: /static must stay public.
    r = TestClient(app).get("/static/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    manifest = r.json()
    assert manifest["start_url"] == "/timbra"
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert TestClient(app).get(icon["src"]).status_code == 200
    page = hired(tmp_path).get("/timbra").text
    assert 'rel="manifest" href="/static/manifest.webmanifest"' in page
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

Icon (host ImageMagick, from the 512 px art already in the repo):

```bash
convert docs/whatsapp.jpg -resize 512x512 -strip src/tata/static/logo-512.png
```

`static/manifest.webmanifest`:

```json
{
  "name": "tata",
  "short_name": "tata",
  "start_url": "/timbra",
  "scope": "/",
  "display": "standalone",
  "background_color": "#f7f6f3",
  "theme_color": "#2f6f5e",
  "icons": [
    {"src": "/static/logo.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/static/logo-512.png", "sizes": "512x512", "type": "image/png"}
  ]
}
```

`base.html` head, after the icon links:

```html
<link rel="manifest" href="/static/manifest.webmanifest">
<meta name="theme-color" content="#2f6f5e">
```

If the content-type assertion fails (Python's `mimetypes` may not know `.webmanifest`), register
it at the top of `web.py`, after the imports:

```python
mimetypes.add_type("application/manifest+json", ".webmanifest")  # PWA manifest, unknown to stdlib
```

No service worker (spec §5).

- [ ] **Step 4: Run, verify pass** — `scripts/format.sh && scripts/check.sh`;
`tests/test_repo_hygiene.py` must still pass with the new PNG (it only rejects data, PDF and
sqlite files).

- [ ] **Step 5: Commit** — "Make tata installable as a PWA opening on the punch button".

---

### Task 8: Docs and device check

**Files:**
- Modify: `README.md` (feature section: the button, cartellino, Da confermare, MORA; install on
  the phone; the device prerequisites from spec §5: step-ca root trusted on the phone, LAN/VPN)
- Modify: `CLAUDE.md` (Architecture: add `timbratura.py`; Key decisions: one line "Punches are the
  cartellino: overruns → EXTRA automatically; shortfalls proposed, confirmed by the employer
  (ASSENZA / PERMESSO / MORA); derived from the first punch on")
- Modify: `docs/superpowers/specs/2026-10-08-timbratura-pwa-design.md` (Status: implemented)

- [ ] **Step 1: Write the docs** (Italian in README, English in CLAUDE.md, neutral register).
- [ ] **Step 2:** `scripts/check.sh` green.
- [ ] **Step 3: Commit** — "Document the punch button and the cartellino".
- [ ] **Step 4: Deploy and device check (with the user):** `docker compose up -d --build`, then
  on the phone: open https://tata.bad.ass/timbra, add to home screen, reopen from the icon, verify
  it opens standalone and HTTP Basic does not re-prompt on every launch. Report the outcome;
  if Basic re-prompts in standalone mode, STOP and raise it (spec §5: cookie fallback is a
  separate decision).
