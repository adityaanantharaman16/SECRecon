"""Server-rendered operations views. Templates autoescape every source-derived value."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from secrecon.api.auth import authorize, create_session, digest, session
from secrecon.db import queries
from secrecon.db.projections import fact_provenance
from secrecon.db.reconciliation import get_run
from secrecon.jobs import store
from secrecon.telemetry.metrics import snapshot

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
NAV = [
    ("overview", "Overview", "◫"),
    ("companies", "Companies", "◇"),
    ("filings", "Filings", "▤"),
    ("amendments", "Reconciliation", "⇄"),
    ("jobs", "Processing jobs", "☷"),
    ("quarantine", "Quarantine", "!"),
    ("sources", "Source archive", "▱"),
    ("recovery", "Replay & recovery", "↺"),
]
PRIVATE = {"jobs", "quarantine", "recovery", "backfills", "replays"}


def render(request: Request, template: str, **context: Any) -> Response:
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "nav": NAV,
            "signed_in": bool(session(request)),
            "csrf": request.cookies.get("secrecon_csrf", ""),
            "mode": request.app.state.settings.sec_mode,
            "grafana_url": request.app.state.settings.grafana_url,
            **context,
        },
    )


@router.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/ui/overview")


@router.get("/ui/login")
def login_page(request: Request) -> Response:
    return render(request, "login.html", page="login", title="Operator sign-in")


@router.post("/ui/login")
def login(request: Request, token: str = Form(..., max_length=500)) -> Response:
    session_token, csrf = create_session(request, token)
    response = RedirectResponse("/ui/jobs", status_code=303)
    secure = request.app.state.settings.cookie_secure
    response.set_cookie(
        "secrecon_session",
        session_token,
        max_age=28800,
        httponly=True,
        samesite="strict",
        secure=secure,
    )
    response.set_cookie("secrecon_csrf", csrf, max_age=28800, samesite="strict", secure=secure)
    return response


@router.post("/ui/logout")
def logout(request: Request) -> Response:
    authorize(request)
    with request.app.state.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM admin_sessions WHERE token_hash=:h"),
            {"h": digest(request.cookies.get("secrecon_session", ""))},
        )
    response = RedirectResponse("/ui/overview", status_code=303)
    response.delete_cookie("secrecon_session")
    response.delete_cookie("secrecon_csrf")
    return response


@router.get("/ui/{page}")
def screen(request: Request, page: str) -> Response:
    if page in PRIVATE and not session(request):
        return RedirectResponse("/ui/login", status_code=303)
    with request.app.state.engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout='5s'"))
        generation = queries.selected_generation(connection, request.query_params.get("generation"))
        title = dict((key, label) for key, label, _ in NAV).get(page, page.title())
        context: dict[str, Any] = {"page": page, "title": title, "generation": generation}
        if page == "overview":
            context["stats"] = snapshot(
                connection,
                request.app.state.settings.redis_url,
                request.app.state.settings.sec_mode == "live",
            )
            context["recent"] = queries.page(connection, "filings", generation=generation, limit=8)[
                "items"
            ]
        elif page == "recovery":
            context["backfills"] = queries.page(connection, "backfills", limit=20)
            context["replays"] = queries.page(connection, "replays", limit=20)
            context["watchlist"] = list(
                connection.execute(
                    text("SELECT cik FROM watchlist WHERE enabled ORDER BY cik")
                ).scalars()
            )
        elif page in queries.SPECS:
            filters = {
                k: request.query_params[k]
                for k in queries.FILTERS[page]
                if request.query_params.get(k)
            }
            context["listing"] = queries.page(
                connection,
                page,
                generation=generation,
                filters=filters,
                limit=25,
                cursor=request.query_params.get("cursor"),
            )
            context["filters"] = filters
        else:
            raise HTTPException(404, "Page not found")
    return render(request, "screen.html", **context)


@router.get("/ui/{resource}/{identifier}")
def detail(request: Request, resource: str, identifier: str) -> Response:
    if resource in {"jobs", "operations"} and not session(request):
        return RedirectResponse("/ui/login", status_code=303)
    with request.app.state.engine.begin() as connection:
        generation = queries.selected_generation(connection, request.query_params.get("generation"))
        data: Any = None
        if resource == "filings":
            data = queries.filing_detail(connection, identifier, generation)
        elif resource == "facts":
            data = fact_provenance(
                connection,
                generation,
                identifier,
                limit=50,
                after=request.query_params.get("cursor", ""),
            )
        elif resource == "comparisons":
            data = get_run(
                connection, identifier, limit=50, after=request.query_params.get("cursor", "")
            )
        elif resource == "sources":
            data = connection.scalar(
                text("SELECT manifest FROM source_events WHERE event_id=:id"), {"id": identifier}
            )
        elif resource == "jobs":
            if connection.scalar(text("SELECT id FROM jobs WHERE id=:id"), {"id": identifier}):
                data = store.inspect(request.app.state.engine, identifier)
        elif resource == "operations":
            row = (
                connection.execute(
                    text(
                        "SELECT a.id,a.action,a.result,a.job_id,j.state,j.error FROM admin_requests a JOIN jobs j ON j.id=a.job_id WHERE a.id=:id"
                    ),
                    {"id": identifier},
                )
                .mappings()
                .first()
            )
            data = dict(row) if row else None
        if data is None:
            raise HTTPException(404, "Record not found")
    return render(
        request,
        "detail.html",
        page=resource,
        title=resource.title(),
        identifier=identifier,
        data=data,
        generation=generation,
    )
