from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tata.homeassistant import history_url, parse_history
from tata.presenza import Change, History, Seen

# Shape of GET /api/history/period/...?minimal_response&no_attributes: the first row is the
# state at the start of the period, in full; the next ones carry only state and last_changed.
PAYLOAD = b"""[[
 {"entity_id": "device_tracker.openwrt_presence_necchi_wifi", "state": "not_home",
  "attributes": {}, "last_changed": "2026-10-14T22:00:00+00:00",
  "last_updated": "2026-10-14T22:00:00+00:00"},
 {"state": "home", "last_changed": "2026-10-15T05:52:40.112930+00:00"},
 {"state": "unavailable", "last_changed": "2026-10-15T06:00:00+00:00"},
 {"state": "not_home", "last_changed": "2026-10-15T06:19:30+00:00"}
]]"""


def test_parse_history() -> None:
    assert parse_history(PAYLOAD) == History(
        initial=Seen.AWAY,
        changes=[
            Change(at=datetime(2026, 10, 15, 5, 52, 40, 112930, tzinfo=UTC), seen=Seen.HOME),
            Change(at=datetime(2026, 10, 15, 6, 0, tzinfo=UTC), seen=Seen.UNKNOWN),
            Change(at=datetime(2026, 10, 15, 6, 19, 30, tzinfo=UTC), seen=Seen.AWAY),
        ],
    )


def test_entity_without_history_is_unknown() -> None:
    assert parse_history(b"[]") == History(initial=Seen.UNKNOWN, changes=[])


def test_garbage_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse_history(b'[[{"state": "home"}]]')


def test_history_url_encodes_the_offsets() -> None:
    url = history_url(
        "http://ha.example:8123/",
        "device_tracker.x",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 15, 8, 30, tzinfo=UTC),
    )
    assert url == (
        "http://ha.example:8123/api/history/period/2026-10-01T00%3A00%3A00%2B00%3A00"
        "?filter_entity_id=device_tracker.x&end_time=2026-10-15T08%3A30%3A00%2B00%3A00"
        "&minimal_response&no_attributes"
    )
