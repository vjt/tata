"""Read a device tracker's history from Home Assistant's REST API (the only network I/O)."""

from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, TypeAdapter

from tata.presenza import Change, History, Seen

TIMEOUT = 10  # seconds: the page shows the error rather than hanging


class EntityGone(Exception):
    """Home Assistant is up but does not know the entity: renamed or removed."""

    def __init__(self, entity: str) -> None:
        super().__init__(
            f"il tracker {entity} non esiste più in Home Assistant (rinominato o rimosso?): "
            "aggiornalo nel contratto"
        )


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")
    state: str | None  # null: the entity was renamed or removed at that moment
    last_changed: datetime


_ROWS = TypeAdapter(list[list[_Row]])


def history_url(base: str, entity: str, start: datetime, end: datetime) -> str:
    query = urlencode({"filter_entity_id": entity, "end_time": end.isoformat()})
    return (
        f"{base.rstrip('/')}/api/history/period/{quote(start.isoformat(), safe='')}"
        f"?{query}&minimal_response&no_attributes"
    )


def fetch_history(base: str, token: str, entity: str, start: datetime, end: datetime) -> History:
    """The entity's states from start to end. Raises EntityGone if Home Assistant does not
    know it (any more: its old history would still answer), URLError/TimeoutError if HA is
    down."""
    headers = {"Authorization": f"Bearer {token}"}
    state = Request(f"{base.rstrip('/')}/api/states/{quote(entity)}", headers=headers)
    try:
        urlopen(state, timeout=TIMEOUT).close()
    except HTTPError as e:
        if e.code == 404:
            raise EntityGone(entity) from e
        raise
    request = Request(history_url(base, entity, start, end), headers=headers)
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


def _seen(state: str | None) -> Seen:
    """home / not_home; anything else (unavailable, unknown, null) is not a fact about her."""
    if state == "home":
        return Seen.HOME
    if state == "not_home":
        return Seen.AWAY
    return Seen.UNKNOWN
