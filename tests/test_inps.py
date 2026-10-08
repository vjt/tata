from datetime import date
from decimal import Decimal

import pytest

from tata.inps import due_date, quarter_contributions
from tata.payslip import compute_payslip
from tests.helpers import rates_2026, standard_contract


def test_due_dates_are_the_tenth_after_each_quarter() -> None:
    assert due_date(2026, 1) == date(2026, 4, 10)
    assert due_date(2026, 2) == date(2026, 7, 10)
    assert due_date(2026, 3) == date(2026, 10, 10)
    assert due_date(2026, 4) == date(2027, 1, 10)


def test_fourth_quarter_2026_hired_12_october() -> None:
    worker = standard_contract(date(2026, 10, 12))
    slips = [compute_payslip(worker, [], 2026, m, rates_2026()) for m in (10, 11, 12)]
    q = quarter_contributions(slips, 2026, 4)
    # ore retribuite: Oct 45 + Nov 65,5 (21 weekdays x 3 + 1 Nov festività 2,5) + Dec 70,5 = 181
    assert q.ore == Decimal("181.00")
    # INPS band 1 at 1,70/h, per month: 76,50 + 111,35 + 119,85 = 307,70
    assert q.inps_totale == Decimal("307.70")
    # worker share as withheld on the payslips: 19,35 + 28,17 + 30,32 = 77,84
    assert q.inps_lavoratore == Decimal("77.84")
    assert q.inps_datore == Decimal("229.86")
    # Cassa Colf 0,06/h: 2,70 + 3,93 + 4,23 = 10,86 ; worker 0,90 + 1,31 + 1,41 = 3,62
    assert q.cassa_totale == Decimal("10.86")
    assert q.cassa_lavoratore == Decimal("3.62")
    assert q.cassa_datore == Decimal("7.24")
    assert q.totale == Decimal("318.56")
    assert q.scadenza == date(2027, 1, 10)


def test_quarter_rejects_payslips_from_other_months() -> None:
    worker = standard_contract(date(2026, 1, 1))
    march = compute_payslip(worker, [], 2026, 3, rates_2026())
    with pytest.raises(ValueError, match="trimestre"):
        quarter_contributions([march], 2026, 2)
