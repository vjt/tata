from decimal import Decimal
from pathlib import Path

import pytest

from tata.models import Livello
from tata.rates import RatesBook

RATES_DIR = Path(__file__).parent.parent / "rates"


def test_2026_file_loads_with_ccnl_and_inps_values() -> None:
    rates = RatesBook(RATES_DIR).get(2026)
    assert rates.ccnl.minimi_orari[Livello.BS] == Decimal("7.45")
    assert rates.inps.fasce[0].fino_a == Decimal("9.61")
    assert rates.inps.fasce[0].totale == Decimal("1.70")
    assert rates.inps.fasce[-1].fino_a is None
    assert rates.inps.cassa_colf.lavoratore == Decimal("0.02")


def test_every_level_has_a_minimum() -> None:
    rates = RatesBook(RATES_DIR).get(2026)
    assert set(rates.ccnl.minimi_orari) == set(Livello)


def test_missing_year_raises_instead_of_reusing_last_year() -> None:
    with pytest.raises(FileNotFoundError, match="1999"):
        RatesBook(RATES_DIR).get(1999)


def test_year_inside_file_must_match_file_name(tmp_path: Path) -> None:
    (tmp_path / "2030.yaml").write_text((RATES_DIR / "2026.yaml").read_text())
    with pytest.raises(ValueError, match="2030"):
        RatesBook(tmp_path).get(2030)


def test_inps_band_boundary_is_inclusive() -> None:
    inps = RatesBook(RATES_DIR).get(2026).inps
    # 8,86 x 13/12 = 9,598 and exactly 9,61 are band 1; 9,6100001 is band 2
    assert inps.contributo(Decimal("9.598"), 900).totale == Decimal("1.70")
    assert inps.contributo(Decimal("9.61"), 900).totale == Decimal("1.70")
    assert inps.contributo(Decimal("9.6100001"), 900).totale == Decimal("1.92")
    assert inps.contributo(Decimal("12"), 900).totale == Decimal("2.34")


def test_inps_flat_rate_above_24_hours_a_week() -> None:
    inps = RatesBook(RATES_DIR).get(2026).inps
    assert inps.contributo(Decimal("9.598"), 24 * 60).totale == Decimal("1.70")
    assert inps.contributo(Decimal("9.598"), 25 * 60).totale == Decimal("1.24")
    assert inps.contributo(Decimal("9.598"), 25 * 60).datore == Decimal("0.93")
