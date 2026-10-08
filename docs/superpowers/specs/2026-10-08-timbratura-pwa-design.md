# Timbratura: one-button PWA — design

Status: awaiting review.

## 1. Goal

The employer opens tata from the phone home screen and gets ONE big button that proposes the
right action for the current moment: start the shift, end it, or confirm it. Success: on a
normal day, recording the day costs two taps and zero typing; on an abnormal day, the
difference from the schedule reaches the payslip without the employer rebuilding it by hand.

Who taps: the employer, on their own phone. The worker never uses the app.

## 2. Domain constraints (challenge the spec)

- The payroll model is "standard schedule + exceptions" (CLAUDE.md). Punches are raw facts;
  they matter to payroll only through the exceptions they imply. A punch log must never
  become a second source of hours next to the schedule.
- A shortfall is not automatically unpaid. Absence by the worker's choice is unpaid
  (`ASSENZA`) or paid leave (`PERMESSO`, art. 19 CCNL); the worker sent home or told to come
  later by the employer is paid in full (mora credendi, art. 1206 ss. c.c.). Only the
  employer knows which: shortfalls always need an explicit choice.
- Excess time IS determinable: minutes worked outside the scheduled spans are `EXTRA`, and
  the existing calculators already classify them (straordinario, notturno, festivo).
- A day has several scheduled spans (e.g. morning and afternoon). "Near the start" and
  "near the end" are per span.

## 3. The button (pure function)

`propose(contract, open_punch, now) -> Proposal` in a new calc module `timbratura.py`, no I/O.

| State | Proposal |
|---|---|
| A punch is open (any time, any day) | **Fine turno** (closes it at `now`) |
| No open punch, `now` within `WINDOW` of a scheduled span's start | **Inizio turno** |
| No open punch, `now` within `WINDOW` of a scheduled span's end, span not yet recorded | **Conferma turno HH:MM–HH:MM** (records the scheduled span as worked: the "forgot to start" case) |
| Anything else | **Inizio turno** |

`WINDOW` = 30 minutes, a named constant. "Near the end" also requires being past the span's
midpoint, so a 45-minute span does not propose "confirm" a quarter of an hour in. "Near the start"
needs no rule of its own: without an open punch, Inizio is already the default. A secondary small link always offers the other
action, so a wrong guess never blocks the user.

An open punch from a previous day is NOT auto-closed: the page asks for its end time
(forgotten stop). A punch never crosses midnight; one longer than 8 h is accepted but
highlighted on the month page.

## 4. Data

New table `punch(id, day, start, end NULL)` in minutes from midnight, like `Span`. At most one
open punch (enforced in `Store`). Writes to a finalized month are refused, same rule as events.

Closing a punch runs the reconciliation (§7) against the scheduled spans of that day.
Tolerance: differences of ≤ `TOLERANCE` = 5 minutes per span edge are ignored (nobody gets
docked for parking).

## 5. PWA

- `/timbra`: the button page. Server-rendered, the proposal computed server-side; the button is
  a `<form method=post>`. No JavaScript, consistent with the rest of the app.
- `static/manifest.webmanifest` (`display: standalone`, `start_url: /timbra`, icons from the
  existing logo at 192 and 512 px), linked from `base.html`. `/static` is outside HTTP Basic (a
  mounted app does not inherit the auth dependency), so browsers fetch manifest and icons without
  credentials; nothing personal lives there.
- No service worker: a punch without the server is meaningless, and offline caching of payroll
  pages is a liability. If the target browser refuses to install without one, add a no-op
  worker; verify on the actual phone first.
- The injected `Clock` becomes a `datetime` clock (Europe/Rome); `today` derives from it.

### Risks to check on the device before building the rest

- The phone must trust the step-ca root, or installation and HTTP Basic in standalone mode fail.
- HTTP Basic in an installed PWA: iOS standalone mode historically re-prompts or drops the
  credentials. If it does, a long-lived cookie set after the first Basic login is the fallback
  (separate decision, not in scope yet).
- tata is LAN-only. Punching from outside the house needs the VPN; the button page says so on
  connection failure rather than pretending.

## 6. Testing

- `propose` table-driven over a realistic two-span schedule with invented times: each row of
  §3, window edges (exactly 30 min = inside), open punch from yesterday.
- Reconciliation: overrun → `EXTRA` with the exact span; within tolerance → nothing; shortfall
  → pending item, never an event until confirmed; `MORA` / `ASSENZA` / `PERMESSO` clear it;
  span covered by ferie or festività → no pending; days before the first punch → no pending.
- `MORA` leaves the payslip identical to a day without events (golden value by hand).
- Deleting a punch removes its `EXTRA`; finalize refused while anything is pending.
- Web: POST start/stop through the app with a fixed clock; finalized month refused.
- Manifest served and linked; `/timbra` behind auth like everything else.

## 7. Decided (2026-10-08): punches are the cartellino AND drive the payslip

Punches are the attendance register (ingresso/uscita, listed per day on the month page) and
feed payroll. Closing a punch compares it with the scheduled spans:
- minutes worked outside the schedule (beyond `TOLERANCE`) become `EXTRA` events at once,
  validated by `check_events` like any other event, deletable like any other event;
- each shortfall becomes a pending item on the month page with three buttons: **assenza**
  (unpaid), **permesso** (paid leave), **mandata a casa** (paid, no event). A month with pending
  items cannot be finalized. The system proposes, the employer confirms: a short punch becomes
  `ASSENZA` only when the worker chose not to work; when the employer sent her home, the time
  stays paid (mora credendi) and no event is written.

The calculators stay untouched (they still read only contract + events): the punch is data
entry, not a parallel ledger. Rejected alternative: register only, with events entered by
hand, which leaves the employer doing the work the button exists to remove.

A scheduled span with no punch at all is also a shortfall (whole span), proposed the same way
once the span has ended.

Pending shortfalls are derived, never stored: scheduled minutes of ended spans, from the day
of the first punch ever recorded, not covered by a punch (± `TOLERANCE`), by an event
(`FERIE`, `MALATTIA`, `PERMESSO`, `ASSENZA`) or by a festività. So "mandata a casa" must leave
a fact behind, or the shortfall comes back: a new `EventKind.MORA` (time span, paid, no effect
on the hours; shown on the month page so the paid-but-not-worked time is visible).

Generated events carry the `punch_id` that produced them (new nullable column on `event`).
Deleting a punch deletes its `EXTRA` events in the same transaction; a closed punch is not
edited, it is deleted and re-entered.
