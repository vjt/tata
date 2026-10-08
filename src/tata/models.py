"""Domain types shared by the calculators, the store and the web layer."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

CENT = Decimal("0.01")
MINUTES_PER_DAY = 24 * 60


def euro(amount: Decimal) -> Decimal:
    """Round to the cent, half-up: the only rounding rule on a payslip."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def hours(minutes: int) -> Decimal:
    """Minutes as hours, two decimals, for display."""
    return euro(Decimal(minutes) / 60)


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Livello(StrEnum):
    A = "A"
    AS = "AS"
    B = "B"
    BS = "BS"
    C = "C"
    CS = "CS"
    D = "D"
    DS = "DS"


class EventKind(StrEnum):
    """Exceptions to the standard schedule."""

    FERIE = "ferie"  # whole working day (Mon-Sat), paid 1/6 of weekly hours
    MALATTIA = "malattia"  # whole calendar day, paid per art. 27
    PERMESSO = "permesso"  # paid leave (art. 19), whole day or a time span
    ASSENZA = "assenza"  # unpaid absence, whole day or a time span
    EXTRA = "extra"  # hours worked beyond the schedule (time span required)


WHOLE_DAY_ONLY = frozenset({EventKind.FERIE, EventKind.MALATTIA})


class Bucket(StrEnum):
    """How a worked minute is paid. Ordered from lowest to highest maggiorazione."""

    ORDINARIO = "ordinario"
    NOTTURNO = "notturno"
    OLTRE_40 = "oltre_40"
    STRAORDINARIO_DIURNO = "straordinario_diurno"
    STRAORDINARIO_NOTTURNO = "straordinario_notturno"
    FESTIVO = "festivo"


class Span(Frozen):
    """A time interval inside one day, in minutes from midnight; end may be 1440 (24:00)."""

    start: int
    end: int

    @model_validator(mode="after")
    def _check(self) -> "Span":
        if not 0 <= self.start < self.end <= MINUTES_PER_DAY:
            raise ValueError(f"intervallo non valido: {self.start}-{self.end}")
        return self

    def minutes(self) -> set[int]:
        return set(range(self.start, self.end))

    @staticmethod
    def parse(text: str) -> "Span":
        """'15:45-18:00' -> Span. Raises ValueError on anything else."""
        start, end = text.strip().split("-")
        return Span(start=_parse_hhmm(start), end=_parse_hhmm(end))

    def __str__(self) -> str:
        return f"{_fmt_hhmm(self.start)}-{_fmt_hhmm(self.end)}"


def _parse_hhmm(text: str) -> int:
    hh, mm = text.strip().split(":")
    return int(hh) * 60 + int(mm)


def _fmt_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class Contract(Frozen):
    """The employment relationship. One per installation."""

    datore_nome: str
    datore_cf: str
    lavoratore_nome: str
    lavoratore_cf: str
    livello: Livello
    paga_oraria: Decimal  # paga globale di fatto agreed, before scatti
    tab_h: bool  # baby sitter of a child under 6 (CCNL art. 34 c.3)
    assunzione: date
    cessazione: date | None
    patrono_mese: int
    patrono_giorno: int
    orario: tuple[tuple[Span, ...], ...]  # 7 entries, Monday first

    @model_validator(mode="after")
    def _check(self) -> "Contract":
        if len(self.orario) != 7:
            raise ValueError("l'orario deve avere 7 giorni")
        if self.cessazione is not None and self.cessazione < self.assunzione:
            raise ValueError("cessazione precedente all'assunzione")
        date(2000, self.patrono_mese, self.patrono_giorno)  # raises if not a real day
        return self

    def weekly_minutes(self) -> int:
        return sum(s.end - s.start for day in self.orario for s in day)

    def employed_on(self, day: date) -> bool:
        return self.assunzione <= day and (self.cessazione is None or day <= self.cessazione)


class Payment(Frozen):
    """An INPS bollettino actually paid: the amounts as computed on the day it was recorded."""

    year: int
    quarter: int
    paid_on: date
    inps_datore: Decimal
    inps_lavoratore: Decimal
    cassa_colf: Decimal
    totale: Decimal


class TfrAdvance(Frozen):
    """Anticipo TFR (CCNL art. 41 c.2)."""

    id: int
    day: date
    amount: Decimal


class Event(Frozen):
    id: int
    day: date
    kind: EventKind
    span: Span | None  # None = whole day
    note: str

    @model_validator(mode="after")
    def _check(self) -> "Event":
        if self.kind in WHOLE_DAY_ONLY and self.span is not None:
            raise ValueError(f"{self.kind} vale per l'intera giornata")
        if self.kind is EventKind.EXTRA and self.span is None:
            raise ValueError("le ore extra richiedono un orario")
        return self
