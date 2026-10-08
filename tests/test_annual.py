from datetime import date
from decimal import Decimal

import yaml

from tata.annual import TfrInput, attestazione, deduzione, deduzione_yaml, tfr, tfr_fold
from tata.models import Payment, TfrAdvance
from tests.helpers import rates_2026, rates_book, standard_contract

DIVISORE = Decimal("13.5")


def test_tfr_fold_quota_revaluation_and_advance() -> None:
    result = tfr_fold(
        [
            TfrInput(
                year=2026,
                imponibile=Decimal("1350.00"),
                anticipi=Decimal(0),
                coefficiente=None,
                divisore=DIVISORE,
            ),
            TfrInput(
                year=2027,
                imponibile=Decimal("2700.00"),
                anticipi=Decimal("50.00"),
                coefficiente=Decimal("2.0"),
                divisore=DIVISORE,
            ),
        ]
    )
    # 2026: 1350 / 13,5 = 100,00
    # 2027: 100 x 2,0% = 2,00 revaluation + 2700 / 13,5 = 200,00 - 50 advance -> 252,00
    assert [y.fondo for y in result.anni] == [Decimal("100.00"), Decimal("252.00")]
    assert result.anni[1].rivalutazione == Decimal("2.00")
    assert result.fondo == Decimal("252.00")
    assert not result.rivalutazione_mancante


def test_tfr_fold_flags_a_missing_coefficient_instead_of_inventing_one() -> None:
    result = tfr_fold(
        [
            TfrInput(
                year=2026,
                imponibile=Decimal("1350.00"),
                anticipi=Decimal(0),
                coefficiente=None,
                divisore=DIVISORE,
            ),
            TfrInput(
                year=2027,
                imponibile=Decimal(0),
                anticipi=Decimal(0),
                coefficiente=None,
                divisore=DIVISORE,
            ),
        ]
    )
    assert result.rivalutazione_mancante
    assert result.fondo == Decimal("100.00")


def test_tfr_first_year_needs_no_coefficient() -> None:
    worker = standard_contract(date(2026, 10, 12))
    result = tfr(worker, [], [], 2026, 12, rates_book())
    # imponibile Oct 398,70 + Nov 580,33 + Dec 768,61 = 1747,64 ; / 13,5 = 129,455 -> 129,45
    assert result.anni[0].imponibile == Decimal("1747.64")
    assert result.fondo == Decimal("129.45")


def test_tfr_advance_reduces_the_fund() -> None:
    worker = standard_contract(date(2026, 10, 12))
    advance = TfrAdvance(id=1, day=date(2026, 12, 20), amount=Decimal("29.45"))
    assert tfr(worker, [], [advance], 2026, 12, rates_book()).fondo == Decimal("100.00")


def test_attestazione_sums_the_year() -> None:
    worker = standard_contract(date(2026, 10, 12))
    att = attestazione(worker, [], 2026, rates_book())
    assert [s.month for s in att.mesi] == [10, 11, 12]
    assert att.lordo == Decimal("1747.64")
    # 19,35 + 28,17 + 30,32 ; 0,90 + 1,31 + 1,41
    assert att.trattenute_inps == Decimal("77.84")
    assert att.trattenute_cassa_colf == Decimal("3.62")
    assert att.netto == Decimal("1666.18")


def payment(year: int, quarter: int, paid_on: date, datore: str) -> Payment:
    return Payment(
        year=year,
        quarter=quarter,
        paid_on=paid_on,
        inps_datore=Decimal(datore),
        inps_lavoratore=Decimal("10.00"),
        cassa_colf=Decimal("5.00"),
        totale=Decimal(datore) + 15,
    )


def test_deduzione_is_cash_basis() -> None:
    payments = [
        payment(2025, 4, date(2026, 1, 9), "200.00"),  # paid in 2026: counts for 2026
        payment(2026, 1, date(2026, 4, 10), "300.00"),
        payment(2026, 4, date(2027, 1, 8), "400.00"),  # paid in 2027: not 2026
    ]
    d = deduzione(payments, 2026, rates_2026())
    assert d.inps_datore_versato == Decimal("500.00")
    assert d.deducibile == Decimal("500.00")
    # Cassa Colf is never deductible, reported apart
    assert d.cassa_colf_versata == Decimal("10.00")


def test_deduzione_is_capped() -> None:
    payments = [payment(2026, q, date(2026, 3 * q + 1, 10), "500.00") for q in (1, 2, 3)]
    d = deduzione(payments, 2026, rates_2026())
    assert d.inps_datore_versato == Decimal("1500.00")
    payments.append(payment(2025, 4, date(2026, 1, 10), "100.00"))
    assert deduzione(payments, 2026, rates_2026()).deducibile == Decimal("1549.37")


def test_deduzione_yaml_round_trips() -> None:
    d = deduzione([payment(2026, 1, date(2026, 4, 10), "300.00")], 2026, rates_2026())
    data = yaml.safe_load(deduzione_yaml(d))
    assert data["anno"] == 2026
    assert data["importo_deducibile"] == "300.00"
    assert "E23" in data["rigo"]
    assert data["versamenti"][0]["pagato_il"] == date(2026, 4, 10)
