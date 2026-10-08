"""Yearly views: TFR fund, attestazione for the worker's 730, employer's deduction."""

import calendar
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import yaml

from tata.models import Contract, Event, Frozen, Payment, TfrAdvance, euro
from tata.payslip import Payslip, compute_payslip
from tata.rates import Rates, RatesBook

# (year, month) -> payslip. Production returns the finalized snapshot when there is one.
PayslipSource = Callable[[int, int], Payslip]


def employed_months(contract: Contract, year: int, upto_month: int) -> list[int]:
    """Months of the year, up to upto_month, with at least one day of employment."""
    months: list[int] = []
    for m in range(1, upto_month + 1):
        first, last = date(year, m, 1), date(year, m, calendar.monthrange(year, m)[1])
        ends = contract.cessazione
        if contract.assunzione <= last and (ends is None or ends >= first):
            months.append(m)
    return months


def computed_source(contract: Contract, events: list[Event], book: RatesBook) -> PayslipSource:
    """Payslips computed from scratch, no snapshots."""

    def source(year: int, month: int) -> Payslip:
        return compute_payslip(contract, events, year, month, book.get(year))

    return source


def year_payslips(
    contract: Contract, year: int, upto_month: int, source: PayslipSource
) -> list[Payslip]:
    return [source(year, m) for m in employed_months(contract, year, upto_month)]


# ---- TFR (CCNL art. 41, art. 2120 c.c.)


class TfrInput(Frozen):
    year: int
    imponibile: Decimal  # retribuzioni of the year
    anticipi: Decimal
    coefficiente: Decimal | None  # % revaluation of the opening fund; None = not published yet
    divisore: Decimal


class TfrYear(Frozen):
    year: int
    imponibile: Decimal
    quota: Decimal
    rivalutazione: Decimal
    anticipi: Decimal
    fondo: Decimal


class Tfr(Frozen):
    anni: tuple[TfrYear, ...]
    fondo: Decimal
    rivalutazione_mancante: bool  # a needed ISTAT coefficient is not in the rates file yet


def tfr_fold(years: list[TfrInput]) -> Tfr:
    """Fund at the end of each year: opening fund revalued + imponibile / divisore - advances.

    A missing coefficient on a non-empty fund is reported, not guessed: the fund is then shown
    without that revaluation and flagged.
    """
    fondo = Decimal(0)
    missing = False
    rows: list[TfrYear] = []
    for y in years:
        rivalutazione = Decimal(0)
        if fondo:
            if y.coefficiente is None:
                missing = True
            else:
                rivalutazione = euro(fondo * y.coefficiente / 100)
        quota = euro(y.imponibile / y.divisore)
        fondo = fondo + rivalutazione + quota - y.anticipi
        rows.append(
            TfrYear(
                year=y.year,
                imponibile=y.imponibile,
                quota=quota,
                rivalutazione=rivalutazione,
                anticipi=y.anticipi,
                fondo=fondo,
            )
        )
    return Tfr(anni=tuple(rows), fondo=fondo, rivalutazione_mancante=missing)


def tfr(
    contract: Contract,
    advances: list[TfrAdvance],
    year: int,
    month: int,
    source: PayslipSource,
    book: RatesBook,
) -> Tfr:
    """TFR fund from hire to the end of year/month."""
    inputs: list[TfrInput] = []
    for y in range(contract.assunzione.year, year + 1):
        upto = 12 if y < year else month
        rates = book.get(y)
        imponibile = sum(
            (s.imponibile_tfr for s in year_payslips(contract, y, upto, source)), Decimal(0)
        )
        coefficient_month = 12 if y < year else month - 1
        coefficiente = (
            Decimal(0) if coefficient_month == 0 else rates.tfr_coefficienti.get(coefficient_month)
        )
        anticipi = sum(
            (a.amount for a in advances if a.day.year == y and (y < year or a.day.month <= upto)),
            Decimal(0),
        )
        inputs.append(
            TfrInput(
                year=y,
                imponibile=imponibile,
                anticipi=anticipi,
                coefficiente=coefficiente,
                divisore=rates.ccnl.tfr_divisore,
            )
        )
    return tfr_fold(inputs)


# ---- Attestazione somme erogate (CCNL art. 34 c.6), for the worker's 730


class Attestazione(Frozen):
    year: int
    contract: Contract
    mesi: tuple[Payslip, ...]
    lordo: Decimal
    trattenute_inps: Decimal
    trattenute_cassa_colf: Decimal
    netto: Decimal


def attestazione(contract: Contract, year: int, source: PayslipSource) -> Attestazione:
    slips = year_payslips(contract, year, 12, source)
    return Attestazione(
        year=year,
        contract=contract,
        mesi=tuple(slips),
        lordo=sum((s.lordo for s in slips), Decimal(0)),
        trattenute_inps=sum((s.trattenuta_inps for s in slips), Decimal(0)),
        trattenute_cassa_colf=sum((s.trattenuta_cassa_colf for s in slips), Decimal(0)),
        netto=sum((s.netto for s in slips), Decimal(0)),
    )


# ---- Oneri deducibili of the employer (art. 10 c.2 TUIR), cash basis


class Deduzione(Frozen):
    year: int
    versamenti: tuple[Payment, ...]
    inps_datore_versato: Decimal
    deducibile: Decimal
    limite: Decimal
    cassa_colf_versata: Decimal  # not deductible, reported for completeness


def deduzione(payments: list[Payment], year: int, rates: Rates) -> Deduzione:
    """Employer's INPS share PAID in the year (whatever quarter it refers to), capped."""
    paid = tuple(sorted((p for p in payments if p.paid_on.year == year), key=lambda p: p.paid_on))
    versato = sum((p.inps_datore for p in paid), Decimal(0))
    limite = rates.fisco.deduzione_max
    return Deduzione(
        year=year,
        versamenti=paid,
        inps_datore_versato=versato,
        deducibile=min(versato, limite),
        limite=limite,
        cassa_colf_versata=sum((p.cassa_colf for p in paid), Decimal(0)),
    )


def deduzione_yaml(d: Deduzione) -> str:
    data = {
        "anno": d.year,
        "rigo": "730: E23 / Redditi PF: RP23 (contributi addetti ai servizi domestici)",
        "criterio": "cassa: versamenti effettuati nell'anno, quota a carico del datore",
        "contributi_inps_datore_versati": str(d.inps_datore_versato),
        "limite": str(d.limite),
        "importo_deducibile": str(d.deducibile),
        "cassa_colf_versata_non_deducibile": str(d.cassa_colf_versata),
        "versamenti": [
            {
                "trimestre": f"{p.year}-T{p.quarter}",
                "pagato_il": p.paid_on,
                "inps_datore": str(p.inps_datore),
                "inps_lavoratore": str(p.inps_lavoratore),
                "cassa_colf": str(p.cassa_colf),
                "totale": str(p.totale),
            }
            for p in d.versamenti
        ],
    }
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
