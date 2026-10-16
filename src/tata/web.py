"""FastAPI app: server-rendered pages, a login page (the employer's password or a worker's,
which picks her rapporto) and a session cookie, no JavaScript."""

# Route handlers are registered by decorator and never called by name; pyright cannot see that.
# pyright: reportUnusedFunction=false

import calendar
import hashlib
import os
import re
import secrets
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.error import URLError
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
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
    Session,
    Span,
)
from tata.payslip import Payslip, compute_payslip, rimborso_line, trip_cost
from tata.pdf import render_pdf
from tata.presenza import History, PresenceDay, presence_days
from tata.rates import RatesBook
from tata.store import RapportoStore, Store
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
    user: str  # the employer's login: every rapporto
    password: str
    mileage: Mileage | None  # rimborso trasferte per turno; None = no rimborso


Clock = Callable[[], datetime]
# Wifi presence of a Home Assistant tracker (each rapporto names its own) from start to end.
PresenceSource = Callable[[str, datetime, datetime], History]
HA_VARS = ("TATA_HA_URL", "TATA_HA_TOKEN")
MILEAGE_VARS = ("TATA_RIMBORSO_KM", "TATA_RIMBORSO_EURO_KM")
RETURN_TO = re.compile(r"/r/\d+/(presenza|mese)/\d{4}/\d{1,2}")
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
COOKIE = "tata_sessione"
SESSION = timedelta(days=90)  # the PWA on her phone should not ask every week
SAFE_NEXT = re.compile(r"/(?!/)[\w/.\-]*")  # after login: a path of this site, nothing else


def worker_routes(cid: int) -> dict[str, re.Pattern[str]]:
    """All a worker may do, in her rapporto only: read the month and its payslip, punch."""
    base = f"/r/{cid}"
    return {
        "GET": re.compile(rf"/|/timbra|{base}/|{base}/timbra|{base}/mese/\d{{4}}/\d{{1,2}}"
                          rf"(/prospetto\.pdf)?"),
        "POST": re.compile(rf"/logout|{base}/timbra/(inizio|fine)|{base}/timbrature"),
    }  # fmt: skip


class WifiBox(Frozen):
    """The month page's wifi proposals still to record, or why there are none to show."""

    days: list[PresenceDay]
    error: str


def closed_punches(rs: RapportoStore, year: int, month: int) -> list[Punch]:
    return [
        p for p in rs.punches() if (p.day.year, p.day.month) == (year, month) and p.end is not None
    ]


def covered(day: date, proposal: Span, punches: list[Punch]) -> bool:
    """A punch that day overlaps the proposal: recorded, perhaps with corrected times."""
    return any(
        p.day == day and p.start < proposal.end and proposal.start < p.span().end for p in punches
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    _, salt, digest = stored.split("$")
    given = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **SCRYPT)
    return secrets.compare_digest(given, bytes.fromhex(digest))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def rome_now() -> datetime:
    return datetime.now(ROME)


def app_from_env() -> FastAPI:
    """The only place that reads the environment (uvicorn --factory entry point).

    Wifi presence is optional: both TATA_HA_* variables, or none (each rapporto names its
    tracker). So is the rimborso trasferte: both TATA_RIMBORSO_*, or none.
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
            user=os.environ["TATA_USER"],
            password=os.environ["TATA_PASSWORD"],
            mileage=Mileage(km_turno=Decimal(km), euro_km=Decimal(euro_km)) if km else None,
        ),
        rome_now,
        _home_assistant(*ha) if all(ha) else None,
    )


def _home_assistant(base: str, token: str) -> PresenceSource:
    def history(entity: str, start: datetime, end: datetime) -> History:
        return fetch_history(base, token, entity, start, end)

    return history


def create_app(settings: Settings, now: Clock, presence: PresenceSource | None) -> FastAPI:
    if not settings.user or not settings.password:
        raise ValueError("TATA_USER o TATA_PASSWORD vuota")
    store = Store(settings.data_dir / "tata.sqlite")
    book = RatesBook(settings.rates_dir)
    templates = _templates()

    def credential_tag(contract_id: int | None) -> str | None:
        """What a session's credential must still be: the employer's login, or the worker's
        current hash. None: she no longer has access."""
        if contract_id is None:
            return digest(f"{settings.user}\n{settings.password}")
        stored = store.worker_hashes().get(contract_id)
        return None if stored is None else digest(stored)

    def auth(request: Request) -> None:
        """Who is asking, from the session cookie: the employer, a worker (her rapporto's
        routes only, 403 elsewhere), or nobody (to the login page)."""
        request.state.worker = False
        request.state.logged = False
        if request.url.path == "/login":
            return
        token = request.cookies.get(COOKIE)
        session = store.session(digest(token)) if token else None
        if (
            session is None
            or session.expires <= now()
            or session.credential_tag != credential_tag(session.contract_id)
        ):
            raise _Redirect(f"/login?next={request.url.path}")
        request.state.logged = True
        if session.contract_id is not None:
            request.state.worker = True
            request.state.rapporto = session.contract_id
            allowed = worker_routes(session.contract_id).get(request.method)
            if allowed is None or not allowed.fullmatch(request.url.path):
                raise HTTPException(403)

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

    @app.exception_handler(_Redirect)
    async def redirect(request: Request, exc: _Redirect) -> RedirectResponse:
        return RedirectResponse(exc.location, status_code=303)

    # ---- login

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        nxt = request.query_params.get("next", "/")
        return templates.TemplateResponse(
            request, "login.html", {"next": nxt, "user": "", "error": ""}
        )

    @app.post("/login", response_model=None)
    async def login(request: Request) -> Response:
        """The employer (TATA_USER), or the worker of the rapporto with that login name."""
        form = await request.form()
        user, password = _field(form, "user"), _field(form, "password")
        nxt = _field(form, "next")
        worker = store.worker_login(user)
        if secrets.compare_digest(user.encode(), settings.user.encode()) and (
            secrets.compare_digest(password.encode(), settings.password.encode())
        ):
            who: int | None = None
        elif worker is not None and check_password(password, worker[1]):
            who = worker[0]
        else:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"next": nxt, "user": user, "error": "utente o password sbagliati"},
                status_code=401,
            )
        tag = credential_tag(who)
        if tag is None:
            raise RuntimeError("accesso appena verificato e già sparito")
        token = secrets.token_urlsafe(32)
        moment = now()
        store.add_session(
            Session(
                token_hash=digest(token),
                contract_id=who,
                credential_tag=tag,
                expires=moment + SESSION,
            ),
            moment,
        )
        target = nxt if SAFE_NEXT.fullmatch(nxt) and nxt != "/login" else "/"
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            COOKIE,
            token,
            max_age=int(SESSION.total_seconds()),
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    @app.post("/logout")
    def logout(request: Request) -> RedirectResponse:
        token = request.cookies.get(COOKIE)
        if token:
            store.delete_session(digest(token))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, httponly=True, secure=True, samesite="lax")
        return response

    def page(rs: RapportoStore, contract: Contract) -> dict[str, object]:
        """What every page of a rapporto needs: its URL prefix and whose it is."""
        return {"base": f"/r/{rs.id}", "who": contract.lavoratore_nome}

    def check_events(contract: Contract, events: list[Event]) -> None:
        """Raise ValueError now, at write time, rather than bricking a month page later."""
        for year, month in sorted({(e.day.year, e.day.month) for e in events}):
            month_work(contract, events, year, month, book.get(year))

    def minute_of(moment: datetime) -> int:
        return moment.hour * 60 + moment.minute

    def pending(rs: RapportoStore, contract: Contract, year: int, month: int) -> list[Shortfall]:
        """Shortfalls of an open month; a finalized month has nothing left to confirm."""
        if rs.final_payslip(year, month) is not None:
            return []
        return [
            s
            for s in shortfalls(contract, rs.events(), rs.punches(), now())
            if (s.day.year, s.day.month) == (year, month)
        ]

    def check_employed(contract: Contract, punch: Punch) -> None:
        if not contract.employed_on(punch.day):
            raise ValueError(f"il {punch.day} è fuori dal rapporto di lavoro")

    def save_punch(rs: RapportoStore, contract: Contract, draft: Punch) -> None:
        """Validate a closed punch and its EXTRA against everything stored, then write it."""
        if draft.end is None:
            raise ValueError("ora di uscita mancante")
        check_employed(contract, draft)
        moment = now()
        if draft.day > moment.date() or (
            draft.day == moment.date() and draft.end > minute_of(moment)
        ):
            raise ValueError("non si timbra nel futuro: l'uscita è dopo adesso")
        others = [p for p in rs.punches() if p.id != draft.id]
        check_punches([*others, draft])
        extras = overruns(contract, draft)
        drafts = [Event(id=0, day=draft.day, kind=EventKind.EXTRA, span=s, note="") for s in extras]
        check_events(contract, rs.events() + drafts)
        if draft.id == 0:
            rs.add_punch(draft.day, draft.start, draft.end, extras)
        else:
            rs.close_punch(draft.id, draft.end, extras)

    def paid_events(rs: RapportoStore, contract: Contract, year: int, month: int) -> list[Event]:
        """The events pay is computed from: the stored ones, and the unpunched time as ASSENZA
        up to the end of the month."""
        events = rs.events()
        return events + unpunched(contract, events, rs.punches(), year, month)

    def source(rs: RapportoStore, contract: Contract) -> PayslipSource:
        def get(year: int, month: int) -> Payslip:
            final = rs.final_payslip(year, month)
            if final is not None:
                return final
            events = paid_events(rs, contract, year, month)
            rimborso = None
            if settings.mileage is not None:
                month_turni = turni(rs.punches(), rs.employer_car(), year, month)
                rimborso = rimborso_line(settings.mileage, sum(t.own_car for t in month_turni))
            return compute_payslip(contract, events, year, month, book.get(year), rimborso)

        return get

    def landing(request: Request, suffix: str) -> RedirectResponse:
        """Where / and /timbra go: the worker's own rapporto, the only open one, or the list."""
        if request.state.worker:
            return RedirectResponse(f"/r/{request.state.rapporto}{suffix}", status_code=303)
        today = now().date()
        open_ = [r for r in store.rapporti() if r.open_on(today)]
        if len(open_) == 1:
            return RedirectResponse(f"/r/{open_[0].id}{suffix}", status_code=303)
        return RedirectResponse("/rapporti", status_code=303)

    @app.get("/")
    def home(request: Request) -> RedirectResponse:
        return landing(request, "/")

    @app.get("/timbra")
    def punch_home(request: Request) -> RedirectResponse:
        return landing(request, "/timbra")

    # ---- rapporti

    @app.get("/rapporti", response_class=HTMLResponse)
    def rapporti_page(request: Request) -> HTMLResponse:
        today = now().date()
        rapporti = sorted(store.rapporti(), key=lambda r: (not r.open_on(today), -r.id))
        return templates.TemplateResponse(
            request, "rapporti.html", {"rapporti": rapporti, "today": today}
        )

    @app.get("/rapporti/nuovo", response_class=HTMLResponse)
    def new_rapporto_form(request: Request) -> HTMLResponse:
        known = store.rapporti()
        datore = (
            (known[-1].contract.datore_nome, known[-1].contract.datore_cf) if known else ("", "")
        )
        return templates.TemplateResponse(
            request,
            "contratto.html",
            {
                "contract": None,
                "datore": datore,
                "ha_entity": "",
                "action": "/rapporti",
                "livelli": list(Livello),
                "giorni": GIORNI,
            },
        )

    @app.post("/rapporti")
    async def new_rapporto(request: Request) -> RedirectResponse:
        form = await request.form()
        contract = _contract(form)
        cid = store.add_rapporto(contract)
        store.set_ha_entity(cid, _field(form, "ha_entity"))
        return RedirectResponse(f"/r/{cid}/", status_code=303)

    @app.get("/r/{cid}/")
    def rapporto_home(cid: int) -> RedirectResponse:
        store.rapporto(cid)
        moment = now()
        return RedirectResponse(f"/r/{cid}/mese/{moment.year}/{moment.month}", status_code=303)

    # ---- cartellino

    @app.get("/r/{cid}/timbra", response_class=HTMLResponse)
    def punch_page(request: Request, cid: int) -> HTMLResponse:
        rs = store.rapporto(cid)
        contract = rs.contract()
        punches = rs.punches()
        moment = now()
        open_ = next((p for p in punches if p.end is None), None)
        return templates.TemplateResponse(
            request,
            "timbra.html",
            {
                **page(rs, contract),
                "proposal": propose(contract, punches, moment),
                "open": open_,
                "stale": open_ is not None and open_.day != moment.date(),
                "today": moment.date(),
                "actions": Action,
            },
        )

    @app.post("/r/{cid}/timbra/inizio")
    def punch_in(cid: int) -> RedirectResponse:
        rs = store.rapporto(cid)
        contract = rs.contract()
        moment = now()
        draft = Punch(id=0, day=moment.date(), start=minute_of(moment), end=None)
        check_employed(contract, draft)
        check_punches([*rs.punches(), draft])
        rs.add_punch(draft.day, draft.start, None, [])
        return RedirectResponse(f"/r/{cid}/timbra", status_code=303)

    @app.post("/r/{cid}/timbra/fine")
    async def punch_out(request: Request, cid: int) -> RedirectResponse:
        rs = store.rapporto(cid)
        contract = rs.contract()
        form = await request.form()
        alle, moment = _field(form, "alle"), now()
        open_ = next((p for p in rs.punches() if p.end is None), None)
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
        save_punch(rs, contract, Punch(id=open_.id, day=open_.day, start=open_.start, end=end))
        return RedirectResponse(f"/r/{cid}/timbra", status_code=303)

    # ---- contract

    @app.get("/r/{cid}/contratto", response_class=HTMLResponse)
    def contract_form(request: Request, cid: int) -> HTMLResponse:
        rs = store.rapporto(cid)
        contract = rs.contract()
        rapporto = next(r for r in store.rapporti() if r.id == cid)
        return templates.TemplateResponse(
            request,
            "contratto.html",
            {
                **page(rs, contract),
                "contract": contract,
                "ha_entity": rapporto.ha_entity,
                "worker_user": rapporto.worker_user,
                "action": f"/r/{cid}/contratto",
                "livelli": list(Livello),
                "giorni": GIORNI,
            },
        )

    @app.post("/r/{cid}/contratto")
    async def contract_save(request: Request, cid: int) -> RedirectResponse:
        rs = store.rapporto(cid)
        form = await request.form()
        contract = _contract(form)
        # The contract has no history: the EXTRA of every closed punch in an open month is
        # recomputed against the new schedule, validated together with it, saved with it.
        punches = rs.punches()
        for p in punches:
            check_employed(contract, p)
        recomputed = {
            p.id: overruns(contract, p)
            for p in punches
            if p.end is not None and rs.final_payslip(p.day.year, p.day.month) is None
        }
        links = rs.punch_links()
        kept = [e for e in rs.events() if links.get(e.id) not in recomputed]
        drafts = [
            Event(id=0, day=p.day, kind=EventKind.EXTRA, span=s, note="")
            for p in punches
            for s in recomputed.get(p.id, [])
        ]
        check_events(contract, kept + drafts)
        rs.save_contract(contract, recomputed)
        store.set_ha_entity(cid, _field(form, "ha_entity"))
        return RedirectResponse(f"/r/{cid}/", status_code=303)

    @app.post("/r/{cid}/accesso")
    async def worker_access(request: Request, cid: int) -> RedirectResponse:
        """Give, change or take away the worker's own login name and password."""
        store.rapporto(cid)
        form = await request.form()
        if form.get("togli"):
            store.set_worker_login(cid, None)
            return RedirectResponse(f"/r/{cid}/contratto", status_code=303)
        user, password = _field(form, "user").strip(), _field(form, "password")
        if not user or user == settings.user:
            raise ValueError("nome utente vuoto o del datore")
        if len(password) < 8:
            raise ValueError("la password deve avere almeno 8 caratteri")
        store.set_worker_login(cid, (user, hash_password(password)))
        return RedirectResponse(f"/r/{cid}/contratto", status_code=303)

    # ---- month

    @app.get("/r/{cid}/mese/{year}/{month}", response_class=HTMLResponse)
    def month_page(request: Request, cid: int, year: int, month: int) -> HTMLResponse:
        rs = store.rapporto(cid)
        contract = rs.contract()
        events = rs.events()
        rates = book.get(year)
        slip = source(rs, contract)(year, month)
        final = rs.final_payslip(year, month) is not None
        planned = None if final else compute_payslip(contract, events, year, month, rates, None)
        paid = paid_events(rs, contract, year, month)
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
                **page(rs, contract),
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
                    t.punch_id: t for t in turni(rs.punches(), rs.employer_car(), year, month)
                },
                "mileage": settings.mileage,
                "trip_cost": trip_cost(settings.mileage) if settings.mileage else None,
                "punches": [p for p in rs.punches() if (p.day.year, p.day.month) == (year, month)],
                "pending": pending(rs, contract, year, month),
                "wifi": wifi_unrecorded(cid, rs, year, month)
                if not request.state.worker
                else WifiBox(days=[], error=""),
                "final": final,
                "tfr": tfr(contract, rs.advances(), year, month, source(rs, contract), book),
                "prev": f"/r/{cid}/mese/{prev_y}/{prev_m}",
                "next": f"/r/{cid}/mese/{next_y}/{next_m}",
                "giorni": GIORNI,
                "selected": request.query_params.get("giorno", date(year, month, 1).isoformat()),
            },
        )

    @app.get("/r/{cid}/mese/{year}/{month}/prospetto.pdf")
    def payslip_pdf(cid: int, year: int, month: int) -> Response:
        rs = store.rapporto(cid)
        contract = rs.contract()
        slip = source(rs, contract)(year, month)
        html = templates.get_template("prospetto_pdf.html").render(
            slip=slip,
            mese=MESI[month],
            tfr=tfr(contract, rs.advances(), year, month, source(rs, contract), book),
            css=(HERE / "static" / "pdf.css").read_text(),
        )
        name = contract.lavoratore_nome.split()[-1].lower()
        return _pdf(render_pdf(html), f"prospetto-{name}-{year}-{month:02d}.pdf")

    @app.post("/r/{cid}/mese/{year}/{month}/finalizza")
    def finalize(cid: int, year: int, month: int) -> RedirectResponse:
        rs = store.rapporto(cid)
        contract = rs.contract()
        py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
        if employed_months(contract, py, pm)[-1:] == [pm] and rs.final_payslip(py, pm) is None:
            raise ValueError(f"finalizza prima {MESI[pm]} {py}: i mesi si chiudono in ordine")
        if any(p.end is None for p in rs.punches() if (p.day.year, p.day.month) == (year, month)):
            raise ValueError(f"c'è una timbratura aperta in {MESI[month]} {year}: chiudila prima")
        if pending(rs, contract, year, month):
            raise ValueError(f"ci sono mancanze da confermare in {MESI[month]} {year}")
        if now().date() < date(year + month // 12, month % 12 + 1, 1):
            raise ValueError(f"{MESI[month]} {year} non è ancora finito")
        rs.finalize(source(rs, contract)(year, month))
        return RedirectResponse(f"/r/{cid}/mese/{year}/{month}", status_code=303)

    @app.post("/r/{cid}/timbrature")
    async def add_punch(request: Request, cid: int) -> RedirectResponse:
        rs = store.rapporto(cid)
        contract = rs.contract()
        form = await request.form()
        day = date.fromisoformat(_field(form, "giorno"))
        s = Span.parse(f"{_field(form, 'inizio')}-{_field(form, 'fine')}")
        save_punch(rs, contract, Punch(id=0, day=day, start=s.start, end=s.end))
        back = form.get("torna")
        if isinstance(back, str) and RETURN_TO.fullmatch(back) and back.startswith(f"/r/{cid}/"):
            return RedirectResponse(back, status_code=303)
        if day == now().date():
            return RedirectResponse(f"/r/{cid}/timbra", status_code=303)
        return RedirectResponse(f"/r/{cid}/mese/{day.year}/{day.month}", status_code=303)

    @app.post("/r/{cid}/turni/auto")
    async def turno_car(request: Request, cid: int) -> RedirectResponse:
        """Whose car a turno was done with: the employer's pays no rimborso."""
        rs = store.rapporto(cid)
        form = await request.form()
        day = date.fromisoformat(_field(form, "giorno"))
        rs.set_employer_car(day, _field(form, "mattina") == "1", _field(form, "datore") == "1")
        return RedirectResponse(f"/r/{cid}/mese/{day.year}/{day.month}#cartellino", status_code=303)

    @app.post("/r/{cid}/timbrature/{punch_id}/elimina")
    def delete_punch(cid: int, punch_id: int) -> RedirectResponse:
        day = store.rapporto(cid).delete_punch(punch_id)
        return RedirectResponse(f"/r/{cid}/mese/{day.year}/{day.month}", status_code=303)

    # ---- wifi presence

    @app.get("/r/{cid}/presenza")
    def this_month_presence(cid: int) -> RedirectResponse:
        store.rapporto(cid)
        moment = now()
        return RedirectResponse(f"/r/{cid}/presenza/{moment.year}/{moment.month}", status_code=303)

    def wifi_days(cid: int, year: int, month: int, moment: datetime) -> list[PresenceDay]:
        """The days of the month, up to now, the worker's phone was seen on the wifi.
        ValueError if wifi presence is not set up; URLError/TimeoutError if HA is down."""
        entity = next(r.ha_entity for r in store.rapporti() if r.id == cid)
        if presence is None:
            raise ValueError(f"presenza wifi non configurata: imposta {', '.join(HA_VARS)}")
        if not entity:
            raise ValueError("presenza wifi: indica nel contratto il tracker del telefono")
        first = date(year, month, 1)
        last = min(date(year, month, calendar.monthrange(year, month)[1]), moment.date())
        if first > last:
            return []
        start = datetime.combine(first, time(0), ROME)
        end = min(moment, datetime.combine(last + timedelta(days=1), time(0), ROME))
        history = presence(entity, start, end)
        return [d for d in presence_days(history, first, last) if d.sessions]

    def wifi_unrecorded(cid: int, rs: RapportoStore, year: int, month: int) -> WifiBox:
        """For the month page: proposals no punch covers yet, confirmable now, and anomalous
        days with no punch at all. Never breaks the page: HA down is said in the box."""
        rapporto = next(r for r in store.rapporti() if r.id == cid)
        if presence is None or not rapporto.ha_entity:
            return WifiBox(days=[], error="")
        moment = now()
        try:
            days = wifi_days(cid, year, month, moment)
        except (URLError, TimeoutError) as e:
            return WifiBox(days=[], error=f"Home Assistant non raggiungibile: {e}")
        punches = closed_punches(rs, year, month)
        todo: list[PresenceDay] = []
        for d in days:
            ready = [
                s
                for s in d.proposals
                if not covered(d.day, s, punches)
                and (d.day < moment.date() or s.end <= minute_of(moment))
            ]
            stray = d.anomalies if not any(p.day == d.day for p in punches) else []
            if ready or stray:
                todo.append(d.model_copy(update={"proposals": ready, "anomalies": stray}))
        return WifiBox(days=todo, error="")

    @app.get("/r/{cid}/presenza/{year}/{month}", response_class=HTMLResponse)
    def presence_page(request: Request, cid: int, year: int, month: int) -> HTMLResponse:
        """Wifi sessions and the punches they suggest; the employer confirms each one."""
        rs = store.rapporto(cid)
        contract = rs.contract()
        moment = now()
        days = wifi_days(cid, year, month, moment)
        punches = closed_punches(rs, year, month)
        weeks: dict[date, int] = {}
        for p in punches:
            monday = p.day - timedelta(days=p.day.weekday())
            weeks[monday] = weeks.get(monday, 0) + p.span().end - p.span().start
        return templates.TemplateResponse(
            request,
            "presenza.html",
            {
                **page(rs, contract),
                "year": year,
                "month": month,
                "mese": MESI[month],
                "days": days,
                "recorded": {
                    (d.day, s) for d in days for s in d.proposals if covered(d.day, s, punches)
                },
                "today": moment.date(),
                "minute": minute_of(moment),
                "weeks": sorted(weeks.items()),
                "here": f"/r/{cid}/presenza/{year}/{month}",
            },
        )

    # ---- events

    @app.post("/r/{cid}/eventi")
    async def add_event(request: Request, cid: int) -> RedirectResponse:
        rs = store.rapporto(cid)
        contract = rs.contract()
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
        check_events(contract, rs.events() + drafts)
        for day in days:
            rs.add_event(day, kind, span, note)
        return RedirectResponse(f"/r/{cid}/mese/{first.year}/{first.month}", status_code=303)

    @app.post("/r/{cid}/eventi/{event_id}/elimina")
    def delete_event(cid: int, event_id: int) -> RedirectResponse:
        day = store.rapporto(cid).delete_event(event_id)
        return RedirectResponse(f"/r/{cid}/mese/{day.year}/{day.month}", status_code=303)

    # ---- year

    @app.get("/r/{cid}/anno")
    def this_year(cid: int) -> RedirectResponse:
        store.rapporto(cid)
        return RedirectResponse(f"/r/{cid}/anno/{now().year}", status_code=303)

    @app.get("/r/{cid}/anno/{year}", response_class=HTMLResponse)
    def year_page(request: Request, cid: int, year: int) -> HTMLResponse:
        rs = store.rapporto(cid)
        contract = rs.contract()
        src = source(rs, contract)
        months = employed_months(contract, year, 12)
        quarters = [
            quarter_contributions([src(year, m) for m in quarter_months(q) if m in months], year, q)
            for q in (1, 2, 3, 4)
            if any(m in months for m in quarter_months(q))
        ]
        paid = {(p.year, p.quarter): p for p in rs.payments()}
        moment = now().date()
        # Months after today are a projection: TFR stops at today's month.
        current = 12 if year < moment.year else (0 if year > moment.year else moment.month)
        last_month = min(months[-1] if months else 12, max(current, 1))
        projected = {q.quarter for q in quarters if quarter_months(q.quarter)[-1] > current}
        return templates.TemplateResponse(
            request,
            "anno.html",
            {
                **page(rs, contract),
                "year": year,
                "quarters": quarters,
                "paid": paid,
                "projected": projected,
                "slips": year_payslips(contract, year, 12, src),
                "current": current,
                "tfr": tfr(contract, rs.advances(), year, last_month, src, book),
                "advances": [a for a in rs.advances() if a.day.year == year],
                "mesi": MESI,
            },
        )

    @app.post("/r/{cid}/anno/{year}/versamenti")
    async def record_payment(request: Request, cid: int, year: int) -> RedirectResponse:
        rs = store.rapporto(cid)
        contract = rs.contract()
        form = await request.form()
        quarter = int(_field(form, "trimestre"))
        src = source(rs, contract)
        months = employed_months(contract, year, 12)
        q = quarter_contributions(
            [src(year, m) for m in quarter_months(quarter) if m in months], year, quarter
        )
        rs.record_payment(
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
        return RedirectResponse(f"/r/{cid}/anno/{year}", status_code=303)

    @app.post("/r/{cid}/anno/{year}/anticipi")
    async def add_advance(request: Request, cid: int, year: int) -> RedirectResponse:
        rs = store.rapporto(cid)
        form = await request.form()
        rs.add_advance(
            date.fromisoformat(_field(form, "giorno")), _decimal(_field(form, "importo"))
        )
        return RedirectResponse(f"/r/{cid}/anno/{year}", status_code=303)

    @app.get("/r/{cid}/anno/{year}/attestazione.pdf")
    def attestazione_pdf(cid: int, year: int) -> Response:
        rs = store.rapporto(cid)
        contract = rs.contract()
        html = templates.get_template("attestazione_pdf.html").render(
            att=attestazione(contract, year, source(rs, contract)),
            mesi=MESI,
            css=(HERE / "static" / "pdf.css").read_text(),
        )
        name = contract.lavoratore_nome.split()[-1].lower()
        return _pdf(render_pdf(html), f"attestazione-{name}-{year}.pdf")

    @app.get("/anno/{year}/deduzioni.yaml")
    def deduction_yaml(year: int) -> PlainTextResponse:
        """The employer's deduction: every rapporto's payments, one cap (art. 10 c.2 TUIR)."""
        d = deduzione(store.all_payments(), year, book.get(year))
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
    templates.env.filters["hm"] = _fmt_hm
    return templates


def _fmt_num(value: Decimal) -> str:
    """Italian number format, two decimals: 1.234,56"""
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _fmt_dmy(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _fmt_ore(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


def _fmt_hm(hours: Decimal) -> str:
    """Hours as hours and minutes: 1,62 -> "1 h 37 min". The payslip keeps hours to the
    cent of an hour, 0,3 min at most from the minutes they came from: rounding restores them."""
    total = int((hours * 60).to_integral_value(ROUND_HALF_UP))
    h, m = divmod(total, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def _fmt_eur(value: Decimal) -> str:
    return f"{_fmt_num(value)} €"


def _field(form: FormData, name: str) -> str:
    value = form.get(name)
    if not isinstance(value, str):
        raise ValueError(f"campo mancante: {name}")
    return value.strip()


def _contract(form: FormData) -> Contract:
    day, month = (int(x) for x in _field(form, "patrono").split("/"))
    cessazione = _field(form, "cessazione")
    return Contract(
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
