"""Quarterly INPS + Cassa Colf contributions for the domestic worker."""

from datetime import date
from decimal import Decimal

from tata.models import Frozen, euro
from tata.payslip import Payslip


class QuarterContributions(Frozen):
    year: int
    quarter: int
    ore: Decimal
    inps_totale: Decimal
    inps_lavoratore: Decimal
    inps_datore: Decimal
    cassa_totale: Decimal
    cassa_lavoratore: Decimal
    cassa_datore: Decimal
    totale: Decimal  # the bollettino amount
    scadenza: date


def quarter_months(quarter: int) -> range:
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"trimestre non valido: {quarter}")
    return range(3 * quarter - 2, 3 * quarter + 1)


def due_date(year: int, quarter: int) -> date:
    """10 April, 10 July, 10 October, 10 January of the following year."""
    last_month = quarter_months(quarter)[-1]
    return date(year + 1, 1, 10) if last_month == 12 else date(year, last_month + 1, 10)


def quarter_contributions(payslips: list[Payslip], year: int, quarter: int) -> QuarterContributions:
    """Sum of the quarter's payslips. The worker share is exactly what the payslips withheld."""
    months = quarter_months(quarter)
    for slip in payslips:
        if slip.year != year or slip.month not in months:
            raise ValueError(f"prospetto {slip.month}/{slip.year} fuori dal trimestre {quarter}")
    inps = sum((euro(s.ore_retribuite * s.contributo.totale) for s in payslips), Decimal(0))
    inps_worker = sum((s.trattenuta_inps for s in payslips), Decimal(0))
    cassa = sum((euro(s.ore_retribuite * s.cassa_colf.totale) for s in payslips), Decimal(0))
    cassa_worker = sum((s.trattenuta_cassa_colf for s in payslips), Decimal(0))
    return QuarterContributions(
        year=year,
        quarter=quarter,
        ore=sum((s.ore_retribuite for s in payslips), Decimal("0.00")),
        inps_totale=inps,
        inps_lavoratore=inps_worker,
        inps_datore=inps - inps_worker,
        cassa_totale=cassa,
        cassa_lavoratore=cassa_worker,
        cassa_datore=cassa - cassa_worker,
        totale=inps + cassa,
        scadenza=due_date(year, quarter),
    )
