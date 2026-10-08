from datetime import date

from tata.festivita import easter_sunday, festivita


def test_easter_sunday_known_years() -> None:
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)
    assert easter_sunday(2024) == date(2024, 3, 31)


def test_2026_has_thirteen_festivita_with_easter_monday_and_patrono() -> None:
    days = festivita(2026, patrono_mese=6, patrono_giorno=29)
    assert len(days) == 13
    assert date(2026, 4, 6) in days  # lunedì dell'Angelo
    assert date(2026, 10, 4) in days  # San Francesco, CCNL art. 16
    assert date(2026, 6, 29) in days  # patrono


def test_patrono_on_a_national_holiday_is_counted_once() -> None:
    # A patrono on 8 December coincides with a national holiday: one day, not two.
    assert len(festivita(2026, patrono_mese=12, patrono_giorno=8)) == 12


def test_san_francesco_is_a_festivita_only_from_2026() -> None:
    assert date(2025, 10, 4) not in festivita(2025, patrono_mese=6, patrono_giorno=29)
    assert date(2026, 10, 4) in festivita(2026, patrono_mese=6, patrono_giorno=29)
