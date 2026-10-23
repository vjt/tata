"""Domain types shared by the calculators, the store and the web layer."""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

CENT = Decimal("0.01")
MINUTES_PER_DAY = 24 * 60
NOON = 12 * 60  # morning before, afternoon from: wifi shifts and rimborso turni


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
    MORA = "mora"  # sent home by the employer: paid as scheduled (art. 1206 c.c.), span required


WHOLE_DAY_ONLY = frozenset({EventKind.FERIE, EventKind.MALATTIA})
SPAN_ONLY = frozenset({EventKind.EXTRA, EventKind.MORA})


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
    """The terms of one employment relationship (see Rapporto)."""

    datore_nome: str
    datore_cf: str
    lavoratore_nome: str
    lavoratore_cf: str
    livello: Livello
    paga_oraria: Decimal  # paga globale di fatto agreed, before scatti
    tab_h: bool  # baby sitter of a child under 6 (CCNL art. 34 c.3)
    # The employer withholds the worker's INPS and Cassa Colf share from her pay; False: he
    # pays her the lordo and bears that share himself (vjt 23/10: a choice, not a rule).
    trattiene: bool
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
        date(2001, self.patrono_mese, self.patrono_giorno)  # must exist every year: no 29/02
        return self

    def weekly_minutes(self) -> int:
        return sum(s.end - s.start for day in self.orario for s in day)

    def employed_on(self, day: date) -> bool:
        return self.assunzione <= day and (self.cessazione is None or day <= self.cessazione)


class Rapporto(Frozen):
    """One employment relationship: the contract, plus what is not a contract term."""

    id: int
    contract: Contract
    ha_entity: str  # Home Assistant tracker of the worker's phone; "" = no wifi presence
    worker_user: str | None  # her own login name; None = no access of her own

    def open_on(self, day: date) -> bool:
        return self.contract.cessazione is None or self.contract.cessazione >= day


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
        if self.kind in SPAN_ONLY and self.span is None:
            raise ValueError(f"{self.kind} richiede un orario")
        return self


class Punch(Frozen):
    """A cartellino entry: when the worker actually started and stopped. end None = still open."""

    id: int
    day: date
    start: int
    end: int | None

    @model_validator(mode="after")
    def _check(self) -> "Punch":
        if not 0 <= self.start < MINUTES_PER_DAY:
            raise ValueError(f"ora di ingresso non valida: {self.start}")
        if self.end is not None:
            self.span()  # same bounds as any span: start < end <= 24:00
        return self

    def span(self) -> Span:
        if self.end is None:
            raise ValueError(f"timbratura del {self.day} ancora aperta")
        return Span(start=self.start, end=self.end)


class Mileage(Frozen):
    """Rimborso trasferte: km per turno (andata e ritorno) at the ACI €/km of her car."""

    km_turno: Decimal
    euro_km: Decimal

    @model_validator(mode="after")
    def _check(self) -> "Mileage":
        if self.km_turno <= 0 or self.euro_km <= 0:
            raise ValueError("rimborso trasferte: km e €/km devono essere positivi")
        return self


class Spesa(Frozen):
    """An expense she paid for the employer (a snack out with the children): reimbursed in
    the month of its day, not income: outside lordo, INPS, TFR."""

    id: int
    day: date
    importo: Decimal
    descrizione: str

    @model_validator(mode="after")
    def _check(self) -> "Spesa":
        if not self.importo.is_finite() or self.importo <= 0:
            raise ValueError("la spesa deve avere un importo positivo")
        if not self.descrizione.strip():
            raise ValueError("la spesa deve avere una descrizione")
        return self


class WagePayment(Frozen):
    """Money the employer gave her: any amount on any day (acconti too), never tied to a
    month; the balance sets it against everything owed since the hire."""

    id: int
    day: date
    importo: Decimal
    nota: str

    @model_validator(mode="after")
    def _check(self) -> "WagePayment":
        if not self.importo.is_finite() or self.importo <= 0:
            raise ValueError("il pagamento deve avere un importo positivo")
        return self


class Session(Frozen):
    """A logged-in browser. The cookie holds a random token; only its hash is stored."""

    token_hash: str
    contract_id: int | None  # the worker's rapporto; None = the employer
    credential_tag: str  # digest of the credential used: changing it ends the session
    expires: datetime
