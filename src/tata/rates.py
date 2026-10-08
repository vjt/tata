"""Yearly rates: CCNL minimums and INPS bands from rates/<year>.yaml."""

from decimal import Decimal
from pathlib import Path

import yaml

from tata.models import Frozen, Livello


class Maggiorazioni(Frozen):
    notturno: Decimal
    oltre_40: Decimal
    straordinario_diurno: Decimal
    straordinario_notturno: Decimal
    festivo: Decimal


class MalattiaGiorni(Frozen):
    fino_6_mesi: int
    fino_2_anni: int
    oltre: int


class Ccnl(Frozen):
    minimi_orari: dict[Livello, Decimal]
    tab_h_orario: Decimal
    maggiorazioni: Maggiorazioni
    max_minuti_giorno: int
    max_minuti_settimana: int
    max_minuti_settimana_banda: int
    ferie_giorni_anno: int
    permessi_ore_anno: int
    permessi_ore_soglia: int
    malattia_giorni_pagati: MalattiaGiorni
    scatto: Decimal
    scatti_max: int
    tfr_divisore: Decimal


class Contributo(Frozen):
    totale: Decimal
    lavoratore: Decimal

    @property
    def datore(self) -> Decimal:
        return self.totale - self.lavoratore


class Fascia(Contributo):
    fino_a: Decimal | None  # None = no upper bound


class Inps(Frozen):
    fasce: tuple[Fascia, ...]
    oltre_24_ore: Contributo
    cassa_colf: Contributo


class Fisco(Frozen):
    deduzione_max: Decimal


class Rates(Frozen):
    year: int
    ccnl: Ccnl
    inps: Inps
    fisco: Fisco
    tfr_coefficienti: dict[int, Decimal]  # month -> cumulative % since previous December


class RatesBook:
    """Loads rates/<year>.yaml on demand. A missing year raises: rates are never carried over."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._loaded: dict[int, Rates] = {}

    def get(self, year: int) -> Rates:
        if year not in self._loaded:
            self._loaded[year] = self._load(year)
        return self._loaded[year]

    def _load(self, year: int) -> Rates:
        path = self._directory / f"{year}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"manca il file delle tariffe {path} per l'anno {year}")
        rates = Rates.model_validate(yaml.safe_load(path.read_text()))
        if rates.year != year:
            raise ValueError(f"{path} dichiara year={rates.year}, atteso {year}")
        return rates
