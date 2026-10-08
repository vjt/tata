"""FastAPI app: server-rendered pages, one password, no JavaScript."""

# Route handlers are registered by decorator and never called by name; pyright cannot see that.
# pyright: reportUnusedFunction=false

import calendar
import os
import re
import secrets
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from tata.annual import (
    PayslipSource,
    attestazione,
    deduzione,
    deduzione_yaml,
    employed_months,
    tfr,
    year_payslips,
)
from tata.festivita import festivita
from tata.hours import month_work, range_days
from tata.inps import quarter_contributions, quarter_months
from tata.models import Contract, Event, EventKind, Frozen, Livello, Payment, Span
from tata.payslip import Payslip, compute_payslip
from tata.pdf import render_pdf
from tata.rates import RatesBook
from tata.store import Store

HERE = Path(__file__).parent
MESI = ("", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre")  # fmt: skip
GIORNI = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
ROME = ZoneInfo("Europe/Rome")


class Settings(Frozen):
    data_dir: Path
    rates_dir: Path
    password: str


Clock = Callable[[], date]


def rome_today() -> date:
    return datetime.now(ROME).date()


def app_from_env() -> FastAPI:
    """The only place that reads the environment (uvicorn --factory entry point)."""
    return create_app(
        Settings(
            data_dir=Path(os.environ["TATA_DATA_DIR"]),
            rates_dir=Path(os.environ["TATA_RATES_DIR"]),
            password=os.environ["TATA_PASSWORD"],
        ),
        rome_today,
    )


def create_app(settings: Settings, today: Clock) -> FastAPI:
    if not settings.password:
        raise ValueError("TATA_PASSWORD vuota")
    store = Store(settings.data_dir / "tata.sqlite")
    book = RatesBook(settings.rates_dir)
    templates = _templates()
    basic = HTTPBasic()

    def auth(credentials: Annotated[HTTPBasicCredentials, Depends(basic)]) -> None:
        if not secrets.compare_digest(credentials.password.encode(), settings.password.encode()):
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    app = FastAPI(dependencies=[Depends(auth)], docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.exception_handler(ValueError)
    @app.exception_handler(FileNotFoundError)
    async def invalid(request: Request, exc: Exception) -> HTMLResponse:
        """Bad input or a missing rates file: show the reason, never a blank 500."""
        return templates.TemplateResponse(
            request, "errore.html", {"message": str(exc)}, status_code=400
        )

    @app.exception_handler(KeyError)
    async def not_found(request: Request, exc: KeyError) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "errore.html", {"message": f"non trovato: {exc}"}, status_code=404
        )

    def require_contract() -> Contract:
        contract = store.contract()
        if contract is None:
            raise _Redirect("/contratto")
        return contract

    @app.exception_handler(_Redirect)
    async def redirect(request: Request, exc: _Redirect) -> RedirectResponse:
        return RedirectResponse(exc.location, status_code=303)

    def check_events(contract: Contract, events: list[Event]) -> None:
        """Raise ValueError now, at write time, rather than bricking a month page later."""
        for year, month in sorted({(e.day.year, e.day.month) for e in events}):
            month_work(contract, events, year, month, book.get(year))

    def source(contract: Contract) -> PayslipSource:
        events = store.events()

        def get(year: int, month: int) -> Payslip:
            final = store.final_payslip(year, month)
            if final is not None:
                return final
            return compute_payslip(contract, events, year, month, book.get(year))

        return get

    @app.get("/")
    def home() -> RedirectResponse:
        require_contract()
        now = today()
        return RedirectResponse(f"/mese/{now.year}/{now.month}", status_code=303)

    # ---- contract

    @app.get("/contratto", response_class=HTMLResponse)
    def contract_form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "contratto.html",
            {"contract": store.contract(), "livelli": list(Livello), "giorni": GIORNI},
        )

    @app.post("/contratto")
    async def contract_save(request: Request) -> RedirectResponse:
        form = await request.form()
        day, month = (int(x) for x in _field(form, "patrono").split("/"))
        cessazione = _field(form, "cessazione")
        contract = Contract(
            datore_nome=_field(form, "datore_nome"),
            datore_cf=_field(form, "datore_cf").upper(),
            lavoratore_nome=_field(form, "lavoratore_nome"),
            lavoratore_cf=_field(form, "lavoratore_cf").upper(),
            livello=Livello(_field(form, "livello")),
            paga_oraria=_decimal(_field(form, "paga_oraria")),
            tab_h="tab_h" in form,
            assunzione=date.fromisoformat(_field(form, "assunzione")),
            cessazione=date.fromisoformat(cessazione) if cessazione else None,
            patrono_mese=month,
            patrono_giorno=day,
            orario=tuple(_spans(_field(form, f"orario_{d}")) for d in range(7)),
        )
        check_events(contract, store.events())
        store.save_contract(contract)
        return RedirectResponse("/", status_code=303)

    # ---- month

    @app.get("/mese/{year}/{month}", response_class=HTMLResponse)
    def month_page(request: Request, year: int, month: int) -> HTMLResponse:
        contract = require_contract()
        events = store.events()
        rates = book.get(year)
        slip = source(contract)(year, month)
        work = {d.day: d for d in month_work(contract, events, year, month, rates)}
        weeks = calendar.Calendar().monthdatescalendar(year, month)
        month_events = [e for e in events if (e.day.year, e.day.month) == (year, month)]
        prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
        next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
        holidays = festivita(year, contract.patrono_mese, contract.patrono_giorno)
        return templates.TemplateResponse(
            request,
            "mese.html",
            {
                "year": year,
                "month": month,
                "mese": MESI[month],
                "weeks": weeks,
                "work": work,
                "holidays": holidays,
                "events": month_events,
                "kinds": list(EventKind),
                "slip": slip,
                "final": store.final_payslip(year, month) is not None,
                "tfr": tfr(contract, store.advances(), year, month, source(contract), book),
                "prev": f"/mese/{prev_y}/{prev_m}",
                "next": f"/mese/{next_y}/{next_m}",
                "giorni": GIORNI,
                "selected": request.query_params.get("giorno", date(year, month, 1).isoformat()),
            },
        )

    @app.get("/mese/{year}/{month}/prospetto.pdf")
    def payslip_pdf(request: Request, year: int, month: int) -> Response:
        contract = require_contract()
        slip = source(contract)(year, month)
        html = templates.get_template("prospetto_pdf.html").render(
            slip=slip,
            mese=MESI[month],
            tfr=tfr(contract, store.advances(), year, month, source(contract), book),
            css=(HERE / "static" / "pdf.css").read_text(),
        )
        return _pdf(render_pdf(html), f"prospetto-{year}-{month:02d}.pdf")

    @app.post("/mese/{year}/{month}/finalizza")
    def finalize(year: int, month: int) -> RedirectResponse:
        contract = require_contract()
        py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
        if employed_months(contract, py, pm)[-1:] == [pm] and store.final_payslip(py, pm) is None:
            raise ValueError(f"finalizza prima {MESI[pm]} {py}: i mesi si chiudono in ordine")
        store.finalize(compute_payslip(contract, store.events(), year, month, book.get(year)))
        return RedirectResponse(f"/mese/{year}/{month}", status_code=303)

    # ---- events

    @app.post("/eventi")
    async def add_event(request: Request) -> RedirectResponse:
        contract = require_contract()
        form = await request.form()
        kind = EventKind(_field(form, "kind"))
        first = date.fromisoformat(_field(form, "dal"))
        last_text = _field(form, "al")
        last = date.fromisoformat(last_text) if last_text else first
        start, end = _field(form, "inizio"), _field(form, "fine")
        span = Span.parse(f"{start}-{end}") if start or end else None
        days = [first] if span is not None else range_days(contract, kind, first, last)
        if span is not None and last != first:
            raise ValueError("un orario vale per un giorno solo")
        if not days:
            raise ValueError("nessun giorno dell'intervallo è utilizzabile per questo evento")
        note = _field(form, "note")
        drafts = [Event(id=0, day=day, kind=kind, span=span, note=note) for day in days]
        check_events(contract, store.events() + drafts)
        for day in days:
            store.add_event(day, kind, span, note)
        return RedirectResponse(f"/mese/{first.year}/{first.month}", status_code=303)

    @app.post("/eventi/{event_id}/elimina")
    def delete_event(event_id: int) -> RedirectResponse:
        day = store.delete_event(event_id)
        return RedirectResponse(f"/mese/{day.year}/{day.month}", status_code=303)

    # ---- year

    @app.get("/anno")
    def this_year() -> RedirectResponse:
        return RedirectResponse(f"/anno/{today().year}", status_code=303)

    @app.get("/anno/{year}", response_class=HTMLResponse)
    def year_page(request: Request, year: int) -> HTMLResponse:
        contract = require_contract()
        src = source(contract)
        months = employed_months(contract, year, 12)
        quarters = [
            quarter_contributions([src(year, m) for m in quarter_months(q) if m in months], year, q)
            for q in (1, 2, 3, 4)
            if any(m in months for m in quarter_months(q))
        ]
        paid = {(p.year, p.quarter): p for p in store.payments()}
        now = today()
        # Months after today are a projection from the schedule: TFR stops at today's month.
        current = 12 if year < now.year else (0 if year > now.year else now.month)
        last_month = min(months[-1] if months else 12, max(current, 1))
        projected = {q.quarter for q in quarters if quarter_months(q.quarter)[-1] > current}
        return templates.TemplateResponse(
            request,
            "anno.html",
            {
                "year": year,
                "quarters": quarters,
                "paid": paid,
                "projected": projected,
                "slips": year_payslips(contract, year, 12, src),
                "current": current,
                "tfr": tfr(contract, store.advances(), year, last_month, src, book),
                "advances": [a for a in store.advances() if a.day.year == year],
                "mesi": MESI,
            },
        )

    @app.post("/anno/{year}/versamenti")
    async def record_payment(request: Request, year: int) -> RedirectResponse:
        contract = require_contract()
        form = await request.form()
        quarter = int(_field(form, "trimestre"))
        src = source(contract)
        months = employed_months(contract, year, 12)
        q = quarter_contributions(
            [src(year, m) for m in quarter_months(quarter) if m in months], year, quarter
        )
        store.record_payment(
            Payment(
                year=year,
                quarter=quarter,
                paid_on=date.fromisoformat(_field(form, "pagato_il")),
                inps_datore=q.inps_datore,
                inps_lavoratore=q.inps_lavoratore,
                cassa_colf=q.cassa_totale,
                totale=q.totale,
            )
        )
        return RedirectResponse(f"/anno/{year}", status_code=303)

    @app.post("/anno/{year}/anticipi")
    async def add_advance(request: Request, year: int) -> RedirectResponse:
        form = await request.form()
        store.add_advance(
            date.fromisoformat(_field(form, "giorno")), _decimal(_field(form, "importo"))
        )
        return RedirectResponse(f"/anno/{year}", status_code=303)

    @app.get("/anno/{year}/attestazione.pdf")
    def attestazione_pdf(year: int) -> Response:
        contract = require_contract()
        html = templates.get_template("attestazione_pdf.html").render(
            att=attestazione(contract, year, source(contract)),
            mesi=MESI,
            css=(HERE / "static" / "pdf.css").read_text(),
        )
        return _pdf(render_pdf(html), f"attestazione-{year}.pdf")

    @app.get("/anno/{year}/deduzioni.yaml")
    def deduction_yaml(year: int) -> PlainTextResponse:
        require_contract()
        d = deduzione(store.payments(), year, book.get(year))
        return PlainTextResponse(
            deduzione_yaml(d),
            media_type="application/yaml",
            headers={"Content-Disposition": f'attachment; filename="deduzioni-{year}.yaml"'},
        )

    return app


class _Redirect(Exception):
    def __init__(self, location: str) -> None:
        super().__init__(location)
        self.location = location


def _templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["eur"] = _fmt_eur
    templates.env.filters["num"] = _fmt_num
    templates.env.filters["dmy"] = _fmt_dmy
    templates.env.filters["ore"] = _fmt_ore
    return templates


def _fmt_num(value: Decimal) -> str:
    """Italian number format, two decimals: 1.234,56"""
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _fmt_dmy(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _fmt_ore(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


def _fmt_eur(value: Decimal) -> str:
    return f"{_fmt_num(value)} €"


def _field(form: FormData, name: str) -> str:
    value = form.get(name)
    if not isinstance(value, str):
        raise ValueError(f"campo mancante: {name}")
    return value.strip()


def _decimal(text: str) -> Decimal:
    if "," not in text and re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
        raise ValueError(f"{text!r} è ambiguo: usa la virgola per i decimali (1.500,00 o 1,50)")
    try:
        return Decimal(text.replace(".", "").replace(",", ".") if "," in text else text)
    except InvalidOperation as e:
        raise ValueError(f"numero non valido: {text!r}") from e


def _spans(text: str) -> tuple[Span, ...]:
    return tuple(Span.parse(part) for part in text.split(",") if part.strip())


def _pdf(content: bytes, filename: str) -> Response:
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
