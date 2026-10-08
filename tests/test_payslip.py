"""Golden payslips. Every expected number is worked out by hand in the comment above it.

Reference worker: 8,86 €/h, Mon-Fri 3 h/day = 15 h/week, livello BS.
Weekly hours / 6 = 2 h 30 (festività and ferie day, CCNL art. 16 c.2, art. 17 c.2).
Monthly reference pay = 8,86 x 15 x 52 / 12 = 575,90 (13ma, malattia trentesimi).
INPS fascia: 8,86 x 13/12 = 9,598 <= 9,61 -> band 1, worker share 0,43/h; Cassa Colf 0,02/h.
"""

from datetime import date
from decimal import Decimal

import pytest

from tata.models import Contract, Event, EventKind
from tata.payslip import Payslip, compute_payslip
from tests.helpers import STANDARD, contract, event, rates_2026, standard_contract


def payslip(worker: Contract, events_: list[Event], year: int, month: int) -> Payslip:
    return compute_payslip(worker, events_, year, month, rates_2026())


def line(slip: Payslip, voce: str) -> Decimal:
    return next(li.importo for li in slip.lines if li.voce == voce)


def test_october_2026_full_month() -> None:
    slip = payslip(standard_contract(date(2026, 1, 1)), [], 2026, 10)
    # 22 weekdays x 3 h = 66 h x 8,86 = 584,76
    assert line(slip, "Ore ordinarie") == Decimal("584.76")
    # 4 October falls on Sunday but is still paid: 2,5 h x 8,86 = 22,15
    assert line(slip, "Festività") == Decimal("22.15")
    assert slip.lordo == Decimal("606.91")
    assert slip.ore_retribuite == Decimal("68.50")
    # 68,5 x 0,43 = 29,455 -> 29,46 ; 68,5 x 0,02 = 1,37
    assert slip.trattenuta_inps == Decimal("29.46")
    assert slip.trattenuta_cassa_colf == Decimal("1.37")
    assert slip.netto == Decimal("576.08")
    # 13ma accrued Jan-Oct: 575,90 x 10/12 = 479,916 -> 479,92
    assert slip.tredicesima_maturata == Decimal("479.92")
    # ferie: 26/12 x 10 months = 21,67 days
    assert slip.ferie_maturate == Decimal("21.67")
    assert slip.ferie_godute == 0
    # TFR quota: 606,91 / 13,5 = 44,956 -> 44,96
    assert slip.tfr_quota == Decimal("44.96")


def test_pay_breakdown_minimo_plus_superminimo() -> None:
    slip = payslip(standard_contract(date(2026, 1, 1)), [], 2026, 10)
    assert slip.paga.minimo == Decimal("7.45")
    assert slip.paga.tab_h == 0
    assert slip.paga.superminimo == Decimal("1.41")
    assert slip.paga.oraria == Decimal("8.86")


def test_pay_breakdown_with_tab_h_indennita() -> None:
    worker = contract(
        assunzione=date(2026, 1, 1), paga="8.86", cessazione=None, orario=STANDARD, tab_h=True
    )
    slip = payslip(worker, [], 2026, 10)
    # 7,45 + 0,84 + 0,57 = 8,86: same total, the indennità is absorbed by the superminimo.
    assert slip.paga.tab_h == Decimal("0.84")
    assert slip.paga.superminimo == Decimal("0.57")
    assert slip.lordo == Decimal("606.91")


def test_pay_below_minimum_is_rejected() -> None:
    worker = contract(
        assunzione=date(2026, 1, 1), paga="7.00", cessazione=None, orario=STANDARD, tab_h=False
    )
    with pytest.raises(ValueError, match="minimo"):
        payslip(worker, [], 2026, 10)


def test_october_2026_hired_on_the_12th() -> None:
    slip = payslip(standard_contract(date(2026, 10, 12)), [], 2026, 10)
    # 15 weekdays from the 12th = 45 h x 8,86 = 398,70; 4 October predates the hire: not paid.
    assert slip.lordo == Decimal("398.70")
    assert [li.voce for li in slip.lines] == ["Ore ordinarie"]
    # 45 x 0,43 = 19,35 ; 45 x 0,02 = 0,90
    assert slip.netto == Decimal("378.45")
    # 20 days of service in October >= 15: a whole month (CCNL chiarimento 4)
    assert slip.tredicesima_maturata == Decimal("47.99")
    assert slip.ferie_maturate == Decimal("2.17")


def test_december_2026_pays_tredicesima() -> None:
    slip = payslip(standard_contract(date(2026, 10, 12)), [], 2026, 12)
    # 23 weekdays, 8 and 25 Dec are festività: 21 x 3 h = 63 h x 8,86 = 558,18
    assert line(slip, "Ore ordinarie") == Decimal("558.18")
    # festività 8, 25, 26 Dec: 3 x 2,5 h = 7,5 h x 8,86 = 66,45
    assert line(slip, "Festività") == Decimal("66.45")
    # Oct, Nov, Dec: 575,90 x 3/12 = 143,975 -> 143,98
    assert line(slip, "Tredicesima") == Decimal("143.98")
    assert slip.lordo == Decimal("768.61")
    # the tredicesima carries no INPS hours: 63 + 7,5 = 70,5 ; x 0,43 = 30,315 -> 30,32
    assert slip.ore_retribuite == Decimal("70.50")
    assert slip.trattenuta_inps == Decimal("30.32")
    assert slip.netto == Decimal("736.88")


def test_ferie_week_costs_the_same_as_a_working_week() -> None:
    worker = standard_contract(date(2026, 1, 1))
    week = [event(date(2026, 10, d), EventKind.FERIE, None) for d in range(19, 25)]  # Mon-Sat
    slip = payslip(worker, week, 2026, 10)
    # 6 ferie days x 2,5 h = 15 h x 8,86 = 132,90; ordinary hours drop to 51 h = 451,86
    assert line(slip, "Ferie") == Decimal("132.90")
    assert line(slip, "Ore ordinarie") == Decimal("451.86")
    assert slip.lordo == Decimal("606.91")
    assert slip.ferie_godute == 6
    assert slip.ferie_residue == Decimal("15.67")


def test_malattia_first_three_days_half_then_full() -> None:
    worker = standard_contract(date(2026, 1, 1))
    sick = [event(date(2026, 10, d), EventKind.MALATTIA, None) for d in range(5, 10)]  # Mon-Fri
    slip = payslip(worker, sick, 2026, 10)
    # one calendar day = 575,90 / 30 = 19,1966 (CCNL chiarimento 2)
    # days 1-3 at 50%: 3 x 19,1966 x 0,5 = 28,795 -> 28,80 ; days 4-5: 2 x 19,1966 = 38,39
    assert line(slip, "Malattia 50%") == Decimal("28.80")
    assert line(slip, "Malattia 100%") == Decimal("38.39")
    # 66 - 15 = 51 h ordinary = 451,86 ; festività 22,15
    assert slip.lordo == Decimal("541.20")
    # INPS hours for paid sick days = the scheduled hours of those days (15 h)
    assert slip.ore_retribuite == Decimal("68.50")
    assert slip.netto == Decimal("510.37")


def test_malattia_beyond_yearly_cap_is_unpaid() -> None:
    # hired in October: anzianità under 6 months -> 8 paid days (art. 27 c.7)
    worker = standard_contract(date(2026, 10, 12))
    sick = [event(date(2026, 11, d), EventKind.MALATTIA, None) for d in range(2, 12)]  # 10 days
    slip = payslip(worker, sick, 2026, 11)
    days = {li.voce: li.quantita for li in slip.lines}
    assert days["Malattia 50%"] == 3
    assert days["Malattia 100%"] == 5
    assert days["Malattia non retribuita"] == 2


def test_permesso_is_paid_at_hourly_rate() -> None:
    worker = standard_contract(date(2026, 1, 1))
    leave = [event(date(2026, 10, 6), EventKind.PERMESSO, "15:45-17:00")]
    slip = payslip(worker, leave, 2026, 10)
    # 1 h 15 x 8,86 = 11,075 -> 11,08 ; ordinary 66 h - 1,25 h = 64,75 h x 8,86 = 573,685 -> 573,69
    assert line(slip, "Permessi retribuiti") == Decimal("11.08")
    assert line(slip, "Ore ordinarie") == Decimal("573.69")
    # 12 h x 15/30 = 6 h per year, pro-rata 12/12 months = 360 min; 75 used
    assert slip.permessi_spettanti == 360
    assert slip.permessi_goduti == 75


def test_friday_night_extra_hours_lines() -> None:
    worker = standard_contract(date(2026, 1, 1))
    extra = [event(date(2026, 10, 9), EventKind.EXTRA, "18:00-24:00")]
    slip = payslip(worker, extra, 2026, 10)
    # 66 + 4 = 70 h x 8,86 = 620,20 ; 1 h x 8,86 x 1,20 = 10,632 -> 10,63 ;
    # 1 h x 8,86 x 1,50 = 13,29
    assert line(slip, "Ore ordinarie") == Decimal("620.20")
    assert line(slip, "Ore notturne (+20%)") == Decimal("10.63")
    assert line(slip, "Straordinario notturno (+50%)") == Decimal("13.29")


def test_scatto_after_first_biennio_raises_pay_and_fascia() -> None:
    # hired 10 March 2024: biennio completed 10 March 2026, scatto from April 2026 (art. 37)
    worker = standard_contract(date(2024, 3, 10))
    march = payslip(worker, [], 2026, 3)
    april = payslip(worker, [], 2026, 4)
    assert march.paga.scatti_n == 0
    assert april.paga.scatti_n == 1
    # 4% of 7,45 = 0,298 -> 8,86 + 0,298 = 9,158 ; x 13/12 = 9,92 > 9,61 -> band 2 (0,48)
    assert april.paga.oraria == Decimal("9.158")
    assert april.contributo.lavoratore == Decimal("0.48")
    assert march.contributo.lavoratore == Decimal("0.43")


def test_cessazione_pays_tredicesima_ratei_and_unused_ferie() -> None:
    worker = contract(
        assunzione=date(2026, 1, 1),
        paga="8.86",
        cessazione=date(2026, 10, 16),
        orario=STANDARD,
        tab_h=False,
    )
    slip = payslip(worker, [], 2026, 10)
    # weekdays 1-16 Oct = 12 x 3 h = 36 h x 8,86 = 318,96 ; festività 4 Oct 22,15
    assert line(slip, "Ore ordinarie") == Decimal("318.96")
    # 16 days of service in October count as a month: 10/12 x 575,90 = 479,92
    assert line(slip, "Tredicesima") == Decimal("479.92")
    # 26 x 10/12 = 21,667 days x 2,5 h = 54,167 h x 8,86 = 479,92
    assert line(slip, "Ferie non godute") == Decimal("479.92")
    assert slip.lordo == Decimal("1300.95")


def test_months_after_cessazione_pay_nothing() -> None:
    worker = contract(
        assunzione=date(2026, 1, 1),
        paga="8.86",
        cessazione=date(2026, 3, 20),
        orario=STANDARD,
        tab_h=False,
    )
    april = payslip(worker, [], 2026, 4)
    assert april.lines == ()
    assert april.lordo == 0
    assert april.ore_retribuite == 0


def test_ferie_taken_in_advance_are_not_silently_deducted_at_cessazione() -> None:
    worker = contract(
        assunzione=date(2026, 1, 1),
        paga="8.86",
        cessazione=date(2026, 2, 28),
        orario=STANDARD,
        tab_h=False,
    )
    # 2 months matured = 4,33 days; 8 taken: residue negative, art. 17 c.9 grants only twelfths
    taken = [event(date(2026, 2, d), EventKind.FERIE, None) for d in (2, 3, 4, 5, 6, 7, 9, 10)]
    slip = payslip(worker, taken, 2026, 2)
    assert "Ferie non godute" not in [li.voce for li in slip.lines]
    assert slip.ferie_residue == Decimal("-3.67")


def test_tfr_counts_full_pay_during_malattia() -> None:
    worker = standard_contract(date(2026, 1, 1))
    sick = [event(date(2026, 10, d), EventKind.MALATTIA, None) for d in range(5, 10)]
    slip = payslip(worker, sick, 2026, 10)
    # art. 2120 c.3 c.c.: during malattia the pay that would have been due counts for TFR.
    # Sick days paid 3 x 50% + 2 x 100% of 19,1966: the missing 1,5 days = 28,795 are added back.
    # 541,20 + 28,80 = 570,00 ; / 13,5 = 42,222 -> 42,22
    assert slip.imponibile_tfr == Decimal("570.00")
    assert slip.tfr_quota == Decimal("42.22")


def test_sick_day_on_a_festivita_adds_no_inps_hours() -> None:
    worker = standard_contract(date(2026, 1, 1))
    # Mon 1 - Fri 5 June 2026; Tue 2 June is a festività, already paid 2,5 h as such
    sick = [event(date(2026, 6, d), EventKind.MALATTIA, None) for d in range(1, 6)]
    slip = payslip(worker, sick, 2026, 6)
    # June: 22 weekdays; 2 and 24 June festività -> 20 scheduled days x 3 h = 60 h,
    # minus 4 sick scheduled days (1, 3, 4, 5) = 48 h worked ; festività 2 x 2,5 = 5 h ;
    # sick hours: the 4 scheduled days = 12 h -> 65 h
    assert slip.ore_retribuite == Decimal("65.00")


def test_permessi_beyond_the_yearly_allowance_are_flagged() -> None:
    worker = standard_contract(date(2026, 1, 1))
    leave = [event(date(2026, 10, d), EventKind.PERMESSO, None) for d in (5, 6)]  # 6 h
    assert not payslip(worker, leave, 2026, 10).permessi_eccedenti
    leave.append(event(date(2026, 10, 7), EventKind.PERMESSO, "08:00-08:45"))
    assert payslip(worker, leave, 2026, 10).permessi_eccedenti
