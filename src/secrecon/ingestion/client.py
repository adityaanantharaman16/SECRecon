"""Bounded HTTP fetching with durable request outcomes and raw error preservation."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import Engine, text

from secrecon.config import Settings
from secrecon.db.projections import register_source
from secrecon.domain.types import utcnow
from secrecon.ingestion.adapters import PARSER_VERSION
from secrecon.ingestion.rate_limit import RateLimiter
from secrecon.jobs.store import enqueue
from secrecon.storage.archive import Archive, Manifest


class FetchError(RuntimeError):
    def __init__(
        self, message: str, *, retryable: bool, retry_after: float = 0, event_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.event_id = event_id


def retry_after_seconds(value: str) -> float:
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError):
            return 0


def validate_sec_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.sec.gov", "data.sec.gov"}
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.fragment
        or parsed.query
        or ".." in parsed.path
    ):
        raise ValueError("Only canonical SEC HTTPS URLs are allowed")


class SecClient:
    def __init__(
        self,
        settings: Settings,
        engine: Engine,
        archive: Archive,
        limiter: RateLimiter,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings, self.engine, self.archive, self.limiter = settings, engine, archive, limiter
        self.http = httpx.Client(
            headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self.http.close()

    def fetch(
        self,
        url: str,
        kind: Literal["submissions", "facts", "document"],
        cik: str,
        accession: str | None = None,
        backfill_id: str | None = None,
    ) -> Manifest:
        validate_sec_url(url)
        if self.settings.sec_mode != "live":
            raise FetchError("SEC network access is disabled", retryable=False)
        request_id = str(uuid4())
        requested_at = utcnow().isoformat()
        self.limiter.acquire()
        with self.engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO fetch_attempts(id,url,requested_at,status)
                VALUES (:id,:url,:at,'started')
            """),
                {"id": request_id, "url": url, "at": requested_at},
            )
        try:
            with self.http.stream("GET", url) as response:
                body = bytearray()
                complete = True
                for chunk in response.iter_bytes():
                    remaining = self.settings.max_response_bytes - len(body)
                    body.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        complete = False
                        break
                headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key
                    in {"content-type", "content-encoding", "etag", "last-modified", "retry-after"}
                }
                manifest = self.archive.preserve(
                    bytes(body),
                    kind=kind,
                    url=url,
                    cik=cik,
                    status=response.status_code,
                    headers=headers,
                    requested_at=requested_at,
                    accession=accession,
                    complete=complete,
                    correlation_id=request_id,
                )
            with self.engine.begin() as connection:
                register_source(connection, manifest)
                if manifest.complete and 200 <= manifest.status < 300:
                    normalization = enqueue(
                        connection,
                        "normalize",
                        {"event_id": manifest.event_id, "generation": "active"},
                        f"normalize:active:{PARSER_VERSION}:{manifest.event_id}",
                        priority=10,
                    )
                    if backfill_id:
                        connection.execute(
                            text(
                                "INSERT INTO backfill_normalizations VALUES (:op,:job) ON CONFLICT DO NOTHING"
                            ),
                            {"op": backfill_id, "job": normalization},
                        )
                connection.execute(
                    text("""
                    UPDATE fetch_attempts SET status=:status,completed_at=now(),event_id=:event
                    WHERE id=:id
                """),
                    {
                        "status": "received" if complete else "truncated",
                        "event": manifest.event_id,
                        "id": request_id,
                    },
                )
        except httpx.TransportError as exc:
            with self.engine.begin() as connection:
                connection.execute(
                    text("""
                    UPDATE fetch_attempts SET status='transport_error',completed_at=now(),error=:error
                    WHERE id=:id
                """),
                    {"id": request_id, "error": type(exc).__name__},
                )
            raise FetchError(type(exc).__name__, retryable=True) from exc
        if manifest.status == 403:
            self.limiter.pause()
        if not complete or not 200 <= manifest.status < 300:
            raise FetchError(
                f"SEC response status={manifest.status}, complete={complete}",
                retryable=complete and (manifest.status in {404, 429} or manifest.status >= 500),
                retry_after=retry_after_seconds(headers.get("retry-after", "")),
                event_id=manifest.event_id,
            )
        return manifest
