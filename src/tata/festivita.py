"""Festività per CCNL lavoro domestico art. 16 (national holidays + Easter Monday + patrono)."""

from datetime import date, timedelta

# Art. 16 c.1. 4 October is a national holiday again from 2026 (L. 151/2025), listed by the CCNL.
FIXED = (
    (1, 1),
    (1, 6),
    (4, 25),
    (5, 1),
    (6, 2),
    (8, 15),
    (10, 4),
    (11, 1),
    (12, 8),
    (12, 25),
    (12, 26),
)


def easter_sunday(year: int) -> date:
    """Gregorian Easter (anonymous Gregorian algorithm, Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return date(year, month, day + 1)


def festivita(year: int, patrono_mese: int, patrono_giorno: int) -> frozenset[date]:
    days = {date(year, m, d) for m, d in FIXED}
    days.add(easter_sunday(year) + timedelta(days=1))
    days.add(date(year, patrono_mese, patrono_giorno))
    return frozenset(days)
