# tata

Hours and payslips for **one** Italian domestic worker (*lavoro domestico*), self-hosted.

Built for a household employing a baby sitter by the hour under the CCNL lavoro domestico
(28 October 2025). You record only the exceptions to the weekly schedule; tata produces:

- the monthly **prospetto paga** (PDF, two signature boxes), CCNL art. 34;
- quarterly **INPS + Cassa Colf** contributions with due dates (10 Apr / 10 Jul / 10 Oct / 10 Jan);
- **tredicesima**, **ferie** and **permessi** balances, **TFR** fund with ISTAT revaluation and
  advances, final settlement on termination;
- the yearly **attestazione delle retribuzioni** for the worker's 730 (CCNL art. 34 c.6);
- a **YAML of the employer's deductible contributions** (rigo E23 / RP23, cash basis, capped).

The domestic employer is not a *sostituto d'imposta*: no IRPEF withholding, no CU, no 770.

## Rules implemented

| Topic | Rule | Source |
|---|---|---|
| Night work 22–6 | +20% | CCNL art. 14 c.6 |
| Overtime (beyond 8 h/day, 40 h/week) | +25% day, +50% night; 40–44 h/week day +10% | art. 15 |
| Sunday / festività worked | +60% | art. 13 c.4, art. 16 c.3 |
| Festività | every festività in the month paid 1/6 of weekly hours, worked day or not | art. 16 c.2 |
| Ferie | 26 working days/year (Mon–Sat), each paid 1/6 of weekly hours | art. 17 |
| Permessi retribuiti | 12 h/year at ≥ 30 h/week, pro-rata below and by months of service | art. 19 |
| Malattia | 8/10/15 paid days per 365 by anzianità; 50% to the 3rd consecutive day, then 100%; 1/30 of monthly pay per calendar day | art. 27, chiarimento 2 |
| Scatti | +4% of the minimum per biennio, from the following month, max 7 | art. 37 |
| Tredicesima | one month's pay (weekly hours × 52/12), 1/12 per month with ≥ 15 days of service | art. 39, chiarimento 4 |
| TFR | yearly pay / 13,5 (full pay counted on sick days), revalued 1,5% + 75% ISTAT | art. 41, art. 2120 c.c. |
| INPS band | paga oraria × 13/12 → hourly contribution; flat rate above 24 h/week | INPS Circ. 9/2026 |
| Cassa Colf | 0,06 €/h, 0,02 worker | art. 54 |
| Deduction | employer INPS share paid in the year, max 1.549,37 € | art. 10 c.2 TUIR |

Overtime is assigned to the chronologically latest minutes of the day/week. Every number that
changes in January lives in [`rates/<year>.yaml`](rates/2026.yaml) with its source.

**Not supported** (raises or is out of scope): fixed-term contracts (contributo addizionale),
live-in workers (vitto/alloggio), more than one worker, hires before the first rates file (2026).
The contract has no history: a raise or a new schedule recomputes every month not yet
finalized, so finalize each month (in order, enforced) once it is paid.

**To double-check** against official texts: INPS band 2 (1,92 €/h) and the >24 h rate come from a
secondary source; INPS hours for sick days are taken as the scheduled hours of those days.

## Privacy: code public, data never

Names, codici fiscali, hours and payslips live in `TATA_DATA_DIR` (a volume outside the
repository). `tests/test_repo_hygiene.py` fails if a database, PDF or `.env` is tracked or if any
tracked file contains a codice fiscale other than the invented test ones.

## Development

The container is the only toolchain; nothing is installed on the host.

```bash
git config core.hooksPath .githooks   # pre-commit runs scripts/check.sh
scripts/test.sh                       # pytest
scripts/lint.sh                       # ruff + pyright strict
scripts/format.sh                     # ruff --fix + format
```

Engineering rules (no default arguments, Decimal money, int minutes, Pydantic only, pure
calculators) are in [CLAUDE.md](CLAUDE.md) and enforced by `tests/test_architecture.py`.

## Deploy

One container with its own LAN address, terminating TLS itself on port 443.

```bash
cp .env.example .env     # data dir, certs dir, network, IP, password
mkdir -p /srv/tata/data /srv/tata/certs   # owned by TATA_UID:TATA_GID
docker compose up -d --build
```

- **TLS**: `server.crt` / `server.key` in `${TATA_CERTS}`. uvicorn reads them at start, so
  whatever renews them must restart the container. With a step-ca cert pusher that deploys
  to Docker containers by copying files and signalling, the target is:

  ```ini
  [docker:tata]
  container_name = tata
  hostname = tata.example.lan
  cert_dest = /root/tata/server.crt
  key_dest = /root/tata/server.key
  signal = RESTART
  ```

  with `${TATA_CERTS}` bind-mounted at `/root/tata` in the pusher.
- **DNS**: an A record for the hostname pointing at `${TATA_IP}`.
- **Auth**: HTTP Basic, any username, the password from `TATA_PASSWORD`.

## Every January

1. Copy `rates/<last year>.yaml` to `rates/<year>.yaml`.
2. Update the CCNL minimums (verbale della Commissione nazionale) and the INPS table (circolare
   of late January / early February), re-verify every other value, update the source comments.
3. During the year, add the ISTAT TFR revaluation coefficients to `tfr_coefficienti` (month →
   cumulative %) as they are published; until then the TFR page flags the missing revaluation.

## License

MIT
