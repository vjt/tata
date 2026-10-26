# tata — Project Memory

## What this is

Self-hosted helper for an Italian household employer with one or more domestic workers (CCNL
lavoro domestico, paid by the hour, non conviventi): one **rapporto** per worker and hire, open
or closed, possibly overlapping. Records the exceptions to a standard weekly
schedule, produces the monthly prospetto paga (PDF), quarterly INPS + Cassa Colf contributions,
tredicesima, TFR, ferie/permessi balances, the yearly attestazione for the worker's 730 and the
employer's deductible-contributions YAML.

The domestic employer is NOT a sostituto d'imposta: no IRPEF withholding, no CU, no 770.

## Architecture

```
src/tata/
  models.py     Pydantic domain types + StrEnums (closed sets)
  rates.py      rates/<year>.yaml loader + schema (CCNL minimums, INPS bands, Cassa Colf)
  festivita.py  festività: fixed national days, Easter Monday, patrono
  hours.py      a day's minutes -> buckets (ordinario, notturno, straordinario, festivo, ...)
  payslip.py    one month -> Payslip (lines, lordo, trattenute, netto, ratei)
  inps.py       fascia, quarterly contributions, due dates
  annual.py     tredicesima, TFR fund, ferie/permessi balances, deduction export
  timbratura.py punch button: propose action, overruns (EXTRA), shortfalls, punch checks
  presenza.py   wifi presence (eve via Home Assistant history) -> proposed punches
  homeassistant.py  HA REST client: a device tracker's history (the only network I/O)
  store.py      SQLite persistence (stdlib sqlite3, no ORM): Store (rapporti) -> RapportoStore
  pdf.py        HTML -> PDF via WeasyPrint
  web.py        FastAPI app factory, server-rendered Jinja2, login page + session cookie,
                routes under /r/{id}/
  static/       PWA manifest + icons (public, outside the login), no service worker
rates/<year>.yaml  one file per year, every value with its source in a comment
```

Data flow: store (contract, schedule, events) → calc modules (pure functions, no I/O) → web/pdf.
Calc modules never touch the DB; the store never computes.

## Key decisions

- **Code public, data NEVER.** Names, codice fiscale, hours, payslips live in `TATA_DATA_DIR`
  (a bind mount outside the repo). `tests/test_repo_hygiene.py` fails if a data/PDF/sqlite file is
  tracked. Fixtures use invented people (Mascetti, Perozzi, Necchi, Melandri, Sassaroli).
  The real household's details (comune, hire date, pay) are runtime data, never in code or docs.
- **Decimal everywhere, minutes as int.** Time is integer minutes until the last step; money is
  `Decimal` rounded half-up to the cent once per payslip line. Never `float` (AST-enforced).
- **Rates are data, not code.** Anything that changes in January lives in `rates/<year>.yaml`.
  A missing year file is a loud failure, never a silent reuse of last year.
- **Derive, don't store.** Balances (ferie, permessi, TFR, 13ma) are recomputed from the events
  since hire. The only snapshots are finalized payslips (immutable once final) and recorded
  INPS payments (cash-basis facts for the deduction).
- **Hourly worker rules (CCNL 28/10/2025):** festività and ferie paid at 1/6 of weekly hours per
  day; festività paid whether or not they fall on a work day; maggiorazioni are per-minute
  classifications, highest wins (festivo/domenica 60% > straordinario notturno 50% > diurno 25%
  > 40–44h 10% > notturno ordinario 20%). Straordinario = beyond 8 h/day or 40 h/week,
  assigned to the chronologically LATEST minutes.
- **Punches are the cartellino.** Closing a punch writes EXTRA events for time outside the schedule
  (>5 min), linked to it (deleting the punch deletes them). Only punched time is paid: no
  punch, no pay. Scheduled time since the hire that no punch or event covers (shortfalls, days
  still to come) counts as unstored ASSENZA (`unpunched`). The month page also shows the
  planned payslip (schedule as if worked), for comparison only. A punch, or an unrecorded wifi
  proposal, overlapping a scheduled span takes its place: no shortfall there (the rest of the
  span stays unpaid). Other shortfalls are still confirmed one by one as
  ASSENZA / PERMESSO / MORA (sent home, paid as scheduled, art. 1206 c.c.): confirming is what
  pays PERMESSO and MORA. A month that hasn't ended, or has shortfalls or an open punch, cannot
  be finalized.
- **Rimborso trasferte per turno** (half-day with a closed punch, at most two a day): km per
  turno × ACI €/km, both from the env (`TATA_RIMBORSO_*`, runtime data). Trasferta fuori comune,
  not income (art. 51 c.5 TUIR): outside lordo, INPS, TFR, attestazione; netto stays lordo −
  trattenute, `da_pagare` = netto + rimborsi. Her car by default; a radio (auto propria / auto
  del datore) on the timbra button and on the punch form declares it per turno
  (`employer_car`): the employer's pays nothing. A turno's km are the configured ones unless the
  employer corrects them on the row's ✏️ form (`turno_km`, replacing, not adding).
- **Spese** (vjt 23/10): an expense she paid for the employer (day, importo, descrizione) is
  another rimborso of its month, outside every base, like the trasferte. Employer only.
- **Gestione** (contract, vjt 23/10 and 26/10): `netto`, the employer withholds her INPS +
  Cassa Colf share (CCNL default); `lordo`, he pays her the lordo and bears it (netto = lordo,
  shares still owed to INPS, the attestazione says so); `consulente`, payroll and contributions
  are a consultant's job: the payslip is worked minutes × paga oraria (CCNL minimum still
  checked) + rimborsi, and the pages hide INPS, ratei, TFR and the attestazione.
- **Balance**: wage payments (day, importo, nota) belong to no month; the month page shows
  owed since the hire (each month's `da_pagare`, today's as worked so far) − paid.
- **Wifi presence proposes, never records.** `/presenza` reads the worker's phone tracker from
  Home Assistant (eve: `not_home` comes 120 s after the last sighting) and proposes punches by
  shift: morning = first arrival 07:30–08:15 to the last departure before noon, + 30 min only if
  that is by 08:30; afternoon = 15:45 (or the earlier arrival) to the last departure. Moves in
  between are ignored; exceptions are not rules: a day that doesn't fit is flagged anomalous.
  Each is confirmed through the ordinary punch path; the month page lists those no punch
  overlaps yet (and survives HA being down). Restart blips (`unavailable`) are not
  departures; a stay with an unknown arrival or end is never proposed.
- **Validate on write.** Events and contract changes are checked against the calculators before
  they are stored; a stored inconsistency would brick every page that sums the year.
- **Months close in order.** Finalizing requires the previous employed month finalized. The
  contract has no history: changes recompute every open month.
- **TFR imponibile** = lordo + the unpaid share of sick days (art. 2120 c.3 c.c.). Ferie taken in
  advance are never deducted silently at termination.
- **INPS fascia** from retribuzione oraria effettiva = paga oraria × 13/12 (13ma rateo; no vitto/
  alloggio for non conviventi). Contributions on ore retribuite (worked + festività + ferie +
  permessi + scheduled hours of paid sick days). Out of scope by design: tempo determinato
  (contributo addizionale), conviventi (vitto/alloggio).
- **Rapporti** (spec 2026-10-15-rapporti): one employer, many rapporti. Everything is per
  rapporto (contract, events, punches, finals, INPS payments, TFR advances) except the
  deduction (art. 10 c.2 TUIR cap per employer: one export summing all). Never deleted: a
  closed rapporto stays readable. Login: the employer's `TATA_USER` + `TATA_PASSWORD`; per rapporto a worker
  login name (unique, identifies it) and a scrypt-hashed password. Schema version in `PRAGMA user_version`, migrations in
  `store._upgrade`, one transaction.

## Engineering principles (inherited from grappa / gastone / decaf, owned here)

- **Challenge the spec.** If domain knowledge (CCNL, INPS, TUIR) contradicts a request, say so
  before building. Cite the article.
- **Directions over code.** This file is the authority. Code that contradicts it is wrong: flag
  it, don't copy it. Same for plans and specs.
- **Ask before building:** does it exist already? is there a 10x simpler way? will it exist in two
  weeks?
- **LESS CODE IS BETTER CODE.** Dead code goes in the same commit that kills it. Validation,
  security checks and test assertions are never "excess code".
- **Design discipline:** derive, don't duplicate state; think about the general case; the
  mechanism must be lighter than the problem; reuse verbs, not nouns.
- **Debug with data first. Never fabricate explanations.** "I don't know, let me check."
- **Implement once, reuse everywhere.** One computation per concept; the web page and the PDF
  render the same `Payslip` object.
- **No leaky abstractions.** Return domain types (Pydantic models), never dicts callers parse.
- **StrEnum for closed sets.** Event kinds, hour buckets: never bare strings.
- **Total consistency or nothing.** Migrate all instances or none.
- **State the contract** (signature + failure mode) before implementing.
- **Fix root causes, not examples.** No `# type: ignore` / `noqa` without a written reason.
- **Never swallow exceptions.** Handle explicitly or crash. Wrong payroll is worse than no payroll.
- **No default arguments.** Every parameter explicit (AST-enforced). Defaults are silent
  degradation paths.
- **Pydantic models only, no `@dataclass`** (AST-enforced). Type annotations on every signature,
  pyright strict, no lazy imports.
- **Constructor injection, no globals.** `create_app(settings)`; the env is read in exactly one
  place (`app_from_env`).
- **"Done" means done.** Every caller updated, every test green, every doc current.

## Testing standards

- TDD. Golden values are computed BY HAND in a comment next to the assertion, citing the rule.
- Assert outcomes, not call sequences. "If the implementation were wrong, would this catch it?"
- Never assert buggy behaviour. Never weaken production code to make a test pass.
- Use production code in tests (build synthetic inputs, never synthetic outputs).
- Realistic fixtures: a real schedule, real 2026 rates, invented people.
- Zero warnings (`-W error`), suite under 10 s, `--timeout=5`.
- Architecture tests use AST, not string matching.

## Running — the container is the runtime

Nothing is installed on the host. Relative paths, from the repo root:

```bash
scripts/test.sh            # pytest in the dev container (repo mounted read-only)
scripts/lint.sh            # ruff + ruff format --check + pyright strict
scripts/format.sh          # ruff --fix + ruff format (writes)
scripts/check.sh           # lint + tests; the pre-commit hook runs this
git config core.hooksPath .githooks   # once per clone
docker compose up -d --build          # production (needs .env, see .env.example)
```

## Collaboration

- Bite-sized commits, one logical change, message explains WHY. Neutral register (public repo).
  Commit message via a temp file written with the Write tool, `git commit -F`.
- Docs updated in the same commit as the change.
- Don't overengineer: "add X" means X. If a change touches >10 files unexpectedly, stop and ask.
- Never touch other repos.

## Deploy

Docker container on the LAN macvlan with its own IP, listening on 443 directly as
`${TATA_DOMAIN}` (resolved by the LAN DNS only, no public record). `entrypoint.sh` gets a
Let's Encrypt cert with acme.sh (DNS-01 on Cloudflare, state in `${TATA_ACME}`) and checks
renewal daily; uvicorn does not reload certs, so a renewal stops it and `restart:
unless-stopped` brings it back. Details in README.md § Installazione.
