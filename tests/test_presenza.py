from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from tata.presenza import Change, History, PresenceDay, Seen, Session, presence_days
from tests.helpers import span

ROME = ZoneInfo("Europe/Rome")
DAY = date(2026, 10, 15)  # Thursday


def at(hhmmss: str) -> datetime:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return datetime(2026, 10, 15, h, m, s, tzinfo=ROME)


def history(initial: Seen, *changes: tuple[str, Seen]) -> History:
    return History(initial=initial, changes=[Change(at=at(t), seen=s) for t, s in changes])


def one_day(h: History) -> PresenceDay:
    [day] = presence_days(h, DAY, DAY)
    return day


HOME, AWAY, UNKNOWN = Seen.HOME, Seen.AWAY, Seen.UNKNOWN


def test_morning_ends_thirty_minutes_after_she_leaves() -> None:
    # Joins the wifi 07:52:40, not_home published 08:19:30 = last seen 08:17:30 (eve's 120 s
    # departure timeout). Proposal: 07:52 to 08:17 + 30 min = 08:47.
    d = one_day(history(AWAY, ("07:52:40", HOME), ("08:19:30", AWAY)))
    assert d.sessions == [Session(arrival=7 * 60 + 52, departure=8 * 60 + 17, lost=False)]
    assert d.proposals == [span("07:52-08:47")]


def test_afternoon_starts_at_sixteen_and_ends_when_she_leaves_without_extra() -> None:
    # Home with the children 16:28:05; not_home 18:04:10 = left 18:02:10. No +30 after 12:00.
    d = one_day(history(AWAY, ("16:28:05", HOME), ("18:04:10", AWAY)))
    assert d.proposals == [span("16:00-18:02")]


def test_afternoon_arrival_before_sixteen_counts_from_the_arrival() -> None:
    d = one_day(history(AWAY, ("15:40:00", HOME), ("18:02:00", AWAY)))
    assert d.proposals == [span("15:40-18:00")]


def test_a_normal_day_gives_two_punches() -> None:
    d = one_day(
        history(
            AWAY,
            ("07:55:00", HOME),
            ("08:20:00", AWAY),
            ("16:31:00", HOME),
            ("18:05:00", AWAY),
        )
    )
    assert d.proposals == [span("07:55-08:48"), span("16:00-18:03")]


def test_second_afternoon_visit_counts_from_its_own_arrival() -> None:
    # Only the first afternoon arrival is backdated to 16:00.
    d = one_day(
        history(
            AWAY,
            ("16:30:00", HOME),
            ("17:10:00", AWAY),
            ("17:40:00", HOME),
            ("18:10:00", AWAY),
        )
    )
    assert d.proposals == [span("16:00-17:08"), span("17:40-18:08")]


def test_overlapping_morning_visits_merge() -> None:
    # Leaves 08:15 (+30 = 08:45), comes back 08:40 to drop a bag, leaves 08:50 (+30 = 09:20).
    d = one_day(
        history(
            AWAY,
            ("07:50:00", HOME),
            ("08:17:00", AWAY),
            ("08:40:00", HOME),
            ("08:52:00", AWAY),
        )
    )
    assert d.proposals == [span("07:50-09:20")]


def test_still_home_gives_no_proposal() -> None:
    d = one_day(history(AWAY, ("16:30:00", HOME)))
    assert d.sessions == [Session(arrival=16 * 60 + 30, departure=None, lost=False)]
    assert d.proposals == []


def test_home_since_before_midnight_gives_no_proposal() -> None:
    # Stale "home" from the previous day (eve keeps a dozing phone home up to 18 h): the
    # arrival is unknown, so nothing is proposed; the employer enters the punch by hand.
    d = one_day(history(HOME, ("08:20:00", AWAY)))
    assert d.sessions == [Session(arrival=None, departure=8 * 60 + 18, lost=False)]
    assert d.proposals == []


def test_restart_blip_while_home_is_not_a_departure() -> None:
    # HA restart: unknown, then eve republishes not_home and, seconds later, home.
    d = one_day(
        history(
            AWAY,
            ("16:30:00", HOME),
            ("17:00:00", UNKNOWN),
            ("17:00:01", AWAY),
            ("17:00:15", HOME),
            ("18:02:00", AWAY),
        )
    )
    assert d.proposals == [span("16:00-18:00")]


def test_departure_during_a_restart_has_no_known_time() -> None:
    # She left while HA was down: not_home after unknown and no home within the grace period.
    d = one_day(history(AWAY, ("16:30:00", HOME), ("17:00:00", UNKNOWN), ("17:00:01", AWAY)))
    assert d.sessions == [Session(arrival=16 * 60 + 30, departure=None, lost=True)]
    assert d.proposals == []


def test_restart_while_away_is_nothing() -> None:
    d = one_day(history(AWAY, ("11:00:00", UNKNOWN), ("11:00:01", AWAY)))
    assert d.sessions == []
    assert d.proposals == []


def test_days_are_split_at_midnight_and_carry_the_state() -> None:
    # Home 21:00 on the 15th, not_home 07:10:00 on the 16th (a stale overnight "home").
    h = History(
        initial=AWAY,
        changes=[
            Change(at=at("21:00:00"), seen=HOME),
            Change(at=at("07:10:00") + timedelta(days=1), seen=AWAY),
        ],
    )
    first, second = presence_days(h, DAY, DAY + timedelta(days=1))
    assert first.sessions == [Session(arrival=21 * 60, departure=None, lost=False)]
    assert second.sessions == [Session(arrival=None, departure=7 * 60 + 8, lost=False)]
    assert first.proposals == second.proposals == []


def test_changes_in_utc_are_read_in_rome_time() -> None:
    # Home Assistant answers in UTC: 05:52 UTC is 07:52 in Rome (CEST, +2).
    h = History(
        initial=AWAY,
        changes=[
            Change(at=datetime(2026, 10, 15, 5, 52, tzinfo=UTC), seen=HOME),
            Change(at=datetime(2026, 10, 15, 6, 19, 30, tzinfo=UTC), seen=AWAY),
        ],
    )
    assert one_day(h).proposals == [span("07:52-08:47")]


def test_quiet_days_are_listed_empty() -> None:
    days = presence_days(History(initial=AWAY, changes=[]), DAY, DAY + timedelta(days=2))
    assert [d.day for d in days] == [DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)]
    assert all(d.sessions == [] for d in days)
