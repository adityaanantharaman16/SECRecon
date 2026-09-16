"""Allowlisted SQL and generation-pinned keyset cursors. No user SQL identifiers."""

import base64
import json
from datetime import date
from typing import Any

from sqlalchemy import Connection, text

from secrecon.domain.types import canonical, cik_text, fingerprint

SPECS = {
    "companies": ("companies t", "t.cik", "t.data", True),
    "filings": ("filings t", "t.accession", "t.data", True),
    "facts": ("facts t", "t.fingerprint", "t.data", True),
    "amendments": (
        "amendment_heads t JOIN amendment_links l ON l.id=t.link_id LEFT JOIN reconciliation_heads r ON r.generation=t.generation AND r.amendment=t.amendment",
        "t.amendment",
        "l.data",
        True,
    ),
    "sources": ("source_events t", "t.event_id", "t.manifest", False),
    "jobs": ("jobs t", "t.id", "to_jsonb(t)-'request_hash'-'idempotency_key'", False),
    "quarantine": (
        "quarantine_records t",
        "t.event_id || '/' || t.parser_version",
        "to_jsonb(t)",
        True,
    ),
    "backfills": ("backfills t", "t.id", "to_jsonb(t)", False),
    "replays": ("replays t", "t.generation", "to_jsonb(t)-'event_ids'", False),
}
FILTERS = {
    "companies": {
        "cik": "t.cik=:cik",
        "q": "(t.cik ILIKE :q OR lower(t.data->>'name') LIKE lower(:q) OR lower((t.data->'tickers')::text) LIKE lower(:q))",
    },
    "filings": {
        "cik": "t.cik=:cik",
        "form": "t.form=:form",
        "from_date": "t.filing_date>=CAST(:from_date AS date)",
        "to_date": "t.filing_date<=CAST(:to_date AS date)",
    },
    "facts": {
        "cik": "t.cik=:cik",
        "accession": "t.accession=:accession",
        "concept": "t.concept=:concept",
        "period_end": "t.data->>'end_date'=:period_end",
    },
    "sources": {
        "cik": "t.manifest->>'cik'=:cik",
        "accession": "(t.manifest->>'accession'=:accession OR EXISTS (SELECT 1 FROM filing_sources f WHERE f.event_id=t.event_id AND f.generation=:gen AND f.accession=:accession))",
    },
    "jobs": {"state": "t.state=:state", "kind": "t.kind=:kind"},
    "quarantine": {},
    "backfills": {"state": "t.state=:state"},
    "replays": {"state": "t.state=:state"},
    "amendments": {},
}


def selected_generation(connection: Connection, requested: str | None) -> str:
    name = (
        requested
        if requested and requested != "active"
        else connection.scalar(text("SELECT value FROM system_state WHERE key='active_generation'"))
    )
    if not connection.scalar(text("SELECT name FROM generations WHERE name=:g"), {"g": name}):
        raise ValueError("Unknown projection generation")
    return str(name)


def page(
    connection: Connection,
    resource: str,
    *,
    generation: str | None = None,
    filters: dict[str, str] | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    filters = dict(filters or {})
    if "cik" in filters:
        filters["cik"] = cik_text(filters["cik"])
    for field in ("from_date", "to_date", "period_end"):
        if field in filters:
            date.fromisoformat(filters[field])
    if any(len(value) > 200 for value in filters.values()):
        raise ValueError("Filter value is too long")
    if (
        "from_date" in filters
        and "to_date" in filters
        and filters["from_date"] > filters["to_date"]
    ):
        raise ValueError("Start date must not follow end date")
    if resource not in SPECS or not 1 <= limit <= 200:
        raise ValueError("Invalid page request")
    if set(filters) - FILTERS[resource].keys():
        raise ValueError("Unsupported filter for this resource")
    token: dict[str, Any] = {}
    if cursor:
        try:
            if len(cursor) > 3000:
                raise ValueError
            token = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if (
                set(token) != {"resource", "generation", "filters", "last"}
                or not isinstance(token["last"], str)
                or len(token["last"]) > 300
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ValueError("Invalid pagination cursor") from None
    gen = selected_generation(connection, str(token["generation"]) if token else generation)
    if token and (
        token["resource"] != resource
        or token["filters"] != fingerprint(filters)
        or (generation not in (None, "active", gen))
    ):
        raise ValueError("Cursor does not match resource, filters or generation")
    table, key, data, versioned = SPECS[resource]
    conditions = [f"{key}>:after"]
    if versioned:
        conditions.append("t.generation=:gen")
    conditions.extend(FILTERS[resource][field] for field in filters)
    params: dict[str, Any] = {
        "after": token.get("last", ""),
        "gen": gen,
        "limit": limit + 1,
        **filters,
    }
    if "q" in params:
        params["q"] = (
            "%" + params["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
    extra = ",r.run_id" if resource == "amendments" else ""
    rows = [
        dict(row)
        for row in connection.execute(
            text(
                f"SELECT {key} AS id,{data} AS data{extra} FROM {table} WHERE {' AND '.join(conditions)} ORDER BY {key} LIMIT :limit"
            ),
            params,
        ).mappings()
    ]
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        base64.urlsafe_b64encode(
            canonical(
                {
                    "resource": resource,
                    "generation": gen,
                    "filters": fingerprint(filters),
                    "last": rows[-1]["id"],
                }
            ).encode()
        ).decode()
        if more
        else None
    )
    freshness = connection.scalar(
        text(
            "SELECT max(s.fetched_at) FROM source_events s JOIN processing_runs p USING(event_id) WHERE p.generation=:g AND p.status='succeeded'"
        ),
        {"g": gen},
    )
    return {
        "generation": gen,
        "freshness": freshness,
        "coverage": "Supported Company Facts observations; absence is not deletion",
        "items": rows,
        "next_cursor": next_cursor,
    }


def filing_detail(connection: Connection, accession: str, generation: str) -> dict[str, Any] | None:
    data = connection.scalar(
        text("SELECT data FROM filings WHERE generation=:g AND accession=:a"),
        {"g": generation, "a": accession},
    )
    if data is None:
        return None
    sources = [
        dict(row)
        for row in connection.execute(
            text(
                "SELECT f.role,s.manifest FROM filing_sources f JOIN source_events s USING(event_id) WHERE f.generation=:g AND f.accession=:a ORDER BY f.event_id,f.role LIMIT 201"
            ),
            {"g": generation, "a": accession},
        ).mappings()
    ]
    count = connection.scalar(
        text("SELECT count(*) FROM facts WHERE generation=:g AND accession=:a"),
        {"g": generation, "a": accession},
    )
    return {
        "generation": generation,
        "filing": data,
        "sources": sources[:200],
        "sources_truncated": len(sources) > 200,
        "coverage": {
            "observations": count,
            "scope": "Supported Company Facts only; not full filing XBRL",
        },
    }
