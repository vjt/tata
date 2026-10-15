# Rapporti: one employer, several employment relationships — design

Status: approved (2026-10-15). Supersedes "more than one worker: out of scope" in CLAUDE.md.

## Model

One installation is **one employer** (one login, `TATA_USER` + `TATA_PASSWORD`) with any number of
**rapporti**: one per worker and hire, open (no cessazione, or a future one) or closed. A
worker hired again after a cessazione is a new rapporto: the contract still has no history.
Rapporti may overlap in time (a babysitter and a colf at once). A rapporto is never deleted:
a closed one stays listed and fully readable (payslips, year, attestazione) forever.

A `Rapporto` is the stored contract plus what belongs to that relationship and not to the
contract terms: its id, the Home Assistant tracker of the worker's phone (`ha_entity`, empty =
no wifi presence) and whether the worker has her own access.

Everything that was per installation becomes per rapporto: contract, events, punches (one
open punch per rapporto), finalized payslips, INPS payments (each rapporto has its own INPS
position and quarterly bollettino), TFR advances, employer-car turni.

Per employer, summed over the rapporti: the **deduction** of contributions (art. 10 c.2 TUIR,
the 1.549,37 €/year cap is per employer) — one export for the year, not one per rapporto.

Global, unchanged: rates, the rimborso €/km and km per turno (`TATA_RIMBORSO_*`), the Home
Assistant URL and token.

## Access

- Employer (`TATA_USER` + `TATA_PASSWORD`): everything, every rapporto.
- Worker: a login name and a password per rapporto, set or cleared by the employer on the
  rapporto's page, the password stored as a salted scrypt hash. It opens only her rapporto, and in it only the month, the
  payslip PDF and punching (button and by hand). `TATA_WORKER_PASSWORD` is gone.
- Login names must differ from the employer's and from each other (the name identifies the
  rapporto; schema version 3 added it and dropped the bare passwords of version 2).

## URLs

`/rapporti` lists them (open first) and creates new ones. Each rapporto lives under
`/r/{id}/`: `contratto`, `mese/{y}/{m}`, `timbra`, `presenza/{y}/{m}`, `anno/{y}`, … as before.
`/` and `/timbra` (the PWA start) go to the only open rapporto, or to the list; for a worker,
to her own. The deduction export is `/anno/{y}/deduzioni.yaml`, for the employer.

## Migration

SQLite `user_version` 0 → 1: every table gets `contract_id`; the existing rows become rapporto
1. `TATA_HA_ENTITY` is copied by hand into rapporto 1's tracker after the upgrade (the store
never reads the environment).
