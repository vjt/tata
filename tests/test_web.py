import base64
from datetime import date
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

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


def client(tmp_path: Path) -> TestClient:
    app = create_app(Settings(data_dir=tmp_path, rates_dir=RATES_DIR, password=PASSWORD))
    return TestClient(app, headers=basic(PASSWORD), follow_redirects=False)


def hired(tmp_path: Path) -> TestClient:
    c = client(tmp_path)
    assert c.post("/contratto", data=CONTRACT_FORM).status_code == 303
    return c


def test_wrong_password_is_refused(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, rates_dir=RATES_DIR, password=PASSWORD))
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
