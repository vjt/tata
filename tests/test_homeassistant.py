import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from tata.homeassistant import EntityGone, fetch_history, history_url, parse_history
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


def test_a_null_state_is_unknown_not_a_crash() -> None:
    # An entity renamed or removed (eve's tracker renamed, 1/11/2026): its history ends with a
    # row whose state is null. Like unavailable: not a fact about her.
    payload = PAYLOAD.replace(
        b"]]", b', {"state": null, "last_changed": "2026-10-15T07:00:00+00:00"}]]'
    )
    assert parse_history(payload).changes[-1] == Change(
        at=datetime(2026, 10, 15, 7, 0, tzinfo=UTC), seen=Seen.UNKNOWN
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


class _FakeHA(BaseHTTPRequestHandler):
    """Home Assistant knowing one entity, device_tracker.x, whose history is PAYLOAD."""

    def do_GET(self) -> None:
        assert self.headers["Authorization"] == "Bearer t0ken"
        if self.path.startswith("/api/states/"):
            known = self.path == "/api/states/device_tracker.x"
            self.send_response(200 if known else 404)
            self.end_headers()
            self.wfile.write(b'{"state": "home"}' if known else b'{"message": "Entity not found."}')
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(PAYLOAD)

    def log_message(self, format: str, *args: object) -> None:
        pass  # keep the test output clean


@contextmanager
def fake_ha() -> Generator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHA)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_fetch_history_of_a_known_entity() -> None:
    start, end = datetime(2026, 10, 15, tzinfo=UTC), datetime(2026, 10, 16, tzinfo=UTC)
    with fake_ha() as base:
        assert fetch_history(base, "t0ken", "device_tracker.x", start, end) == parse_history(
            PAYLOAD
        )


def test_fetch_history_of_a_vanished_entity_says_so() -> None:
    start, end = datetime(2026, 10, 15, tzinfo=UTC), datetime(2026, 10, 16, tzinfo=UTC)
    with fake_ha() as base, pytest.raises(EntityGone, match=r"device_tracker\.gone"):
        fetch_history(base, "t0ken", "device_tracker.gone", start, end)
