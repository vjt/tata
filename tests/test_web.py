import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import URLError
from zoneinfo import ZoneInfo

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tata.festivita import festivita
from tata.models import Mileage
from tata.presenza import Change, History, Seen
from tata.store import Store
from tata.web import Settings, create_app
from tests.helpers import RATES_DIR

USER = "mascetti"
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
    "ha_entity": "",
}


def logged_in(app: FastAPI, user: str, password: str, follow: bool) -> TestClient:
    """A browser that went through the login page (https: the cookie is Secure)."""
    c = TestClient(app, base_url="https://testserver", follow_redirects=follow)
    form = {"user": user, "password": password, "next": "/"}
    r = c.post("/login", data=form, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


WORKER = ("carmen", "pass-lavoratrice")  # set per rapporto by the employer, see with_worker
# Invented route: 12 km a turno (andata e ritorno), ACI 0,4463 €/km.
MILEAGE = Mileage(km_turno=Decimal(12), euro_km=Decimal("0.4463"))


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        rates_dir=RATES_DIR,
        user=USER,
        password=PASSWORD,
        mileage=MILEAGE,
    )


def end_of_2026() -> datetime:
    return datetime(2026, 12, 31, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))


def client(tmp_path: Path) -> TestClient:
    app = create_app(settings(tmp_path), end_of_2026, None)
    return logged_in(app, USER, PASSWORD, False)


def hired(tmp_path: Path) -> TestClient:
    c = client(tmp_path)
    assert c.post("/rapporti", data=CONTRACT_FORM).status_code == 303
    return c


def worked_as_scheduled(tmp_path: Path, first: date, last: date) -> None:
    """Every scheduled span first..last punched in full (no punch, no pay). Months already
    worked, so not through the web (which refuses future punches); one transaction, because
    a commit per punch (Store.add_punch) made each of these tests take a second."""
    path = tmp_path / "tata.sqlite"
    contract = Store(path).rapporto(1).contract()
    rows = [
        (1, day.isoformat(), s.start, s.end)
        for day in (first + timedelta(days=n) for n in range((last - first).days + 1))
        if day not in festivita(day.year, 6, 24)  # patrono as in CONTRACT_FORM
        for s in contract.orario[day.weekday()]
    ]
    with closing(sqlite3.connect(path)) as db, db:
        db.executemany(
            "INSERT INTO punch (contract_id, day, start, stop) VALUES (?, ?, ?, ?)", rows
        )
    assert len(Store(path).rapporto(1).punches()) >= len(rows)  # the schema still matches


def worked(tmp_path: Path) -> TestClient:
    """Hired 12/10/2026 and worked exactly the schedule to the end of the year."""
    c = hired(tmp_path)
    worked_as_scheduled(tmp_path, date(2026, 10, 12), date(2026, 12, 31))
    return c


def test_login_page_guards_everything(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), end_of_2026, None)
    anon = TestClient(app, base_url="https://testserver", follow_redirects=False)
    r = anon.get("/r/1/contratto")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/r/1/contratto"
    assert anon.post("/r/1/eventi").headers["location"].startswith("/login")
    page = anon.get("/login?next=/r/1/contratto").text
    assert 'name="user"' in page and 'type="password"' in page
    assert "rapporti</a>" not in page  # no nav before login
    for user, password in ((USER, "sbagliata"), ("perozzi", PASSWORD)):
        r = anon.post("/login", data={"user": user, "password": password, "next": "/"})
        assert r.status_code == 401 and "utente o password sbagliati" in r.text
    r = anon.post("/login", data={"user": USER, "password": PASSWORD, "next": "/rapporti"})
    assert r.headers["location"] == "/rapporti"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert anon.get("/rapporti").status_code == 200
    # Elsewhere after login: never another site.
    other = TestClient(app, base_url="https://testserver", follow_redirects=False)
    r = other.post("/login", data={"user": USER, "password": PASSWORD, "next": "//evil.example/"})
    assert r.headers["location"] == "/"
    # Logout ends the session.
    assert anon.post("/logout").headers["location"] == "/login"
    assert anon.get("/rapporti").headers["location"].startswith("/login")


def test_session_expires(tmp_path: Path) -> None:
    clock = FakeClock(rome(2026, 10, 13, 9, 0))
    c = logged_in(create_app(settings(tmp_path), clock, None), USER, PASSWORD, False)
    clock.value = rome(2027, 1, 10, 9, 0)  # 89 days: still in
    assert c.get("/rapporti").status_code == 200
    clock.value = rome(2027, 1, 12, 9, 0)  # past 90 days
    assert c.get("/rapporti").headers["location"].startswith("/login")


def test_without_rapporti_everything_leads_to_the_list(tmp_path: Path) -> None:
    c = client(tmp_path)
    assert c.get("/").headers["location"] == "/rapporti"
    assert c.get("/timbra").headers["location"] == "/rapporti"
    assert c.get("/r/1/mese/2026/10").status_code == 404
    assert "Nuovo rapporto" in c.get("/rapporti").text
    assert c.get("/rapporti/nuovo").status_code == 200


def test_overlapping_rapporti_each_with_its_own_pages(tmp_path: Path) -> None:
    # A colf (rapporto 1) and a babysitter (2) at once; the colf leaves on 31/10 and stays.
    c = hired(tmp_path)
    sitter = {**CONTRACT_FORM, "lavoratore_nome": "Luisa Sassaroli", "assunzione": "2026-10-01"}
    assert c.post("/rapporti", data=sitter).headers["location"] == "/r/2/"
    assert c.get("/").headers["location"] == "/rapporti"  # two open: choose
    assert "Luisa Sassaroli" in c.get("/r/2/mese/2026/10").text
    assert "Luisa Sassaroli" not in c.get("/r/1/mese/2026/10").text
    gone = {**CONTRACT_FORM, "cessazione": "2026-10-31"}
    assert c.post("/r/1/contratto", data=gone).status_code == 303
    listing = c.get("/rapporti").text
    assert listing.index("Luisa Sassaroli") < listing.index("Carmen Necchi")  # open first
    assert "chiuso" in listing
    assert c.get("/r/1/anno/2026").status_code == 200  # closed, still readable
    assert c.get("/").headers["location"] == "/r/2/"  # the only open one


def test_month_page_shows_the_payslip(tmp_path: Path) -> None:
    page = hired(tmp_path).get("/r/1/mese/2026/10")
    assert page.status_code == 200
    assert "Ore ordinarie" in page.text
    assert "378,45" in page.text  # netto, see test_payslip


def test_contract_form_shows_saved_values(tmp_path: Path) -> None:
    page = hired(tmp_path).get("/r/1/contratto")
    assert "Carmen Necchi" in page.text
    assert "08:00-08:45, 15:45-18:00" in page.text


def test_ferie_range_creates_one_event_per_working_day(tmp_path: Path) -> None:
    c = hired(tmp_path)
    r = c.post(
        "/r/1/eventi",
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
    page = c.get("/r/1/mese/2026/10")
    assert "Ferie" in page.text
    assert page.text.count("badge ferie") == 6  # Mon-Sat, Sunday skipped


def test_extra_hours_and_delete(tmp_path: Path) -> None:
    c = hired(tmp_path)
    c.post(
        "/r/1/eventi",
        data={
            "kind": "extra",
            "dal": "2026-10-16",
            "al": "",
            "inizio": "18:00",
            "fine": "24:00",
            "note": "cena",
        },
    )
    page = c.get("/r/1/mese/2026/10")
    assert "Straordinario notturno" in page.text
    event_id = page.text.split('action="/r/1/eventi/')[1].split("/")[0]
    assert c.post(f"/r/1/eventi/{event_id}/elimina").status_code == 303
    assert "Straordinario notturno" not in c.get("/r/1/mese/2026/10").text


def test_invalid_input_is_a_400_with_the_reason(tmp_path: Path) -> None:
    r = hired(tmp_path).post(
        "/r/1/eventi",
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
    r = hired(tmp_path).get("/r/1/mese/2026/10/prospetto.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_finalized_month_refuses_new_events(tmp_path: Path) -> None:
    c = worked(tmp_path)
    assert c.post("/r/1/mese/2026/10/finalizza").status_code == 303
    r = c.post(
        "/r/1/eventi",
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
    c = worked(tmp_path)
    page = c.get("/r/1/anno/2026")
    assert "318,56" in page.text  # Q4 bollettino, see test_inps
    assert "10/01/2027" in page.text
    r = c.post("/r/1/anno/2026/versamenti", data={"trimestre": "4", "pagato_il": "2026-12-30"})
    assert r.status_code == 303
    data = yaml.safe_load(c.get("/anno/2026/deduzioni.yaml").text)
    assert data["importo_deducibile"] == "229.86"
    assert data["versamenti"][0]["pagato_il"] == date(2026, 12, 30)


def test_attestazione_pdf(tmp_path: Path) -> None:
    r = hired(tmp_path).get("/r/1/anno/2026/attestazione.pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


def test_tfr_advance(tmp_path: Path) -> None:
    c = worked(tmp_path)
    r = c.post("/r/1/anno/2026/anticipi", data={"giorno": "2026-12-20", "importo": "28,69"})
    assert r.status_code == 303
    assert "100,00" in c.get("/r/1/anno/2026").text  # fund 128,69 (test_annual) - 28,69


def add(c: TestClient, kind: str, dal: str, al: str, inizio: str, fine: str) -> int:
    data = {"kind": kind, "dal": dal, "al": al, "inizio": inizio, "fine": fine, "note": ""}
    return c.post("/r/1/eventi", data=data).status_code


def test_conflicting_event_is_refused_at_insert_and_the_month_stays_usable(tmp_path: Path) -> None:
    c = hired(tmp_path)
    assert add(c, "ferie", "2026-11-02", "2026-11-06", "", "") == 303
    assert add(c, "malattia", "2026-11-04", "2026-11-05", "", "") == 400
    assert c.get("/r/1/mese/2026/11").status_code == 200
    assert c.get("/r/1/anno/2026").status_code == 200


def test_timed_event_before_hire_is_refused(tmp_path: Path) -> None:
    c = hired(tmp_path)
    assert add(c, "extra", "2026-10-05", "", "09:00", "10:00") == 400
    assert c.get("/r/1/mese/2026/10").status_code == 200


def test_contract_change_that_orphans_events_is_refused(tmp_path: Path) -> None:
    c = hired(tmp_path)
    assert add(c, "extra", "2026-10-16", "", "18:00", "20:00") == 303
    later = {**CONTRACT_FORM, "assunzione": "2026-10-19"}
    assert c.post("/r/1/contratto", data=later).status_code == 400
    assert c.get("/r/1/mese/2026/10").status_code == 200


def test_months_must_be_finalized_in_order(tmp_path: Path) -> None:
    c = worked(tmp_path)
    r = c.post("/r/1/mese/2026/11/finalizza")
    assert r.status_code == 400
    assert "ottobre" in r.text
    assert c.post("/r/1/mese/2026/10/finalizza").status_code == 303
    assert c.post("/r/1/mese/2026/11/finalizza").status_code == 303


def test_ambiguous_thousands_dot_is_refused(tmp_path: Path) -> None:
    c = hired(tmp_path)
    r = c.post("/r/1/anno/2026/anticipi", data={"giorno": "2026-12-20", "importo": "1.500"})
    assert r.status_code == 400
    assert "virgola" in r.text


def test_year_page_marks_future_quarters_as_projection(tmp_path: Path) -> None:
    worked(tmp_path)

    def mid_november() -> datetime:
        return datetime(2026, 11, 15, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))

    c = logged_in(create_app(settings(tmp_path), mid_november, None), USER, PASSWORD, True)
    page = c.get("/r/1/anno/2026")
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
    c = logged_in(create_app(settings(tmp_path), clock, None), USER, PASSWORD, False)
    assert c.post("/rapporti", data=CONTRACT_FORM).status_code == 303
    return c, clock


def test_button_start_then_stop_with_overrun(tmp_path: Path) -> None:
    # Tue 13/10, schedule 15:45-18:00. In at 15:44 (1 min early, tolerated), out at 18:40.
    c, clock = punching(tmp_path, rome(2026, 10, 13, 15, 44))
    assert "Inizio turno" in c.get("/r/1/timbra").text
    assert c.post("/r/1/timbra/inizio").headers["location"] == "/r/1/timbra"
    clock.value = rome(2026, 10, 13, 18, 40)
    assert "Fine turno" in c.get("/r/1/timbra").text
    assert c.post("/r/1/timbra/fine", data={"alle": ""}).status_code == 303
    page = c.get("/r/1/mese/2026/10").text
    assert "15:44-18:40" in page  # cartellino
    assert "extra 18:00-18:40" in page  # generated event


def test_confirm_records_the_scheduled_span(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 18, 10))
    page = c.get("/r/1/timbra").text
    assert "Conferma turno 15:45-18:00" in page
    r = c.post("/r/1/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    assert r.headers["location"] == "/r/1/timbra"
    assert "Inizio turno" in c.get("/r/1/timbra").text  # span recorded, not proposed again


def test_shortfall_is_proposed_and_blocks_finalize_until_confirmed(tmp_path: Path) -> None:
    # Mon 12/10 (hire day) punched only in the afternoon; the 08:00-08:45 span is pending.
    c, _ = punching(tmp_path, rome(2026, 10, 12, 18, 30))
    c.post("/r/1/timbrature", data={"giorno": "2026-10-12", "inizio": "15:45", "fine": "18:00"})
    page = c.get("/r/1/mese/2026/10").text
    assert "Da confermare" in page and "08:00-08:45" in page
    r = c.post("/r/1/mese/2026/10/finalizza")
    assert r.status_code == 400 and "da confermare" in r.text
    c.post(
        "/r/1/eventi",
        data={
            "kind": "mora",
            "dal": "2026-10-12",
            "al": "",
            "inizio": "08:00",
            "fine": "08:45",
            "note": "",
        },
    )
    assert "Da confermare" not in c.get("/r/1/mese/2026/10").text


def test_month_still_running_cannot_be_finalized(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 31, 12, 0))
    worked_as_scheduled(tmp_path, date(2026, 10, 12), date(2026, 10, 30))
    r = c.post("/r/1/mese/2026/10/finalizza")
    assert r.status_code == 400 and "non è ancora finito" in r.text


def test_payslip_pays_only_punched_time(tmp_path: Path) -> None:
    # Hired Mon 12/10, nothing punched that day: no punch, no pay. Tue 13/10 punched
    # 15:45-18:00 = 2 h 15 = 2,25 h; its 08:00-08:45 gap (still to confirm) and 14-31/10 unpaid.
    c, _ = punching(tmp_path, rome(2026, 10, 13, 18, 30))
    c.post("/r/1/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    page = c.get("/r/1/mese/2026/10").text
    assert "<td>Ore ordinarie</td><td>2 h 15 min</td>" in page
    # The 13ma follows: 2,25 h x 8,86 = 19,935 -> 19,94 ; / 12 = 1,66 (art. 39).
    assert "<th>Tredicesima maturata nell'anno</th><td>1,66 €</td>" in page
    # Planned, for comparison: the schedule 12-31/10, 15 working days x 3 h = 45 h.
    assert "<td>Ore ordinarie</td><td>45 h</td>" in page.split("<h2>Pianificato</h2>")[1]
    c.post(
        "/r/1/eventi",
        data={
            "kind": "mora",
            "dal": "2026-10-13",
            "al": "",
            "inizio": "08:00",
            "fine": "08:45",
            "note": "",
        },
    )
    # Sent home 08:00-08:45: paid as scheduled (art. 1206 c.c.), + 45 min = 3,00 h.
    assert "<td>Ore ordinarie</td><td>3 h</td>" in c.get("/r/1/mese/2026/10").text


def test_stale_open_punch_asks_for_the_exit_time(tmp_path: Path) -> None:
    c, clock = punching(tmp_path, rome(2026, 10, 13, 15, 45))
    c.post("/r/1/timbra/inizio")
    clock.value = rome(2026, 10, 14, 7, 55)
    page = c.get("/r/1/timbra").text
    assert 'name="alle"' in page and "13/10/2026" in page
    r = c.post("/r/1/timbra/fine", data={"alle": ""})
    assert r.status_code == 400 and "ora di uscita" in r.text
    assert c.post("/r/1/timbra/fine", data={"alle": "18:00"}).status_code == 303
    assert "15:45-18:00" in c.get("/r/1/mese/2026/10").text


def test_stop_in_the_same_minute_is_refused(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 15, 45))
    c.post("/r/1/timbra/inizio")
    r = c.post("/r/1/timbra/fine", data={"alle": ""})
    assert r.status_code == 400
    assert "Fine turno" in c.get("/r/1/timbra").text  # still open, nothing stored


def test_shortfalls_of_a_finalized_month_are_ignored(tmp_path: Path) -> None:
    # Worked 12-29/10; Fri 30/10 afternoon only, its morning confirmed as MORA; October
    # finalized Mon 2/11 at 07:00.
    c, clock = punching(tmp_path, rome(2026, 11, 2, 7, 0))
    worked_as_scheduled(tmp_path, date(2026, 10, 12), date(2026, 10, 29))
    c.post("/r/1/timbrature", data={"giorno": "2026-10-30", "inizio": "15:45", "fine": "18:00"})
    c.post(
        "/r/1/eventi",
        data={
            "kind": "mora",
            "dal": "2026-10-30",
            "al": "",
            "inizio": "08:00",
            "fine": "08:45",
            "note": "",
        },
    )
    assert c.post("/r/1/mese/2026/10/finalizza").status_code == 303
    clock.value = rome(2026, 11, 3, 9, 0)
    assert "Da confermare" not in c.get("/r/1/mese/2026/10").text
    assert "Da confermare" in c.get("/r/1/mese/2026/11").text  # 2/11 unpunched, 3/11 morning


def test_deleting_a_punch_removes_its_extra(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 20, 0))
    c.post("/r/1/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "19:00"})
    page = c.get("/r/1/mese/2026/10").text
    assert "extra 18:00-19:00" in page
    punch_id = page.split('action="/r/1/timbrature/')[1].split("/")[0]
    assert c.post(f"/r/1/timbrature/{punch_id}/elimina").status_code == 303
    assert "extra 18:00-19:00" not in c.get("/r/1/mese/2026/10").text


def test_finalize_refuses_an_open_punch(tmp_path: Path) -> None:
    c, clock = punching(tmp_path, rome(2026, 10, 13, 15, 45))
    c.post("/r/1/timbra/inizio")
    clock.value = rome(2026, 10, 13, 18, 0)
    r = c.post("/r/1/mese/2026/10/finalizza")
    assert r.status_code == 400 and "timbratura aperta" in r.text
    assert c.post("/r/1/timbra/fine", data={"alle": ""}).status_code == 303


def test_punch_page_always_offers_manual_entry(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 12, 0))
    assert "/r/1/mese/2026/10?giorno=2026-10-13#cartellino" in c.get("/r/1/timbra").text


def test_punch_longer_than_eight_hours_is_highlighted(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 20, 0))
    c.post("/r/1/timbrature", data={"giorno": "2026-10-13", "inizio": "07:00", "fine": "16:00"})
    assert 'class="long"' in c.get("/r/1/mese/2026/10").text


def test_punch_in_a_finalized_month_is_refused(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 11, 2, 9, 0))
    worked_as_scheduled(tmp_path, date(2026, 10, 12), date(2026, 10, 31))
    assert c.post("/r/1/mese/2026/10/finalizza").status_code == 303
    r = c.post("/r/1/timbrature", data={"giorno": "2026-10-18", "inizio": "10:00", "fine": "12:00"})
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
    page = hired(tmp_path).get("/r/1/timbra").text
    assert 'rel="manifest" href="/static/manifest.webmanifest"' in page


def test_contract_change_recomputes_the_punch_extras(tmp_path: Path) -> None:
    # Tue 13/10 punched 15:45-18:00, exactly the schedule: no EXTRA. The afternoon span then
    # moves to 16:00-18:00: 15:45-16:00 (15 min > TOLERANCE) is now outside the schedule.
    c, _ = punching(tmp_path, rome(2026, 10, 13, 20, 0))
    c.post("/r/1/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    assert "extra 15:45-16:00" not in c.get("/r/1/mese/2026/10").text
    moved = {**CONTRACT_FORM, "orario_1": "08:00-08:45, 16:00-18:00"}
    assert c.post("/r/1/contratto", data=moved).status_code == 303
    assert "extra 15:45-16:00" in c.get("/r/1/mese/2026/10").text
    assert c.post("/r/1/contratto", data=CONTRACT_FORM).status_code == 303  # and back
    assert "extra 15:45-16:00" not in c.get("/r/1/mese/2026/10").text


def test_contract_change_leaving_a_punch_outside_employment_is_refused(tmp_path: Path) -> None:
    # Punch on 13/10 within the schedule (no EXTRA); hiring moved to 19/10 would orphan it.
    c, _ = punching(tmp_path, rome(2026, 10, 13, 20, 0))
    c.post("/r/1/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"})
    later = {**CONTRACT_FORM, "assunzione": "2026-10-19"}
    r = c.post("/r/1/contratto", data=later)
    assert r.status_code == 400 and "fuori dal rapporto" in r.text
    assert 'value="2026-10-12"' in c.get("/r/1/contratto").text  # old contract still in force


def test_punch_on_a_future_day_is_refused(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 20, 0))
    r = c.post("/r/1/timbrature", data={"giorno": "2026-10-14", "inizio": "08:00", "fine": "08:45"})
    assert r.status_code == 400 and "futuro" in r.text
    assert 'action="/r/1/timbrature/' not in c.get("/r/1/mese/2026/10").text  # nothing stored


def test_exit_later_than_now_is_refused(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 15, 45))
    c.post("/r/1/timbra/inizio")
    r = c.post("/r/1/timbra/fine", data={"alle": "18:00"})  # it is 15:45
    assert r.status_code == 400 and "futuro" in r.text
    assert "Fine turno" in c.get("/r/1/timbra").text  # still open, nothing stored


class FakeHomeAssistant:
    """Records what the presence page asks for and answers with a fixed history."""

    def __init__(self, history: History | None) -> None:
        self.history = history
        self.asked: list[tuple[datetime, datetime]] = []

    def __call__(self, entity: str, start: datetime, end: datetime) -> History:
        assert entity == TRACKER  # the rapporto's own tracker
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


TRACKER = "device_tracker.necchi"


def presence_client(tmp_path: Path, clock: datetime, ha: FakeHomeAssistant) -> TestClient:
    c = logged_in(create_app(settings(tmp_path), FakeClock(clock), ha), USER, PASSWORD, False)
    form = {**CONTRACT_FORM, "ha_entity": TRACKER}
    assert c.post("/rapporti", data=form).status_code == 303
    return c


def test_presence_page_proposes_and_records_on_confirmation(tmp_path: Path) -> None:
    ha = FakeHomeAssistant(A_DAY)
    c = presence_client(tmp_path, rome(2026, 10, 15, 19, 0), ha)
    page = c.get("/r/1/presenza/2026/10").text
    # Morning: 07:55 to 08:12 + 30 = 08:42 (left by 08:30). Afternoon: 15:45 to 18:03.
    assert "07:55-08:42" in page
    assert "15:45-18:03" in page
    assert ha.asked == [(rome(2026, 10, 1, 0, 0), rome(2026, 10, 15, 19, 0))]
    # Nothing is recorded until confirmed.
    assert "07:55-08:42" not in c.get("/r/1/mese/2026/10").text.split('id="cartellino"')[1]
    for inizio, fine in (("07:55", "08:42"), ("15:45", "18:03")):
        r = c.post(
            "/r/1/timbrature",
            data={
                "giorno": "2026-10-15",
                "inizio": inizio,
                "fine": fine,
                "torna": "/r/1/presenza/2026/10",
            },
        )
        assert r.headers["location"] == "/r/1/presenza/2026/10"
    page = c.get("/r/1/presenza/2026/10").text
    assert page.count("registrata") == 2
    # Week of 12/10: 47 min + 2 h 18 min = 3:05.
    assert "3:05" in page
    assert "07:55-08:42" in c.get("/r/1/mese/2026/10").text.split('id="cartellino"')[1]


def test_presence_proposal_ending_after_now_cannot_be_confirmed_yet(tmp_path: Path) -> None:
    ha = FakeHomeAssistant(A_DAY)
    page = presence_client(tmp_path, rome(2026, 10, 15, 8, 30), ha).get("/r/1/presenza/2026/10")
    assert "07:55-08:42" in page.text
    assert "dalle 8:42" in page.text
    assert 'value="8:42"' not in page.text


def test_presence_page_without_home_assistant(tmp_path: Path) -> None:
    c = presence_client(tmp_path, rome(2026, 10, 15, 19, 0), FakeHomeAssistant(None))
    r = c.get("/r/1/presenza/2026/10")
    assert r.status_code == 502
    assert "Home Assistant" in r.text


def test_presence_not_configured(tmp_path: Path) -> None:
    c = logged_in(create_app(settings(tmp_path), end_of_2026, None), USER, PASSWORD, True)
    assert c.post("/rapporti", data=CONTRACT_FORM).status_code == 200
    r = c.get("/r/1/presenza/2026/10")
    assert r.status_code == 400
    assert "TATA_HA_URL" in r.text


def test_presence_needs_the_rapporto_tracker(tmp_path: Path) -> None:
    ha = FakeHomeAssistant(A_DAY)
    c, _ = punching(tmp_path, rome(2026, 10, 15, 19, 0))
    c = logged_in(create_app(settings(tmp_path), end_of_2026, ha), USER, PASSWORD, False)
    r = c.get("/r/1/presenza/2026/10")
    assert r.status_code == 400 and "tracker" in r.text
    assert ha.asked == []


def test_presence_future_month_asks_nothing(tmp_path: Path) -> None:
    ha = FakeHomeAssistant(A_DAY)
    c = presence_client(tmp_path, rome(2026, 10, 15, 19, 0), ha)
    assert c.get("/r/1/presenza/2026/11").status_code == 200
    assert ha.asked == []


def test_return_address_must_be_a_presence_page(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 15, 19, 0))
    r = c.post(
        "/r/1/timbrature",
        data={
            "giorno": "2026-10-14",
            "inizio": "16:00",
            "fine": "18:00",
            "torna": "https://evil.example/",
        },
    )
    assert r.headers["location"] == "/r/1/mese/2026/10"


def with_worker(c: TestClient, cid: int, login: tuple[str, str]) -> TestClient:
    """The employer gives the worker of rapporto `cid` her login; her client."""
    user, password = login
    form = {"user": user, "password": password}
    assert c.post(f"/r/{cid}/accesso", data=form).status_code == 303
    app = c.app
    assert isinstance(app, FastAPI)
    return logged_in(app, user, password, False)


def test_worker_reads_the_month_and_punches_but_nothing_else(tmp_path: Path) -> None:
    c, _ = punching(tmp_path, rome(2026, 10, 13, 18, 30))
    w = with_worker(c, 1, WORKER)
    assert w.get("/").headers["location"] == "/r/1/"
    assert w.get("/timbra").headers["location"] == "/r/1/timbra"
    assert (
        w.post(
            "/r/1/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "18:00"}
        ).status_code
        == 303
    )
    page = w.get("/r/1/mese/2026/10").text
    assert "15:45-18:00" in page and "prospetto paga" in page
    # No employer controls on her page: events, confirmations, deletions, finalize, nav.
    for control in (
        'action="/r/1/eventi"',
        "/elimina",
        "/finalizza",
        'href="/r/1/contratto"',
        'href="/r/1/presenza"',
    ):
        assert control not in page
    assert w.get("/r/1/mese/2026/10/prospetto.pdf").status_code == 200
    assert w.get("/r/1/timbra").status_code == 200
    forbidden = [
        ("GET", "/r/1/contratto"),
        ("GET", "/r/1/anno/2026"),
        ("GET", "/anno/2026/deduzioni.yaml"),
        ("GET", "/r/1/presenza/2026/10"),
        ("POST", "/r/1/eventi"),
        ("POST", "/r/1/mese/2026/10/finalizza"),
        ("POST", "/r/1/timbrature/1/elimina"),
        ("POST", "/r/1/contratto"),
        ("POST", "/r/1/accesso"),
        ("GET", "/rapporti"),
        ("POST", "/rapporti"),
    ]
    for method, path in forbidden:
        assert w.request(method, path).status_code == 403, path
    # The employer still sees everything.
    assert 'action="/r/1/eventi"' in c.get("/r/1/mese/2026/10").text


def test_worker_password_is_hers_alone(tmp_path: Path) -> None:
    c = hired(tmp_path)
    sitter = {**CONTRACT_FORM, "lavoratore_nome": "Luisa Sassaroli"}
    c.post("/rapporti", data=sitter)
    carmen = with_worker(c, 1, WORKER)
    assert "Entra come <b>carmen</b>" in c.get("/r/1/contratto").text
    # Her login opens her rapporto only.
    assert carmen.get("/r/1/mese/2026/10").status_code == 200
    assert carmen.get("/r/2/mese/2026/10").status_code == 403
    assert carmen.post("/r/2/timbra/inizio").status_code == 403
    # A login name identifies one rapporto: not the employer's, not another worker's.
    for taken in (USER, WORKER[0]):
        r = c.post("/r/2/accesso", data={"user": taken, "password": "altra-password"})
        assert r.status_code == 400
    r = c.post("/r/2/accesso", data={"user": "luisa", "password": "corto"})
    assert r.status_code == 400
    # The same password under another name is fine: the name says who.
    luisa = with_worker(c, 2, ("luisa", WORKER[1]))
    assert luisa.get("/r/2/mese/2026/10").status_code == 200
    assert luisa.get("/r/1/mese/2026/10").status_code == 403
    # Taken away: she is nobody.
    assert c.post("/r/1/accesso", data={"togli": "1"}).status_code == 303
    assert carmen.get("/r/1/mese/2026/10").headers["location"].startswith("/login")


def test_each_turno_carries_its_rimborso(tmp_path: Path) -> None:
    # Tue 13/10: morning, then two afternoon punches (one turno: one trip, not two).
    c, _ = punching(tmp_path, rome(2026, 10, 13, 19, 0))
    for inizio, fine in (("08:00", "08:45"), ("15:45", "16:30"), ("16:40", "18:00")):
        c.post("/r/1/timbrature", data={"giorno": "2026-10-13", "inizio": inizio, "fine": fine})
    page = c.get("/r/1/mese/2026/10").text
    # 12 km x 0,4463 = 5,3556 -> 5,36 a turno; 2 turni = 24 km, 10,72 EUR.
    assert page.count("trasferta 12,00 km, 5,36 €") == 2
    assert "Trasferte del mese: 2 turni, 24,00 km," in page and "rimborso 10,72 €" in page
    # Outside the lordo: netto + rimborso = da pagare.
    slip = page.split("Lavorato")[1]
    netto = slip.split('<td colspan="3">Netto</td><td>')[1].split(" €")[0]
    da_pagare = slip.split('<td colspan="3">Da pagare</td><td>')[1].split(" €")[0]
    assert italian(da_pagare) - italian(netto) == Decimal("10.72")
    # The afternoon was done with the employer's car: no rimborso for it.
    r = c.post("/r/1/turni/auto", data={"giorno": "2026-10-13", "mattina": "0", "datore": "1"})
    assert r.status_code == 303
    page = c.get("/r/1/mese/2026/10").text
    assert page.count("trasferta 12,00 km, 5,36 €") == 1
    assert "auto del datore, niente trasferta" in page
    assert "Trasferte del mese: 1 turni, 12,00 km," in page and "rimborso 5,36 €" in page
    # And back to hers.
    c.post("/r/1/turni/auto", data={"giorno": "2026-10-13", "mattina": "0", "datore": "0"})
    assert "rimborso 10,72 €" in c.get("/r/1/mese/2026/10").text
    # Only the employer says whose car it was.
    w = with_worker(c, 1, WORKER)
    assert w.post("/r/1/turni/auto", data={}).status_code == 403


def italian(amount: str) -> Decimal:
    return Decimal(amount.replace(".", "").replace(",", "."))


def test_payslip_hours_are_shown_as_hours_and_minutes(tmp_path: Path) -> None:
    # 15:45-17:22 = 97 min, kept as 1,62 h (97,2 min): shown as 1 h 37 min, INPS rows too.
    c, _ = punching(tmp_path, rome(2026, 10, 13, 19, 0))
    c.post("/r/1/timbrature", data={"giorno": "2026-10-13", "inizio": "15:45", "fine": "17:22"})
    page = c.get("/r/1/mese/2026/10").text.split("Lavorato")[1]
    assert "<td>Ore ordinarie</td><td>1 h 37 min</td>" in page
    assert "Contributi INPS a carico lavoratore (1 h 37 min " in page
