from datetime import date

import pytest

from tata.models import Contract, Punch
from tests.helpers import span, standard_contract


def test_patrono_on_29_february_is_rejected() -> None:
    data = standard_contract(date(2026, 1, 1)).model_dump()
    data.update(patrono_mese=2, patrono_giorno=29)
    with pytest.raises(ValueError):
        Contract.model_validate(data)


def test_punch_bounds() -> None:
    open_ = Punch(id=1, day=date(2026, 10, 13), start=480, end=None)
    with pytest.raises(ValueError, match="aperta"):
        open_.span()
    assert Punch(id=1, day=date(2026, 10, 13), start=480, end=525).span() == span("08:00-08:45")
    with pytest.raises(ValueError):
        Punch(id=1, day=date(2026, 10, 13), start=480, end=480)  # empty
    with pytest.raises(ValueError):
        Punch(id=1, day=date(2026, 10, 13), start=1440, end=None)  # starts at midnight
