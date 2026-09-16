"""Snapshot-bound immutable comparison runs. Callers hold company projection locks."""

from collections import Counter
from typing import Any

from sqlalchemy import Connection, Engine, text

from secrecon.db.transactions import transaction
from secrecon.domain import reconciliation as rules
from secrecon.domain.types import canonical, fingerprint


def snapshot(connection: Connection, generation: str, cik: str, event_id: str | None) -> str | None:
    rows = (
        connection.execute(
            text("""
        SELECT s.event_id FROM source_events s JOIN processing_runs p USING(event_id)
        WHERE p.generation=:g AND p.status='succeeded' AND s.manifest->>'kind'='facts'
        AND s.manifest->>'cik'=:cik AND (CAST(:id AS text) IS NULL OR s.event_id=:id)
        ORDER BY s.fetched_at DESC,s.event_id DESC LIMIT 1
    """),
            {"g": generation, "cik": cik, "id": event_id},
        )
        .scalars()
        .first()
    )
    if event_id and rows is None:
        raise ValueError(
            "Selected snapshot is not a successfully processed Company Facts source for this company/generation"
        )
    return rows


def observations(
    connection: Connection, generation: str, accession: str, event_id: str | None
) -> list[dict[str, Any]]:
    if not event_id:
        return []
    rows = connection.execute(
        text("""
        SELECT f.data,f.fingerprint,p.locator,p.event_id,p.parser_version
        FROM facts f JOIN fact_provenance p USING(generation,fingerprint)
        WHERE f.generation=:g AND f.accession=:acc AND p.event_id=:id
        ORDER BY f.fingerprint,p.locator
    """),
        {"g": generation, "acc": accession, "id": event_id},
    ).mappings()
    return [
        {
            **row["data"],
            **{k: row[k] for k in ("fingerprint", "locator", "event_id", "parser_version")},
        }
        for row in rows
    ]


def evidence(
    connection: Connection, generation: str, accession: str, event_id: str | None
) -> dict[str, Any]:
    documents = (
        connection.execute(
            text("""
        SELECT s.manifest FROM filing_sources f JOIN source_events s USING(event_id)
        WHERE f.generation=:g AND f.accession=:a AND f.role='document'
        ORDER BY s.fetched_at,s.event_id
    """),
            {"g": generation, "a": accession},
        )
        .scalars()
        .all()
    )
    source = connection.scalar(
        text("SELECT manifest FROM source_events WHERE event_id=:id"), {"id": event_id}
    )
    discovery = list(
        connection.execute(
            text("""
        SELECT s.manifest FROM filing_sources f JOIN source_events s USING(event_id)
        WHERE f.generation=:g AND f.accession=:a AND f.role='discovery'
        ORDER BY s.fetched_at,s.event_id
    """),
            {"g": generation, "a": accession},
        ).scalars()
    )
    return {"snapshot": source, "documents": list(documents), "discovery": discovery}


def save_pair(
    connection: Connection,
    generation: str,
    original: dict[str, Any],
    amendment: dict[str, Any],
    original_event: str | None = None,
    amendment_event: str | None = None,
) -> str:
    link = rules.candidates(amendment, [original])
    if link["status"] != "unique":
        raise ValueError(
            "Pair must share company, base form and report date; original must be earlier"
        )
    cik = original["cik"]
    left_id = snapshot(connection, generation, cik, original_event)
    right_id = snapshot(connection, generation, cik, amendment_event)
    left = observations(connection, generation, original["accession"], left_id)
    right = observations(connection, generation, amendment["accession"], right_id)
    changes = rules.compare(left, right)
    summary = {
        "comparison_version": rules.COMPARISON_VERSION,
        "parser_version": connection.scalar(
            text("SELECT parser_version FROM generations WHERE name=:g"), {"g": generation}
        ),
        "original": original,
        "amendment": amendment,
        "original_event_id": left_id,
        "amendment_event_id": right_id,
        "coverage": {
            "status": "available" if left and right else "incomplete",
            "original_observations": len(left),
            "amendment_observations": len(right),
            "scope": "Supported entity-wide US-GAAP observations in selected snapshots; not complete filing XBRL",
            "absence_policy": "Absent observations are not deletions",
        },
        "counts": dict(sorted(Counter(item["status"] for item in changes).items())),
        "evidence": {
            "original": evidence(connection, generation, original["accession"], left_id),
            "amendment": evidence(connection, generation, amendment["accession"], right_id),
        },
    }
    run_id = fingerprint({"generation": generation, "summary": summary, "changes": changes})
    inserted = connection.scalar(
        text("""
        INSERT INTO reconciliation_runs(id,generation,original,amendment,data)
        VALUES (:id,:g,:o,:a,CAST(:data AS jsonb)) ON CONFLICT DO NOTHING RETURNING id
    """),
        {
            "id": run_id,
            "g": generation,
            "o": original["accession"],
            "a": amendment["accession"],
            "data": canonical(summary),
        },
    )
    if inserted:
        for change in changes:
            connection.execute(
                text("INSERT INTO fact_changes VALUES (:id,:key,:status,CAST(:data AS jsonb))"),
                {
                    "id": run_id,
                    "key": fingerprint(change["key"]),
                    "status": change["status"],
                    "data": canonical(change),
                },
            )
    return run_id


def refresh_company(connection: Connection, generation: str, cik: str) -> None:
    filings = list(
        connection.execute(
            text("SELECT data FROM filings WHERE generation=:g AND cik=:c ORDER BY accession"),
            {"g": generation, "c": cik},
        ).scalars()
    )
    for amendment in (row for row in filings if row["form"].endswith("/A")):
        data = rules.candidates(amendment, filings)
        accessions = [amendment["accession"], *[row["accession"] for row in data["candidates"]]]
        data["discovery_event_ids"] = {
            str(row[0]): str(row[1])
            for row in connection.execute(
                text("""
            SELECT accession,split_part(source_order,'/',2) FROM filings
            WHERE generation=:g AND accession=ANY(:ids) ORDER BY accession
        """),
                {"g": generation, "ids": accessions},
            ).all()
        }
        link_id = fingerprint({"generation": generation, "data": data})
        connection.execute(
            text(
                "INSERT INTO amendment_links(id,generation,amendment,data) VALUES (:id,:g,:a,CAST(:d AS jsonb)) ON CONFLICT DO NOTHING"
            ),
            {"id": link_id, "g": generation, "a": amendment["accession"], "d": canonical(data)},
        )
        connection.execute(
            text(
                "INSERT INTO amendment_heads VALUES (:g,:a,:id) ON CONFLICT(generation,amendment) DO UPDATE SET link_id=excluded.link_id"
            ),
            {"id": link_id, "g": generation, "a": amendment["accession"]},
        )
        # Only pointers change; prior evidence and explicit comparisons remain immutable.
        connection.execute(
            text("DELETE FROM reconciliation_heads WHERE generation=:g AND amendment=:a"),
            {"g": generation, "a": amendment["accession"]},
        )
        if data["status"] == "unique":
            original = data["candidates"][0]
            run_id = save_pair(connection, generation, original, amendment)
            connection.execute(
                text("INSERT INTO reconciliation_heads VALUES (:g,:o,:a,:id)"),
                {
                    "g": generation,
                    "o": original["accession"],
                    "a": amendment["accession"],
                    "id": run_id,
                },
            )


def reconcile(
    engine: Engine | Connection,
    original: str,
    amendment: str,
    generation: str = "active",
    original_event: str | None = None,
    amendment_event: str | None = None,
) -> str:
    from secrecon.db.projections import resolve_generation

    with transaction(engine) as connection:
        generation = resolve_generation(connection, generation)
        cik = connection.scalar(
            text("SELECT cik FROM filings WHERE generation=:g AND accession=:a"),
            {"g": generation, "a": original},
        )
        if not cik:
            raise ValueError("Original filing not found in selected generation")
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"{generation}:{cik}"},
        )
        filings = {
            row["accession"]: row
            for row in connection.execute(
                text("SELECT data FROM filings WHERE generation=:g AND accession=ANY(:ids)"),
                {"g": generation, "ids": [original, amendment]},
            ).scalars()
        }
        if len(filings) != 2:
            raise ValueError("Both distinct filings must exist in the selected generation")
        return save_pair(
            connection,
            generation,
            filings[original],
            filings[amendment],
            original_event,
            amendment_event,
        )


def get_run(
    connection: Connection, run_id: str, *, limit: int | None = None, after: str = ""
) -> dict[str, Any] | None:
    row = (
        connection.execute(
            text("SELECT generation,data FROM reconciliation_runs WHERE id=:id"), {"id": run_id}
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    if limit is not None and not 1 <= limit <= 200:
        raise ValueError("Comparison page must contain 1-200 keys")
    rows = list(
        connection.execute(
            text(
                "SELECT key_hash,data FROM fact_changes WHERE run_id=:id AND key_hash>:after ORDER BY key_hash LIMIT :limit"
            ),
            {"id": run_id, "after": after, "limit": limit + 1 if limit else None},
        ).mappings()
    )
    more = limit is not None and len(rows) > limit
    shown = rows[:limit] if limit else rows
    result = {
        "id": run_id,
        "generation": row["generation"],
        **row["data"],
        "changes": [r["data"] for r in shown],
    }
    if limit is not None:
        result["next_cursor"] = shown[-1]["key_hash"] if more else None
    return result


def canonical_heads(connection: Connection, generation: str) -> dict[str, list[Any]]:
    links = list(
        connection.execute(
            text(
                "SELECT l.data FROM amendment_heads h JOIN amendment_links l ON l.id=h.link_id WHERE h.generation=:g"
            ),
            {"g": generation},
        ).scalars()
    )
    ids = connection.execute(
        text("SELECT run_id FROM reconciliation_heads WHERE generation=:g"), {"g": generation}
    ).scalars()
    runs = []
    for run_id in ids:
        run = get_run(connection, run_id)
        assert run is not None
        runs.append({key: value for key, value in run.items() if key not in {"id", "generation"}})
    return {
        "amendment_links": sorted(links, key=canonical),
        "reconciliations": sorted(runs, key=canonical),
    }
