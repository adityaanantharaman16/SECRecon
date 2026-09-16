import hashlib
import secrets
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def credential(request: Request) -> str:
    return str(request.app.state.settings.admin_token.get_secret_value())


def session(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get("secrecon_session", "")
    if not token or not credential(request):
        return None
    with request.app.state.engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT * FROM admin_sessions WHERE token_hash=:h AND credential_hash=:c AND expires_at>now()"
                ),
                {"h": digest(token), "c": digest(credential(request))},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


def same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Cross-origin action rejected")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site action rejected")


bearer = HTTPBearer(auto_error=False)


def authorize(
    request: Request,
    _bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> str:
    expected = credential(request)
    supplied = request.headers.get("authorization", "")
    if (
        expected
        and supplied.startswith("Bearer ")
        and secrets.compare_digest(supplied[7:], expected)
    ):
        return "local-token"
    current_session = session(request)
    if not current_session:
        raise HTTPException(401, "Operator sign-in required")
    if request.method not in {"GET", "HEAD"}:
        same_origin(request)
        if not secrets.compare_digest(
            digest(request.headers.get("x-csrf-token", "")), current_session["csrf_hash"]
        ):
            raise HTTPException(403, "Invalid CSRF token")
    return "local-session"


def create_session(request: Request, supplied: str) -> tuple[str, str]:
    same_origin(request)
    if not credential(request) or not secrets.compare_digest(supplied, credential(request)):
        raise HTTPException(401, "Invalid operator token")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with request.app.state.engine.begin() as connection:
        connection.execute(text("DELETE FROM admin_sessions WHERE expires_at<=now()"))
        connection.execute(
            text(
                "INSERT INTO admin_sessions(token_hash,csrf_hash,credential_hash,expires_at) VALUES (:h,:csrf,:c,now()+interval '8 hours')"
            ),
            {"h": digest(token), "csrf": digest(csrf), "c": digest(credential(request))},
        )
    return token, csrf
