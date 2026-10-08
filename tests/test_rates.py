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
