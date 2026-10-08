"""One month of pay: the prospetto paga (CCNL art. 34)."""

from datetime import date, timedelta
from decimal import Decimal

from tata.hours import month_work
from tata.models import Bucket, Contract, Event, EventKind, Frozen, Livello, euro
from tata.rates import Contributo, Rates

WEEKS_PER_MONTH = Decimal(52) / 12
DAYS_IN_MONTH_CCNL = 30  # "giorni di calendario" = trentesimi (CCNL chiarimento 2)
SICK_HALF_PAY_DAYS = 3  # art. 27 c.7: 50% up to the 3rd consecutive day

LABELS = {
    Bucket.ORDINARIO: "Ore ordinarie",
    Bucket.NOTTURNO: "Ore notturne (+20%)",
    Bucket.OLTRE_40: "Ore 40-44 settimanali (+10%)",
    Bucket.STRAORDINARIO_DIURNO: "Straordinario diurno (+25%)",
    Bucket.STRAORDINARIO_NOTTURNO: "Straordinario notturno (+50%)",
    Bucket.FESTIVO: "Ore domenicali/festive (+60%)",
}


class Paga(Frozen):
    """How the hourly pay is made up (art. 34 c.2, c.5)."""

    minimo: Decimal
    tab_h: Decimal
    superminimo: Decimal
    scatti_n: int
    scatti: Decimal
    oraria: Decimal  # paga oraria globale di fatto, scatti included


class Line(Frozen):
    voce: str
    quantita: Decimal
    unita: str
    tariffa: Decimal
    importo: Decimal


class Payslip(Frozen):
    year: int
    month: int
    contract: Contract
    paga: Paga
    lines: tuple[Line, ...]
    lordo: Decimal
    ore_retribuite: Decimal  # hours declared to INPS / Cassa Colf
    oraria_effettiva: Decimal  # paga oraria + 1/12 tredicesima: picks the INPS band
    contributo: Contributo  # INPS, per hour
    cassa_colf: Contributo  # per hour
    trattenuta_inps: Decimal
    trattenuta_cassa_colf: Decimal
    netto: Decimal
    tredicesima_maturata: Decimal  # this calendar year, up to this month
    ferie_maturate: Decimal  # days since hire, up to this month
    ferie_godute: Decimal
    ferie_residue: Decimal
    permessi_spettanti: int  # minutes, this calendar year
    permessi_goduti: int
    permessi_eccedenti: bool  # art. 19 allowance exceeded: check it was lutto/nascita (c.3-4)
    imponibile_tfr: Decimal  # lordo + full pay of sick days (art. 2120 c.3 c.c.)
    tfr_quota: Decimal


def compute_payslip(
    contract: Contract, events: list[Event], year: int, month: int, rates: Rates
) -> Payslip:
    """The payslip of one month. Raises ValueError on pay below minimum or inconsistent events."""
    paga = pay_breakdown(contract, year, month, rates)
    p = paga.oraria
    weekly = contract.weekly_minutes()
    day_share = Decimal(weekly) / 6  # minutes paid per festività / ferie day
    monthly = monthly_reference(contract, p)
    days = month_work(contract, events, year, month, rates)
    last_day = days[-1].day

    lines: list[Line] = []
    paid_minutes = Decimal(0)

    worked: dict[Bucket, int] = {}
    for d in days:
        for bucket, minutes in d.buckets.items():
            worked[bucket] = worked.get(bucket, 0) + minutes
    for bucket in Bucket:
        minutes = worked.get(bucket, 0)
        if minutes:
            rate = p * (1 + _maggiorazione(bucket, rates))
            lines.append(_hours_line(LABELS[bucket], Decimal(minutes), rate))
            paid_minutes += minutes

    holidays = sum(1 for d in days if d.festivita and contract.employed_on(d.day))
    if holidays:
        lines.append(_days_line("Festività", holidays, day_share, p))
        paid_minutes += holidays * day_share

    ferie = sum(1 for d in days if d.ferie)
    if ferie:
        lines.append(_days_line("Ferie", ferie, day_share, p))
        paid_minutes += ferie * day_share

    permesso = sum(d.permesso for d in days)
    if permesso:
        lines.append(_hours_line("Permessi retribuiti", Decimal(permesso), p))
        paid_minutes += permesso

    sick = sick_pay(contract, events, rates)
    month_sick = [d for d in days if d.malattia]
    for pct, voce in ((Decimal("0.5"), "Malattia 50%"), (Decimal(1), "Malattia 100%")):
        n = sum(1 for d in month_sick if sick[d.day] == pct)
        if n:
            per_day = monthly / DAYS_IN_MONTH_CCNL * pct
            lines.append(
                Line(
                    voce=voce,
                    quantita=Decimal(n),
                    unita="gg",
                    tariffa=euro(per_day),
                    importo=euro(per_day * n),
                )
            )
    unpaid_sick = sum(1 for d in month_sick if sick[d.day] == 0)
    if unpaid_sick:
        lines.append(
            Line(
                voce="Malattia non retribuita",
                quantita=Decimal(unpaid_sick),
                unita="gg",
                tariffa=Decimal(0),
                importo=Decimal(0),
            )
        )
    # INPS hours of a paid sick day: the hours it was scheduled for (none on a festività).
    paid_minutes += sum(d.scheduled for d in month_sick if sick[d.day] > 0)
    # Art. 2120 c.3 c.c. (CCNL art. 41): TFR counts the full pay of the sick days.
    sick_shortfall = sum(
        (monthly / DAYS_IN_MONTH_CCNL * (1 - sick[d.day]) for d in month_sick), Decimal(0)
    )

    months_this_year = sum(1 for m in range(1, month + 1) if counts_as_month(contract, year, m))
    tredicesima = euro(monthly * months_this_year / 12)
    ending = contract.cessazione is not None and days[0].day <= contract.cessazione <= last_day
    if month == 12 or ending:
        lines.append(
            Line(
                voce="Tredicesima",
                quantita=Decimal(months_this_year),
                unita="/12",
                tariffa=euro(monthly),
                importo=tredicesima,
            )
        )

    ferie_maturate = (
        Decimal(rates.ccnl.ferie_giorni_anno) / 12 * months_since_hire(contract, year, month)
    )
    ferie_godute = Decimal(
        sum(1 for e in events if e.kind is EventKind.FERIE and e.day <= last_day)
    )
    ferie_residue = ferie_maturate - ferie_godute
    if ending and ferie_residue > 0:  # ferie taken in advance are not deducted (art. 17 c.9)
        lines.append(_days_line("Ferie non godute", ferie_residue, day_share, p))
        paid_minutes += ferie_residue * day_share

    lordo = sum((li.importo for li in lines), Decimal(0))
    imponibile_tfr = lordo + euro(sick_shortfall)
    spettanti = permessi_spettanti(contract, year, rates)
    goduti = _permessi_goduti(contract, events, year, month, rates)
    ore = euro(paid_minutes / 60)
    oraria_effettiva = euro(p * 13 / 12)
    contributo = rates.inps.contributo(oraria_effettiva, weekly)
    trattenuta_inps = euro(ore * contributo.lavoratore)
    trattenuta_cassa = euro(ore * rates.inps.cassa_colf.lavoratore)

    return Payslip(
        year=year,
        month=month,
        contract=contract,
        paga=paga,
        lines=tuple(lines),
        lordo=lordo,
        ore_retribuite=ore,
        oraria_effettiva=oraria_effettiva,
        contributo=contributo,
        cassa_colf=rates.inps.cassa_colf,
        trattenuta_inps=trattenuta_inps,
        trattenuta_cassa_colf=trattenuta_cassa,
        netto=lordo - trattenuta_inps - trattenuta_cassa,
        tredicesima_maturata=tredicesima,
        ferie_maturate=euro(ferie_maturate),
        ferie_godute=ferie_godute,
        ferie_residue=euro(ferie_residue),
        permessi_spettanti=spettanti,
        permessi_goduti=goduti,
        permessi_eccedenti=goduti > spettanti,
        imponibile_tfr=imponibile_tfr,
        tfr_quota=euro(imponibile_tfr / rates.ccnl.tfr_divisore),
    )


def pay_breakdown(contract: Contract, year: int, month: int, rates: Rates) -> Paga:
    minimo = rates.ccnl.minimi_orari[contract.livello]
    if contract.tab_h and contract.livello is not Livello.BS:
        raise ValueError("l'indennità tabella H spetta solo al livello BS (baby sitter)")
    tab_h = rates.ccnl.tab_h_orario if contract.tab_h else Decimal(0)
    superminimo = contract.paga_oraria - minimo - tab_h
    if superminimo < 0:
        raise ValueError(
            f"paga oraria {contract.paga_oraria} sotto il minimo {minimo + tab_h} ({year})"
        )
    n = scatti(contract, year, month, rates)
    amount = minimo * rates.ccnl.scatto * n
    return Paga(
        minimo=minimo,
        tab_h=tab_h,
        superminimo=superminimo,
        scatti_n=n,
        scatti=amount,
        oraria=contract.paga_oraria + amount,
    )


def scatti(contract: Contract, year: int, month: int, rates: Rates) -> int:
    """Art. 37: one per completed biennio, due from the month after it completes, max 7."""
    n = 0
    for k in range(1, rates.ccnl.scatti_max + 1):
        hired = contract.assunzione
        anniversary = hired.replace(year=hired.year + 2 * k, day=min(hired.day, 28))
        if (anniversary.year, anniversary.month) < (year, month):
            n = k
    return n


def monthly_reference(contract: Contract, oraria: Decimal) -> Decimal:
    """Monthly pay of the agreed schedule: weekly hours x 52/12 (CCNL chiarimento 1)."""
    return oraria * contract.weekly_minutes() / 60 * WEEKS_PER_MONTH


def counts_as_month(contract: Contract, year: int, month: int) -> bool:
    """A month counts when it has at least 15 days of service (CCNL chiarimento 4)."""
    day = date(year, month, 1)
    served = 0
    while day.month == month:
        served += contract.employed_on(day)
        day += timedelta(days=1)
    return served >= 15


def months_since_hire(contract: Contract, year: int, month: int) -> int:
    y, m = contract.assunzione.year, contract.assunzione.month
    n = 0
    while (y, m) <= (year, month):
        n += counts_as_month(contract, y, m)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return n


def permessi_spettanti(contract: Contract, year: int, rates: Rates) -> int:
    """Art. 19: 12 h/year at >= 30 h/week, pro-rata below; pro-rata by months of service."""
    threshold = rates.ccnl.permessi_ore_soglia * 60
    full = rates.ccnl.permessi_ore_anno * 60 * min(contract.weekly_minutes(), threshold)
    months = sum(1 for m in range(1, 13) if counts_as_month(contract, year, m))
    return full * months // (threshold * 12)


def _permessi_goduti(
    contract: Contract, events: list[Event], year: int, month: int, rates: Rates
) -> int:
    if not any(e.kind is EventKind.PERMESSO and e.day.year == year for e in events):
        return 0
    return sum(
        d.permesso
        for m in range(1, month + 1)
        for d in month_work(contract, events, year, m, rates)
    )


def sick_pay(contract: Contract, events: list[Event], rates: Rates) -> dict[date, Decimal]:
    """Pay fraction of every sick day (art. 27 c.7): 0.5 up to the 3rd consecutive day, then 1;
    0 once the paid days in the trailing 365 days reach the cap for the anzianità."""
    sick_days = sorted(e.day for e in events if e.kind is EventKind.MALATTIA)
    result: dict[date, Decimal] = {}
    paid: list[date] = []
    streak = 0
    previous: date | None = None
    for day in sick_days:
        streak = streak + 1 if previous == day - timedelta(days=1) else 1
        previous = day
        window_start = day - timedelta(days=364)
        if sum(1 for p in paid if p >= window_start) >= _sick_cap(contract, day, rates):
            result[day] = Decimal(0)
            continue
        paid.append(day)
        result[day] = Decimal("0.5") if streak <= SICK_HALF_PAY_DAYS else Decimal(1)
    return result


def _sick_cap(contract: Contract, day: date, rates: Rates) -> int:
    caps = rates.ccnl.malattia_giorni_pagati
    hired = contract.assunzione
    months = (day.year - hired.year) * 12 + day.month - hired.month - (day.day < hired.day)
    if months < 6:
        return caps.fino_6_mesi
    if months < 24:
        return caps.fino_2_anni
    return caps.oltre


def _maggiorazione(bucket: Bucket, rates: Rates) -> Decimal:
    m = rates.ccnl.maggiorazioni
    return {
        Bucket.ORDINARIO: Decimal(0),
        Bucket.NOTTURNO: m.notturno,
        Bucket.OLTRE_40: m.oltre_40,
        Bucket.STRAORDINARIO_DIURNO: m.straordinario_diurno,
        Bucket.STRAORDINARIO_NOTTURNO: m.straordinario_notturno,
        Bucket.FESTIVO: m.festivo,
    }[bucket]


def _hours_line(voce: str, minutes: Decimal, rate: Decimal) -> Line:
    return Line(
        voce=voce,
        quantita=euro(minutes / 60),
        unita="h",
        tariffa=euro(rate),
        importo=euro(minutes * rate / 60),
    )


def _days_line(voce: str, n: Decimal | int, day_share: Decimal, p: Decimal) -> Line:
    return Line(
        voce=voce,
        quantita=euro(Decimal(n)),
        unita="gg",
        tariffa=euro(day_share * p / 60),
        importo=euro(n * day_share * p / 60),
    )
