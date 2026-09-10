"""Transaction-scoped projection writes; callers own commit boundaries."""

from typing import Any

from sqlalchemy import Connection, Engine, text

from secrecon.domain.types import canonical
from secrecon.ingestion.adapters import PARSER_VERSION, ParsedSource, SchemaError, parse
from secrecon.storage.archive import Archive, ArchiveIntegrityError, Manifest


def resolve_generation(connection: Connection, generation: str) -> str:
    if generation == "active":
        connection.execute(
            text("SELECT pg_advisory_xact_lock_shared(hashtextextended('projection-active',0))")
        )
        return str(
            connection.scalar(text("SELECT value FROM system_state WHERE key='active_generation'"))
        )
    return generation


def register_source(connection: Connection, manifest: Manifest) -> None:
    connection.execute(
        text("""
        INSERT INTO source_events(event_id,manifest,fetched_at,sha256)
        VALUES (:id,CAST(:manifest AS jsonb),:at,:sha) ON CONFLICT DO NOTHING
    """),
        {
            "id": manifest.event_id,
            "manifest": canonical(manifest.model_dump()),
            "at": manifest.fetched_at,
            "sha": manifest.sha256,
        },
    )
    stored = connection.scalar(
        text("SELECT manifest FROM source_events WHERE event_id=:id"), {"id": manifest.event_id}
    )
    if stored != manifest.model_dump():
        raise ArchiveIntegrityError("Source event identity conflict")


def apply_projection(
    connection: Connection, manifest: Manifest, parsed: ParsedSource, generation: str = "active"
) -> None:
    generation = resolve_generation(connection, generation)
    register_source(connection, manifest)
    # Per-company serialization is intentionally coarse at the demo watchlist size.
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"{generation}:{manifest.cik}"},
    )
    status = connection.scalar(
        text("""
        SELECT status FROM processing_runs WHERE generation=:gen
        AND event_id=:id AND parser_version=:parser
    """),
        {"gen": generation, "id": manifest.event_id, "parser": PARSER_VERSION},
    )
    if status == "succeeded":
        return
    order = manifest.fetched_at + "/" + manifest.event_id
    if manifest.kind == "submissions" and parsed.company.get("name"):
        connection.execute(
            text("""
            INSERT INTO companies VALUES (:gen,:cik,CAST(:data AS jsonb),:ord)
            ON CONFLICT(generation,cik) DO UPDATE SET
              data=companies.data || excluded.data, source_order=excluded.source_order
            WHERE excluded.source_order > companies.source_order
        """),
            {
                "gen": generation,
                "cik": manifest.cik,
                "data": canonical(parsed.company),
                "ord": order,
            },
        )
    links: set[tuple[str, str]] = set()
    for filing in parsed.filings:
        connection.execute(
            text("""
            INSERT INTO filings VALUES (:gen,:acc,:cik,:form,:date,CAST(:data AS jsonb),:ord)
            ON CONFLICT(generation,accession) DO UPDATE SET data=excluded.data,
              form=excluded.form, filing_date=excluded.filing_date, source_order=excluded.source_order
            WHERE excluded.source_order > filings.source_order
        """),
            {
                "gen": generation,
                "acc": filing["accession"],
                "cik": manifest.cik,
                "form": filing["form"],
                "date": filing["filing_date"],
                "data": canonical(filing),
                "ord": order,
            },
        )
        links.add((filing["accession"], "discovery"))
    for fact in parsed.facts:
        data = {key: value for key, value in fact.items() if key not in {"locator", "fingerprint"}}
        connection.execute(
            text("""
            INSERT INTO facts VALUES (:gen,:fp,:cik,:acc,:concept,:value,CAST(:data AS jsonb))
            ON CONFLICT DO NOTHING
        """),
            {
                "gen": generation,
                "fp": fact["fingerprint"],
                "cik": manifest.cik,
                "acc": fact["accession"],
                "concept": fact["concept"],
                "value": fact["value"],
                "data": canonical(data),
            },
        )
        connection.execute(
            text("""
            INSERT INTO fact_provenance VALUES (:gen,:fp,:event,:locator,:parser)
            ON CONFLICT DO NOTHING
        """),
            {
                "gen": generation,
                "fp": fact["fingerprint"],
                "event": manifest.event_id,
                "locator": fact["locator"],
                "parser": PARSER_VERSION,
            },
        )
        links.add((fact["accession"], "facts"))
    if manifest.kind == "document" and manifest.accession:
        links.add((manifest.accession, "document"))
    for accession, role in sorted(links):
        connection.execute(
            text("INSERT INTO filing_sources VALUES (:gen,:acc,:id,:role) ON CONFLICT DO NOTHING"),
            {"gen": generation, "acc": accession, "id": manifest.event_id, "role": role},
        )
    connection.execute(
        text("""
        INSERT INTO processing_runs(generation,event_id,parser_version,status,fact_count)
        VALUES (:gen,:id,:parser,'succeeded',:count) ON CONFLICT DO NOTHING
    """),
        {
            "gen": generation,
            "id": manifest.event_id,
            "parser": PARSER_VERSION,
            "count": len(parsed.facts),
        },
    )


def quarantine(connection: Connection, manifest: Manifest, generation: str, reason: str) -> None:
    generation = resolve_generation(connection, generation)
    register_source(connection, manifest)
    connection.execute(
        text("""
        INSERT INTO quarantine_records(generation,event_id,parser_version,reason)
        VALUES (:gen,:id,:parser,:reason) ON CONFLICT DO NOTHING
    """),
        {
            "gen": generation,
            "id": manifest.event_id,
            "parser": PARSER_VERSION,
            "reason": reason[:2000],
        },
    )


def process_source(
    engine: Engine, archive: Archive, manifest: Manifest, generation: str = "active"
) -> ParsedSource:
    try:
        parsed = parse(manifest, archive.load(manifest))
    except (SchemaError, ArchiveIntegrityError) as exc:
        with engine.begin() as connection:
            quarantine(connection, manifest, generation, str(exc))
        raise
    with engine.begin() as connection:
        apply_projection(connection, manifest, parsed, generation)
    return parsed


def fact_provenance(connection: Connection, generation: str, fact_id: str) -> dict[str, Any] | None:
    fact = (
        connection.execute(
            text("""
        SELECT data,accession FROM facts WHERE generation=:gen AND fingerprint=:id
    """),
            {"gen": generation, "id": fact_id},
        )
        .mappings()
        .first()
    )
    if fact is None:
        return None
    sources = (
        connection.execute(
            text("""
        SELECT p.locator,p.parser_version,s.manifest
        FROM fact_provenance p JOIN source_events s USING(event_id)
        WHERE p.generation=:gen AND p.fingerprint=:id ORDER BY s.fetched_at,s.event_id
    """),
            {"gen": generation, "id": fact_id},
        )
        .mappings()
        .all()
    )
    documents = (
        connection.execute(
            text("""
        SELECT s.manifest FROM filing_sources f JOIN source_events s USING(event_id)
        WHERE f.generation=:gen AND f.accession=:acc AND f.role='document'
        ORDER BY s.fetched_at,s.event_id
    """),
            {"gen": generation, "acc": fact["accession"]},
        )
        .scalars()
        .all()
    )
    return {
        "fact": fact["data"],
        "sources": [dict(row) for row in sources],
        "documents": list(documents),
    }
