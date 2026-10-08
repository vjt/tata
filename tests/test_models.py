from datetime import date

import pytest

from tata.models import Contract
from tests.helpers import standard_contract


def test_patrono_on_29_february_is_rejected() -> None:
    data = standard_contract(date(2026, 1, 1)).model_dump()
    data.update(patrono_mese=2, patrono_giorno=29)
    with pytest.raises(ValueError):
        Contract.model_validate(data)
