# Rimborso trasferte (school run by car) — design

Status: approved (2026-10-15).

## Rule

Every turno worked carries a mileage reimbursement, by default: the worker drives the children
between home and school with her own car (the employer can't tell which car was used, so hers
is the default). Home and school are in different comuni.

- **Turno** = a half-day with at least one closed punch: morning (punch starting before 12:00)
  or afternoon. At most two a day, however many punches; a punch is never counted twice.
- **Km per turno** (andata + ritorno) and **€/km** are configuration, not code:
  `TATA_RIMBORSO_KM` and `TATA_RIMBORSO_EURO_KM`, both or neither (neither: no
  reimbursement). They are runtime data in `.env`: the route length describes the household.
- **Amount per turno** = km × €/km, rounded to the cent once; the month = turni × that amount.
  The €/km comes from the ACI tables (costo chilometrico for the worker's car model).

## Tax and contributions

Trasferta outside the comune, reimbursed on the ACI tables: not income (art. 51 c.5 TUIR), so
not in the lordo, not INPS / Cassa Colf hours, not TFR, not in the attestazione. The payslip
shows it below the netto: **netto** stays lordo − trattenute, **da pagare** = netto + rimborso.

## Where it shows

- Cartellino (month page): a table, one row per punch, aligned columns: hours, day, span,
  car, time cost (minutes × plain hourly pay, no maggiorazioni) and trasferta cost (her car,
  once per turno), no km; the month's totals. ✏️ edits a row, ❌ deletes it. The car is declared per
  turno by a radio (*auto propria* / *auto del datore*) next to the timbra button and in
  the punch form, which *modifica* fills with a row to correct it (vjt 16/10).
- Prospetto (page and PDF): a line under the netto, and the amount to pay.

## Out of scope

Telling the worker's car from the employer's automatically (no GPS). Her car is the default;
the employer marks by hand, on the cartellino, a turno done with his car: it pays no rimborso.
