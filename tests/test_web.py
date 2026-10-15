import base64
from datetime import date, datetime
from pathlib import Path
from urllib.error import URLError
from zoneinfo import ZoneInfo

import yaml
from fastapi.testclient import TestClient

from tata.presenza import Change, History, Seen
from tata.web import Settings, create_app
from tests.helpers import RATES_DIR

PASSWORD = "supercazzola"

CONTRACT_FORM = {
    "datore_nome": "Rambaldo Melandri",
    "datore_cf": "MLNRBL40A01D612X",
    "lavoratore_nome": "Carmen Necchi",
    "lavoratore_cf": "NCCCMN95A41D612Y",
    "livello": "BS",
    "paga_oraria": "8,86",
    "assunzione": "2026-10-12",
    "cessazione": "",
    "patrono": "24/06",
    "orario_0": "08:00-08:45, 15:45-18:00",
    "orario_1": "08:00-08:45, 15:45-18:00",
    "orario_2": "08:00-08:45, 15:45-18:00",
    "orario_3": "08:00-08:45, 15:45-18:00",
    "orario_4": "08:00-08:45, 15:45-18:00",
    "orario_5": "",
    "orario_6": "",
}


def basic(password: str) -> dict[str, str]:
    token = base64.b64encode(f"datore:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, rates_dir=RATES_DIR, password=PASSWORD)


def end_of_2026() -> datetime:
    return datetime(2026, 12, 31, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))


def client(tmp_path: Path) -> TestClient:
    app = create_app(settings(tmp_path), end_of_2026, None)
    return TestClient(app, headers=basic(PASSWORD), follow_redirects=False)


def hired(tmp_path: Path) -> TestClient:
    c = client(tmp_path)
    assert c.post("/contratto", data=CONTRACT_FORM).status_code == 303
    return c


def test_wrong_password_is_refused(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), end_of_2026, None)
    assert TestClient(app, headers=basic("sbagliata")).get("/contratto").status_code == 401
    assert TestClient(app).get("/contratto").status_code == 401


def test_without_a_contract_everything_leads_to_the_contract_form(tmp_path: Path) -> None:
    c = client(tmp_path)
    assert c.get("/").headers["location"] == "/contratto"
    assert c.get("/mese/2026/10").headers["location"] == "/contratto"
    assert c.get("/contratto").status_code == 200


def test_month_page_shows_the_payslip(tmp_path: Path) -> None:
    page = hired(tmp_path).get("/mese/2026/10")
    assert page.status_code == 200
    assert "Ore ordinarie" in page.text
    assert "378,45" in page.text  # netto, see test_payslip


def test_contract_form_shows_saved_values(tmp_path: Path) -> None:
    page = hired(tmp_path).get("/contratto")
    assert "Carmen Necchi" in page.text
    assert "08:00-08:45, 15:45-18:00" in page.text


def test_ferie_range_creates_one_event_per_working_day(tmp_path: Path) -> None:
    c = hired(tmp_path)
    r = c.post(
        "/eventi",
        data={
            "kind": "ferie",
            "dal": "2026-10-19",
            "al": "2026-10-25",
            "inizio": "",
            "fine": "",
            "note": "",
        },
    )
    assert r.status_code == 303
    page = c.get("/mese/2026/10")
    assert "Ferie" in page.text
    assert page.text.count("badge ferie") == 6  # Mon-Sat, Sunday skipped


def test_extra_hours_and_delete(tmp_path: Path) -> None:
    c = hired(tmp_path)
    c.post(
        "/eventi",
        data={
            "kind": "extra",
            "dal": "2026-10-16",
            "al": "",
            "inizio": "18:00",
            "fine": "24:00",
            "note": "cena",
        },
    )
    page = c.get("/mese/2026/10")
    assert "Straordinario notturno" in page.text
    event_id = page.text.split('action="/eventi/')[1].split("/")[0]
    assert c.post(f"/eventi/{event_id}/elimina").status_code == 303
    assert "Straordinario notturno" not in c.get("/mese/2026/10").text


def test_invalid_input_is_a_400_with_the_reason(tmp_path: Path) -> None:
    r = hired(tmp_path).post(
        "/eventi",
        data={
            "kind": "ferie",
            "dal": "2026-10-18",
            "al": "2026-10-18",
            "inizio": "",
            "fine": "",
            "note": "",
        },
    )
    # a Sunday-only ferie range is empty
    assert r.status_code == 400
    assert "nessun giorno" in r.text


def test_payslip_pdf(tmp_path: Path) -> None:
    r = hired(tmp_path).get("/mese/2026/10/prospetto.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_finalized_month_refuses_new_events(tmp_path: Path) -> None:
    c = hired(tmp_path)
    assert c.post("/mese/2026/10/finalizza").status_code == 303
    r = c.post(
        "/eventi",
        data={
            "kind": "assenza",
            "dal": "2026-10-13",
            "al": "",
            "inizio": "",
            "fine": "",
            "note": "",
        },
    )
    assert r.status_code == 400
    assert "finalizzato" in r.text


def test_year_page_quarter_and_payment_and_deduction(tmp_path: Path) -> None:
    c = hired(tmp_path)
    page = c.get("/anno/2026")
    assert "318,56" in page.text  # Q4 bollettino, see test_inps
    assert "10/01/2027" in page.text
    r = c.post("/anno/2026/versamenti", data={"trimestre": "4", "pagato_il": "2026-12-30"})
    assert r.status_code == 303
    data = yaml.safe_load(c.get("/anno/2026/deduzioni.yaml").text)
    assert data["importo_deducibile"] == "229.86"
    assert data["versamenti"][0]["pagato_il"] == date(2026, 12, 30)


def test_attestazione_pdf(tmp_path: Path) -> None:
    r = hired(tmp_path).get("/anno/2026/attestazione.pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


def test_tfr_advance(tmp_path: Path) -> None:
    c = hired(tmp_path)
    r = c.post("/anno/2026/anticipi", data={"giorno": "2026-12-20", "importo": "29,45"})
    assert r.status_code == 303
    assert "100,00" in c.get("/anno/2026").text  # fund 129,45 - 29,45


def add(c: TestClient, kind: str, dal: str, al: str, inizio: str, fine: str) -> int:
    data = {"kind": kind, "dal": dal, "al": al, "inizio": inizio, "fine": fine, "note": ""}
    return c.post("/eventi", data=data).status_code


def test_conflicting_event_is_refused_at_insert_and_the_month_stays_usable(tmp_path: Path) -> None:
    c = hired(tmp_path)
    assert add(c, "ferie", "2026-11-02", "2026-11-06", "", "") == 303
    assert add(c, "malattia", "2026-11-04", "2026-11-05", "", "") == 400
    assert c.get("/mese/2026/11").status_code == 200
    assert c.get("/anno/2026").status_code == 200


def test_timed_event_before_hire_is_refused(tmp_path: Path) -> None:
    c = hired(tmp_path)
    assert add(c, "extra", "2026-10-05", "", "09:00", "10:00") == 400
    assert c.get("/mese/2026/10").status_code == 200


def test_contract_change_that_orphans_events_is_refused(tmp_path: Path) -> None:
    c = hired(tmp_path)
    assert add(c, "extra", "2026-10-16", "", "18:00", "20:00") == 303
    later = {**CONTRACT_FORM, "assunzione": "2026-10-19"}
    assert c.post("/contratto", data=later).status_code == 400
    assert c.get("/mese/2026/10").status_code == 200


def test_months_must_be_finalized_in_order(tmp_path: Path) -> None:
    c = hired(tmp_path)
    r = c.post("/mese/2026/11/finalizza")
    assert r.status_code == 400
    assert "ottobre" in r.text
    assert c.post("/mese/2026/10/finalizza").status_code == 303
    assert c.post("/mese/2026/11/finalizza").status_code == 303


def test_ambiguous_thousands_dot_is_refused(tmp_path: Path) -> None:
    c = hired(tmp_path)
    r = c.post("/anno/2026/anticipi", data={"giorno": "2026-12-20", "importo": "1.500"})
    assert r.status_code == 400
    assert "virgola" in r.text


def test_year_page_marks_future_quarters_as_projection(tmp_path: Path) -> None:
    hired(tmp_path)

    def mid_november() -> datetime:
        return datetime(2026, 11, 15, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))

    c = TestClient(create_app(settings(tmp_path), mid_november, None), headers=basic(PASSWORD))
    page = c.get("/anno/2026")
    assert "stima" in page.text
    # TFR through November only: (398,70 + 580,33) / 13,5 = 72,52
    assert "72,52" in page.text


def rome(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Europe/Rome"))


class FakeClock:
    """A settable clock: punch tests move time forward between taps."""

    def __init__(self, start: datetime) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value


def punching(tmp_path: Path, start: datetime) -> tuple[TestClient, FakeClock]:
    clock = FakeClock(start)
    c = TestClient(
        create_app(settings(tmp_path), clock, None), headers=basic(PASSWORD), follow_redirects=False
    )
    assert c.post("/contratto", data=CONTRACT_FORM).status_code == 303
    return c, clock


def test_button_start_then_stop_with_overrun(tmp_path: Path) -> None:
    # Tue 13/10, schedule 15:45-18:00. In at 15:44 (1 min early, tolerated), out at 18:40.
    c, clock = punching(tmp_path, rome(2026, 10, 13, 15, 44))
    assert "Inizio turno" in c.get("/timbra").text
    assert c.post("/timbra/inizio").headers["location"] == "/timbra"
    clock.value = rome(2026, 10, 13, 18, 40)
    assert "Fine turno" in c.get("/timbra").text
    assert c.post("/timbra/fine", data={"alle": ""}).status_code == 303
    page = c.get("/mese/2026/10").text
    assert "15:44-18:40" in page  # cartellino
    assert "extra 18:00-18:40" in page  # generated event


def test_confirm_records_the_scheduled_span(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 18, 10))
    page = c.get("/timbra").text
    assert "Conferma turno 15:45-18:00" in page
    r = c.post("/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    assert r.headers["location"] == "/timbra"
    assert "Inizio turno" in c.get("/timbra").text  # span recorded, not proposed again


def test_shortfall_is_proposed_and_blocks_finalize_until_confirmed(tmp_path: Path) -> None:
    # Tue 13/10 punched only in the afternoon; the 08:00-08:45 span is pending.
    c, _ = punching(tmp_path, rome(2026, 10, 13, 18, 30))
    c.post("/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    page = c.get("/mese/2026/10").text
    assert "Da confermare" in page and "08:00-08:45" in page
    r = c.post("/mese/2026/10/finalizza")
    assert r.status_code == 400 and "da confermare" in r.text
    c.post(
        "/eventi",
        data={
            "kind": "mora",
            "dal": "2026-10-13",
            "al": "",
            "inizio": "08:00",
            "fine": "08:45",
            "note": "",
        },
    )
    assert "Da confermare" not in c.get("/mese/2026/10").text


def test_month_still_running_cannot_be_finalized(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 31, 12, 0))
    r = c.post("/mese/2026/10/finalizza")
    assert r.status_code == 400 and "non è ancora finito" in r.text


def test_payslip_pays_only_punched_time_from_the_first_punch(tmp_path: Path) -> None:
    # Hired Mon 12/10, no punch that day: paid as scheduled, 3 h. First punch Tue 13/10
    # 15:45-18:00 (2 h 15); its 08:00-08:45 gap, still to confirm, and 14-31/10 are unpaid.
    # Ore ordinarie: 180 + 135 = 315 min = 5,25 h.
    c, _ = punching(tmp_path, rome(2026, 10, 13, 18, 30))
    c.post("/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    assert "<td>Ore ordinarie</td><td>5,25 h</td>" in c.get("/mese/2026/10").text
    c.post(
        "/eventi",
        data={
            "kind": "mora",
            "dal": "2026-10-13",
            "al": "",
            "inizio": "08:00",
            "fine": "08:45",
            "note": "",
        },
    )
    # Sent home 08:00-08:45: paid as scheduled (art. 1206 c.c.), + 45 min = 6,00 h.
    assert "<td>Ore ordinarie</td><td>6,00 h</td>" in c.get("/mese/2026/10").text


def test_stale_open_punch_asks_for_the_exit_time(tmp_path: Path) -> None:
    c, clock = punching(tmp_path, rome(2026, 10, 13, 15, 45))
    c.post("/timbra/inizio")
    clock.value = rome(2026, 10, 14, 7, 55)
    page = c.get("/timbra").text
    assert 'name="alle"' in page and "13/10/2026" in page
    r = c.post("/timbra/fine", data={"alle": ""})
    assert r.status_code == 400 and "ora di uscita" in r.text
    assert c.post("/timbra/fine", data={"alle": "18:00"}).status_code == 303
    assert "15:45-18:00" in c.get("/mese/2026/10").text


def test_stop_in_the_same_minute_is_refused(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 15, 45))
    c.post("/timbra/inizio")
    r = c.post("/timbra/fine", data={"alle": ""})
    assert r.status_code == 400
    assert "Fine turno" in c.get("/timbra").text  # still open, nothing stored


def test_shortfalls_of_a_finalized_month_are_ignored(tmp_path: Path) -> None:
    # First punch Fri 30/10, its morning confirmed as MORA; October finalized Mon 2/11 at 07:00.
    c, clock = punching(tmp_path, rome(2026, 11, 2, 7, 0))
    c.post("/timbrature", data={"giorno": "2026-10-30", "inizio": "15:45", "fine": "18:00"})
    c.post(
        "/eventi",
        data={
            "kind": "mora",
            "dal": "2026-10-30",
            "al": "",
            "inizio": "08:00",
            "fine": "08:45",
            "note": "",
        },
    )
    assert c.post("/mese/2026/10/finalizza").status_code == 303
    clock.value = rome(2026, 11, 3, 9, 0)
    assert "Da confermare" not in c.get("/mese/2026/10").text
    assert "Da confermare" in c.get("/mese/2026/11").text  # 2/11 unpunched, 3/11 morning


def test_deleting_a_punch_removes_its_extra(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 20, 0))
    c.post("/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "19:00"})
    page = c.get("/mese/2026/10").text
    assert "extra 18:00-19:00" in page
    punch_id = page.split('action="/timbrature/')[1].split("/")[0]
    assert c.post(f"/timbrature/{punch_id}/elimina").status_code == 303
    assert "extra 18:00-19:00" not in c.get("/mese/2026/10").text


def test_finalize_refuses_an_open_punch(tmp_path: Path) -> None:
    c, clock = punching(tmp_path, rome(2026, 10, 13, 15, 45))
    c.post("/timbra/inizio")
    clock.value = rome(2026, 10, 13, 18, 0)
    r = c.post("/mese/2026/10/finalizza")
    assert r.status_code == 400 and "timbratura aperta" in r.text
    assert c.post("/timbra/fine", data={"alle": ""}).status_code == 303


def test_punch_page_always_offers_manual_entry(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 12, 0))
    assert "/mese/2026/10?giorno=2026-10-13#cartellino" in c.get("/timbra").text


def test_punch_longer_than_eight_hours_is_highlighted(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 20, 0))
    c.post("/timbrature", data={"giorno": "2026-10-13", "inizio": "07:00", "fine": "16:00"})
    assert 'class="long"' in c.get("/mese/2026/10").text


def test_punch_in_a_finalized_month_is_refused(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 11, 2, 9, 0))
    assert c.post("/mese/2026/10/finalizza").status_code == 303
    r = c.post("/timbrature", data={"giorno": "2026-10-14", "inizio": "15:45", "fine": "18:00"})
    assert r.status_code == 400 and "finalizzato" in r.text


def test_manifest_is_public_and_linked(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), end_of_2026, None)
    # Browsers fetch the manifest and icons without credentials: /static must stay public.
    r = TestClient(app).get("/static/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    manifest = r.json()
    assert manifest["start_url"] == "/timbra"
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert TestClient(app).get(icon["src"]).status_code == 200
    page = hired(tmp_path).get("/timbra").text
    assert 'rel="manifest" href="/static/manifest.webmanifest"' in page


def test_contract_change_recomputes_the_punch_extras(tmp_path: Path) -> None:
    # Tue 13/10 punched 15:45-18:00, exactly the schedule: no EXTRA. The afternoon span then
    # moves to 16:00-18:00: 15:45-16:00 (15 min > TOLERANCE) is now outside the schedule.
    c, _ = punching(tmp_path, rome(2026, 10, 13, 20, 0))
    c.post("/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    assert "extra 15:45-16:00" not in c.get("/mese/2026/10").text
    moved = {**CONTRACT_FORM, "orario_1": "08:00-08:45, 16:00-18:00"}
    assert c.post("/contratto", data=moved).status_code == 303
    assert "extra 15:45-16:00" in c.get("/mese/2026/10").text
    assert c.post("/contratto", data=CONTRACT_FORM).status_code == 303  # and back
    assert "extra 15:45-16:00" not in c.get("/mese/2026/10").text


def test_contract_change_leaving_a_punch_outside_employment_is_refused(tmp_path: Path) -> None:
    # Punch on 13/10 within the schedule (no EXTRA); hiring moved to 19/10 would orphan it.
    c, _ = punching(tmp_path, rome(2026, 10, 13, 20, 0))
    c.post("/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    later = {**CONTRACT_FORM, "assunzione": "2026-10-19"}
    r = c.post("/contratto", data=later)
    assert r.status_code == 400 and "fuori dal rapporto" in r.text
    assert 'value="2026-10-12"' in c.get("/contratto").text  # old contract still in force


def test_punch_on_a_future_day_is_refused(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 20, 0))
    r = c.post("/timbrature", data={"giorno": "2026-10-14", "inizio": "08:00", "fine": "08:45"})
    assert r.status_code == 400 and "futuro" in r.text
    assert 'action="/timbrature/' not in c.get("/mese/2026/10").text  # nothing stored


def test_exit_later_than_now_is_refused(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 15, 45))
    c.post("/timbra/inizio")
    r = c.post("/timbra/fine", data={"alle": "18:00"})  # it is 15:45
    assert r.status_code == 400 and "futuro" in r.text
    assert "Fine turno" in c.get("/timbra").text  # still open, nothing stored


class FakeHomeAssistant:
    """Records what the presence page asks for and answers with a fixed history."""

    def __init__(self, history: History | None) -> None:
        self.history = history
        self.asked: list[tuple[datetime, datetime]] = []

    def __call__(self, start: datetime, end: datetime) -> History:
        self.asked.append((start, end))
        if self.history is None:
            raise URLError("connection refused")
        return self.history


# Thu 15/10: home 07:55, not_home 08:14 (left 08:12); home 16:31, not_home 18:05 (left 18:03).
A_DAY = History(
    initial=Seen.AWAY,
    changes=[
        Change(at=rome(2026, 10, 15, 7, 55), seen=Seen.HOME),
        Change(at=rome(2026, 10, 15, 8, 14), seen=Seen.AWAY),
        Change(at=rome(2026, 10, 15, 16, 31), seen=Seen.HOME),
        Change(at=rome(2026, 10, 15, 18, 5), seen=Seen.AWAY),
    ],
)


def presence_client(tmp_path: Path, clock: datetime, ha: FakeHomeAssistant) -> TestClient:
    c = TestClient(
        create_app(settings(tmp_path), FakeClock(clock), ha),
        headers=basic(PASSWORD),
        follow_redirects=False,
    )
    assert c.post("/contratto", data=CONTRACT_FORM).status_code == 303
    return c


def test_presence_page_proposes_and_records_on_confirmation(tmp_path: Path) -> None:
    ha = FakeHomeAssistant(A_DAY)
    c = presence_client(tmp_path, rome(2026, 10, 15, 19, 0), ha)
    page = c.get("/presenza/2026/10").text
    # Morning: 07:55 to 08:12 + 30 = 08:42 (left by 08:30). Afternoon: 15:45 to 18:03.
    assert "07:55-08:42" in page
    assert "15:45-18:03" in page
    assert ha.asked == [(rome(2026, 10, 1, 0, 0), rome(2026, 10, 15, 19, 0))]
    # Nothing is recorded until confirmed.
    assert "07:55-08:42" not in c.get("/mese/2026/10").text.split('id="cartellino"')[1]
    for inizio, fine in (("07:55", "08:42"), ("15:45", "18:03")):
        r = c.post(
            "/timbrature",
            data={
                "giorno": "2026-10-15",
                "inizio": inizio,
                "fine": fine,
                "torna": "/presenza/2026/10",
            },
        )
        assert r.headers["location"] == "/presenza/2026/10"
    page = c.get("/presenza/2026/10").text
    assert page.count("registrata") == 2
    # Week of 12/10: 47 min + 2 h 18 min = 3:05.
    assert "3:05" in page
    assert "07:55-08:42" in c.get("/mese/2026/10").text.split('id="cartellino"')[1]


def test_presence_proposal_ending_after_now_cannot_be_confirmed_yet(tmp_path: Path) -> None:
    ha = FakeHomeAssistant(A_DAY)
    page = presence_client(tmp_path, rome(2026, 10, 15, 8, 30), ha).get("/presenza/2026/10")
    assert "07:55-08:42" in page.text
    assert "dalle 8:42" in page.text
    assert 'value="8:42"' not in page.text


def test_presence_page_without_home_assistant(tmp_path: Path) -> None:
    c = presence_client(tmp_path, rome(2026, 10, 15, 19, 0), FakeHomeAssistant(None))
    r = c.get("/presenza/2026/10")
    assert r.status_code == 502
    assert "Home Assistant" in r.text


def test_presence_not_configured(tmp_path: Path) -> None:
    c = TestClient(create_app(settings(tmp_path), end_of_2026, None), headers=basic(PASSWORD))
    assert c.post("/contratto", data=CONTRACT_FORM).status_code == 200
    r = c.get("/presenza/2026/10")
    assert r.status_code == 400
    assert "TATA_HA_URL" in r.text


def test_presence_future_month_asks_nothing(tmp_path: Path) -> None:
    ha = FakeHomeAssistant(A_DAY)
    c = presence_client(tmp_path, rome(2026, 10, 15, 19, 0), ha)
    assert c.get("/presenza/2026/11").status_code == 200
    assert ha.asked == []


def test_return_address_must_be_a_presence_page(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 15, 19, 0))
    r = c.post(
        "/timbrature",
        data={
            "giorno": "2026-10-14",
            "inizio": "16:00",
            "fine": "18:00",
            "torna": "https://evil.example/",
        },
    )
    assert r.headers["location"] == "/mese/2026/10"
