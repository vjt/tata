"""Test helpers. Invented people only — never real data in this repo."""

from datetime import date
from decimal import Decimal
from itertools import count
from pathlib import Path

from tata.models import Contract, Event, EventKind, Livello, Span
from tata.rates import Rates, RatesBook

RATES_DIR = Path(__file__).parent.parent / "rates"
_ids = count(1)


def rates_book() -> RatesBook:
    return RatesBook(RATES_DIR)


def rates_2026() -> Rates:
    return rates_book().get(2026)


def span(text: str) -> Span:
    return Span.parse(text)


# Mon-Fri 08:00-08:45 + 15:45-18:00 = 3 h/day, 15 h/week.
STANDARD = tuple(
    (span("08:00-08:45"), span("15:45-18:00")) if weekday < 5 else () for weekday in range(7)
)


def contract(
    *,
    assunzione: date,
    paga: str,
    cessazione: date | None,
    orario: tuple[tuple[Span, ...], ...],
    tab_h: bool,
) -> Contract:
    return Contract(
        datore_nome="Giorgio Perozzi",
        datore_cf="PRZGRG50A01D612X",
        lavoratore_nome="Titti Melandri",
        lavoratore_cf="MLNTTT90A41D612Y",
        livello=Livello.BS,
        paga_oraria=Decimal(paga),
        tab_h=tab_h,
        assunzione=assunzione,
        cessazione=cessazione,
        patrono_mese=6,
        patrono_giorno=24,  # San Giovanni, Firenze
        orario=orario,
    )


def standard_contract(assunzione: date) -> Contract:
    return contract(
        assunzione=assunzione, paga="8.86", cessazione=None, orario=STANDARD, tab_h=False
    )


def event(day: date, kind: EventKind, when: str | None) -> Event:
    return Event(
        id=next(_ids), day=day, kind=kind, span=span(when) if when else None, note="fixture"
    )
