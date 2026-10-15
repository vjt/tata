"""FastAPI app: server-rendered pages, one password, no JavaScript."""

# Route handlers are registered by decorator and never called by name; pyright cannot see that.
# pyright: reportUnusedFunction=false

import calendar
import os
import re
import secrets
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated
from urllib.error import URLError
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
from tata.homeassistant import fetch_history
from tata.hours import month_work, range_days
from tata.inps import quarter_contributions, quarter_months
from tata.models import (
    Contract,
    Event,
    EventKind,
    Frozen,
    Livello,
    Mileage,
    Payment,
    Punch,
    Span,
)
from tata.payslip import Payslip, compute_payslip, rimborso_line, trip_cost
from tata.pdf import render_pdf
from tata.presenza import History, presence_days
from tata.rates import RatesBook
from tata.store import Store
from tata.timbratura import (
    Action,
    Shortfall,
    check_punches,
    overruns,
    propose,
    shortfalls,
    turni,
    unpunched,
)

HERE = Path(__file__).parent
MESI = ("", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre")  # fmt: skip
GIORNI = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
ROME = ZoneInfo("Europe/Rome")


class Settings(Frozen):
    data_dir: Path
    rates_dir: Path
    password: str  # the employer: everything
    worker_password: str  # the worker: reads the month, punches; empty = no worker access
    mileage: Mileage | None  # rimborso trasferte per turno; None = no rimborso


Clock = Callable[[], datetime]
PresenceSource = Callable[[datetime, datetime], History]  # wifi presence from start to end
HA_VARS = ("TATA_HA_URL", "TATA_HA_TOKEN", "TATA_HA_ENTITY")
MILEAGE_VARS = ("TATA_RIMBORSO_KM", "TATA_RIMBORSO_EURO_KM")
RETURN_TO = re.compile(r"/presenza/\d{4}/\d{1,2}")
WORKER_ROUTES = {  # all the worker may do: read the month and its payslip, punch, by hand too
    "GET": re.compile(r"/|/timbra|/mese/\d{4}/\d{1,2}(/prospetto\.pdf)?"),
    "POST": re.compile(r"/timbra/(inizio|fine)|/timbrature"),
}


def rome_now() -> datetime:
    return datetime.now(ROME)


def app_from_env() -> FastAPI:
    """The only place that reads the environment (uvicorn --factory entry point).

    Wifi presence is optional: all three TATA_HA_* variables, or none. So is the worker's
    access, TATA_WORKER_PASSWORD, and the rimborso trasferte: both TATA_RIMBORSO_*, or none.
    """
    ha = [os.environ.get(name, "") for name in HA_VARS]
    if any(ha) and not all(ha):
        raise ValueError(f"presenza wifi: servono tutte o nessuna tra {', '.join(HA_VARS)}")
    km, euro_km = (os.environ.get(name, "") for name in MILEAGE_VARS)
    if bool(km) != bool(euro_km):
        raise ValueError(f"rimborso trasferte: servono entrambe o nessuna tra {MILEAGE_VARS}")
    return create_app(
        Settings(
            data_dir=Path(os.environ["TATA_DATA_DIR"]),
            rates_dir=Path(os.environ["TATA_RATES_DIR"]),
            password=os.environ["TATA_PASSWORD"],
            worker_password=os.environ.get("TATA_WORKER_PASSWORD", ""),
            mileage=Mileage(km_turno=Decimal(km), euro_km=Decimal(euro_km)) if km else None,
        ),
        rome_now,
        _home_assistant(*ha) if all(ha) else None,
    )


def _home_assistant(base: str, token: str, entity: str) -> PresenceSource:
    def history(start: datetime, end: datetime) -> History:
        return fetch_history(base, token, entity, start, end)

    return history


def create_app(settings: Settings, now: Clock, presence: PresenceSource | None) -> FastAPI:
    if not settings.password:
        raise ValueError("TATA_PASSWORD vuota")
    if settings.worker_password == settings.password:
        raise ValueError("TATA_WORKER_PASSWORD deve essere diversa da TATA_PASSWORD")
    store = Store(settings.data_dir / "tata.sqlite")
    book = RatesBook(settings.rates_dir)
    templates = _templates()
    basic = HTTPBasic()

    def auth(
        request: Request, credentials: Annotated[HTTPBasicCredentials, Depends(basic)]
    ) -> None:
        """Who is asking: the employer, the worker (only WORKER_ROUTES, 403 elsewhere), or 401."""
        given = credentials.password.encode()
        request.state.worker = False
        if secrets.compare_digest(given, settings.password.encode()):
            return
        if settings.worker_password and secrets.compare_digest(
            given, settings.worker_password.encode()
        ):
            request.state.worker = True
            allowed = WORKER_ROUTES.get(request.method)
            if allowed is None or not allowed.fullmatch(request.url.path):
                raise HTTPException(403)
            return
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

    @app.exception_handler(URLError)
    @app.exception_handler(TimeoutError)
    async def unreachable(request: Request, exc: Exception) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "errore.html",
            {"message": f"Home Assistant non raggiungibile: {exc}"},
            status_code=502,
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

    def minute_of(moment: datetime) -> int:
        return moment.hour * 60 + moment.minute

    def pending(contract: Contract, year: int, month: int) -> list[Shortfall]:
        """Shortfalls of an open month; a finalized month has nothing left to confirm."""
        if store.final_payslip(year, month) is not None:
            return []
        return [
            s
            for s in shortfalls(contract, store.events(), store.punches(), now())
            if (s.day.year, s.day.month) == (year, month)
        ]

    def check_employed(contract: Contract, punch: Punch) -> None:
        if not contract.employed_on(punch.day):
            raise ValueError(f"il {punch.day} è fuori dal rapporto di lavoro")

    def save_punch(contract: Contract, draft: Punch) -> None:
        """Validate a closed punch and its EXTRA against everything stored, then write it."""
        if draft.end is None:
            raise ValueError("ora di uscita mancante")
        check_employed(contract, draft)
        moment = now()
        if draft.day > moment.date() or (
            draft.day == moment.date() and draft.end > minute_of(moment)
        ):
            raise ValueError("non si timbra nel futuro: l'uscita è dopo adesso")
        others = [p for p in store.punches() if p.id != draft.id]
        check_punches([*others, draft])
        extras = overruns(contract, draft)
        drafts = [Event(id=0, day=draft.day, kind=EventKind.EXTRA, span=s, note="") for s in extras]
        check_events(contract, store.events() + drafts)
        if draft.id == 0:
            store.add_punch(draft.day, draft.start, draft.end, extras)
        else:
            store.close_punch(draft.id, draft.end, extras)

    # ---- cartellino

    @app.get("/timbra", response_class=HTMLResponse)
    def punch_page(request: Request) -> HTMLResponse:
        contract = require_contract()
        punches = store.punches()
        moment = now()
        open_ = next((p for p in punches if p.end is None), None)
        return templates.TemplateResponse(
            request,
            "timbra.html",
            {
                "proposal": propose(contract, punches, moment),
                "open": open_,
                "stale": open_ is not None and open_.day != moment.date(),
                "today": moment.date(),
                "actions": Action,
            },
        )

    @app.post("/timbra/inizio")
    def punch_in() -> RedirectResponse:
        contract = require_contract()
        moment = now()
        draft = Punch(id=0, day=moment.date(), start=minute_of(moment), end=None)
        check_employed(contract, draft)
        check_punches([*store.punches(), draft])
        store.add_punch(draft.day, draft.start, None, [])
        return RedirectResponse("/timbra", status_code=303)

    @app.post("/timbra/fine")
    async def punch_out(request: Request) -> RedirectResponse:
        contract = require_contract()
        form = await request.form()
        alle, moment = _field(form, "alle"), now()
        open_ = next((p for p in store.punches() if p.end is None), None)
        if open_ is None:
            raise ValueError("nessuna timbratura aperta")
        if alle:
            end = Span.parse(f"00:00-{alle}").end
        elif open_.day == moment.date():
            end = minute_of(moment)
        else:
            raise ValueError(f"indica l'ora di uscita del {open_.day:%d/%m/%Y}")
        # Built explicitly, not model_copy (which skips validation): a same-minute exit must
        # raise the Span error (400) before anything is written.
        save_punch(contract, Punch(id=open_.id, day=open_.day, start=open_.start, end=end))
        return RedirectResponse("/timbra", status_code=303)

    def paid_events(contract: Contract, year: int, month: int) -> list[Event]:
        """The events pay is computed from: the stored ones, and the unpunched time as ASSENZA
        up to the end of the month."""
        events = store.events()
        return events + unpunched(contract, events, store.punches(), year, month)

    def source(contract: Contract) -> PayslipSource:
        def get(year: int, month: int) -> Payslip:
            final = store.final_payslip(year, month)
            if final is not None:
                return final
            events = paid_events(contract, year, month)
            rimborso = None
            if settings.mileage is not None:
                month_turni = turni(store.punches(), store.employer_car(), year, month)
                rimborso = rimborso_line(settings.mileage, sum(t.own_car for t in month_turni))
            return compute_payslip(contract, events, year, month, book.get(year), rimborso)

        return get

    @app.get("/")
    def home() -> RedirectResponse:
        require_contract()
        moment = now()
        return RedirectResponse(f"/mese/{moment.year}/{moment.month}", status_code=303)

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
        # The contract has no history: the EXTRA of every closed punch in an open month is
        # recomputed against the new schedule, validated together with it, saved with it.
        punches = store.punches()
        for p in punches:
            check_employed(contract, p)
        recomputed = {
            p.id: overruns(contract, p)
            for p in punches
            if p.end is not None and store.final_payslip(p.day.year, p.day.month) is None
        }
        links = store.punch_links()
        kept = [e for e in store.events() if links.get(e.id) not in recomputed]
        drafts = [
            Event(id=0, day=p.day, kind=EventKind.EXTRA, span=s, note="")
            for p in punches
            for s in recomputed.get(p.id, [])
        ]
        check_events(contract, kept + drafts)
        store.save_contract(contract, recomputed)
        return RedirectResponse("/", status_code=303)

    # ---- month

    @app.get("/mese/{year}/{month}", response_class=HTMLResponse)
    def month_page(request: Request, year: int, month: int) -> HTMLResponse:
        contract = require_contract()
        events = store.events()
        rates = book.get(year)
        slip = source(contract)(year, month)
        final = store.final_payslip(year, month) is not None
        planned = None if final else compute_payslip(contract, events, year, month, rates, None)
        paid = paid_events(contract, year, month)
        work = {d.day: d for d in month_work(contract, paid, year, month, rates)}
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
                "planned": planned,
                "trips": {
                    t.punch_id: t for t in turni(store.punches(), store.employer_car(), year, month)
                },
                "mileage": settings.mileage,
                "trip_cost": trip_cost(settings.mileage) if settings.mileage else None,
                "punches": [
                    p for p in store.punches() if (p.day.year, p.day.month) == (year, month)
                ],
                "pending": pending(contract, year, month),
                "final": final,
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
        if any(
            p.end is None for p in store.punches() if (p.day.year, p.day.month) == (year, month)
        ):
            raise ValueError(f"c'è una timbratura aperta in {MESI[month]} {year}: chiudila prima")
        if pending(contract, year, month):
            raise ValueError(f"ci sono mancanze da confermare in {MESI[month]} {year}")
        if now().date() < date(year + month // 12, month % 12 + 1, 1):
            raise ValueError(f"{MESI[month]} {year} non è ancora finito")
        store.finalize(source(contract)(year, month))
        return RedirectResponse(f"/mese/{year}/{month}", status_code=303)

    @app.post("/timbrature")
    async def add_punch(request: Request) -> RedirectResponse:
        contract = require_contract()
        form = await request.form()
        day = date.fromisoformat(_field(form, "giorno"))
        s = Span.parse(f"{_field(form, 'inizio')}-{_field(form, 'fine')}")
        save_punch(contract, Punch(id=0, day=day, start=s.start, end=s.end))
        back = form.get("torna")
        if isinstance(back, str) and RETURN_TO.fullmatch(back):
            return RedirectResponse(back, status_code=303)
        if day == now().date():
            return RedirectResponse("/timbra", status_code=303)
        return RedirectResponse(f"/mese/{day.year}/{day.month}", status_code=303)

    @app.post("/turni/auto")
    async def turno_car(request: Request) -> RedirectResponse:
        """Whose car a turno was done with: the employer's pays no rimborso."""
        form = await request.form()
        day = date.fromisoformat(_field(form, "giorno"))
        store.set_employer_car(day, _field(form, "mattina") == "1", _field(form, "datore") == "1")
        return RedirectResponse(f"/mese/{day.year}/{day.month}#cartellino", status_code=303)

    @app.post("/timbrature/{punch_id}/elimina")
    def delete_punch(punch_id: int) -> RedirectResponse:
        day = store.delete_punch(punch_id)
        return RedirectResponse(f"/mese/{day.year}/{day.month}", status_code=303)

    # ---- wifi presence

    @app.get("/presenza")
    def this_month_presence() -> RedirectResponse:
        moment = now()
        return RedirectResponse(f"/presenza/{moment.year}/{moment.month}", status_code=303)

    @app.get("/presenza/{year}/{month}", response_class=HTMLResponse)
    def presence_page(request: Request, year: int, month: int) -> HTMLResponse:
        """Wifi sessions and the punches they suggest; the employer confirms each one."""
        require_contract()
        if presence is None:
            raise ValueError(f"presenza wifi non configurata: imposta {', '.join(HA_VARS)}")
        moment = now()
        first = date(year, month, 1)
        last = min(date(year, month, calendar.monthrange(year, month)[1]), moment.date())
        days = []
        if first <= last:
            start = datetime.combine(first, time(0), ROME)
            end = min(moment, datetime.combine(last + timedelta(days=1), time(0), ROME))
            days = [d for d in presence_days(presence(start, end), first, last) if d.sessions]
        punches = [
            p
            for p in store.punches()
            if (p.day.year, p.day.month) == (year, month) and p.end is not None
        ]
        weeks: dict[date, int] = {}
        for p in punches:
            monday = p.day - timedelta(days=p.day.weekday())
            weeks[monday] = weeks.get(monday, 0) + p.span().end - p.span().start
        return templates.TemplateResponse(
            request,
            "presenza.html",
            {
                "year": year,
                "month": month,
                "mese": MESI[month],
                "days": days,
                "recorded": {(p.day, p.span()) for p in punches},
                "today": moment.date(),
                "minute": minute_of(moment),
                "weeks": sorted(weeks.items()),
                "here": f"/presenza/{year}/{month}",
            },
        )

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
        return RedirectResponse(f"/anno/{now().year}", status_code=303)

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
        moment = now().date()
        # Months after today are a projection from the schedule: TFR stops at today's month.
        current = 12 if year < moment.year else (0 if year > moment.year else moment.month)
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
