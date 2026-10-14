"""Read a device tracker's history from Home Assistant's REST API (the only network I/O)."""

from datetime import datetime
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, TypeAdapter

from tata.presenza import Change, History, Seen

TIMEOUT = 10  # seconds: the page shows the error rather than hanging


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")
    state: str
    last_changed: datetime


_ROWS = TypeAdapter(list[list[_Row]])


def history_url(base: str, entity: str, start: datetime, end: datetime) -> str:
    query = urlencode({"filter_entity_id": entity, "end_time": end.isoformat()})
    return (
        f"{base.rstrip('/')}/api/history/period/{quote(start.isoformat(), safe='')}"
        f"?{query}&minimal_response&no_attributes"
    )


def fetch_history(base: str, token: str, entity: str, start: datetime, end: datetime) -> History:
    """The entity's states from start to end. Raises URLError/TimeoutError if HA is down."""
    request = Request(
        history_url(base, entity, start, end), headers={"Authorization": f"Bearer {token}"}
    )
    with urlopen(request, timeout=TIMEOUT) as response:
        return parse_history(response.read())


def parse_history(payload: bytes) -> History:
    """The first row is the state at the start of the period. Raises ValidationError."""
    rows = _ROWS.validate_json(payload)
    if not rows or not rows[0]:
        return History(initial=Seen.UNKNOWN, changes=[])
    first, *rest = rows[0]
    return History(
        initial=_seen(first.state),
        changes=[Change(at=r.last_changed, seen=_seen(r.state)) for r in rest],
    )


def _seen(state: str) -> Seen:
    """home / not_home; anything else (unavailable, unknown) is a restart, not a fact."""
    if state == "home":
        return Seen.HOME
    if state == "not_home":
        return Seen.AWAY
    return Seen.UNKNOWN
