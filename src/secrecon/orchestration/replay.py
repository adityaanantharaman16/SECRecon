"""Offline rebuilding from a frozen manifest inventory into isolated generations."""

from typing import Any

from sqlalchemy import Connection, Engine, text

from secrecon.db.projections import generation_parser, process_source, register_source
from secrecon.db.reconciliation import canonical_heads
from secrecon.domain.reconciliation import COMPARISON_VERSION
from secrecon.domain.types import canonical, fingerprint
from secrecon.ingestion.adapters import PARSER_VERSION, PARSER_VERSIONS
from secrecon.jobs.store import enqueue
from secrecon.storage.archive import Archive, ArchiveIntegrityError


def inventory(engine: Engine, archive: Archive) -> int:
    """Repair object-store -> SQL crash boundaries and missing normalization work."""
    count = 0
    for manifest in archive.manifests():
        with engine.begin() as connection:
            register_source(connection, manifest)
            if manifest.complete and 200 <= manifest.status < 300:
                version = generation_parser(connection, "active")
                enqueue(
                    connection,
                    "normalize",
                    {"event_id": manifest.event_id, "generation": "active"},
                    f"normalize:active:{version}:{manifest.event_id}",
                    priority=0,
                )
                count += 1
    return count


def digest(connection: Connection, generation: str) -> dict[str, Any]:
    rows: dict[str, list[Any]] = {}
    for table in ("companies", "filings", "facts"):
        # Table identifiers come solely from this fixed tuple.
        data = connection.execute(
            text(f"SELECT data FROM {table} WHERE generation=:g"), {"g": generation}
        ).scalars()
        rows[table] = sorted(data, key=canonical)
    for table, columns in (
        ("fact_provenance", "fingerprint,event_id,locator,parser_version"),
        ("filing_sources", "accession,event_id,role"),
    ):
        mapped = connection.execute(
            text(f"SELECT {columns} FROM {table} WHERE generation=:g"), {"g": generation}
        ).mappings()
        rows[table] = sorted((dict(row) for row in mapped), key=canonical)
    rows.update(canonical_heads(connection, generation))
    return {"sha256": fingerprint(rows), "counts": {key: len(value) for key, value in rows.items()}}


def rebuild(
    engine: Engine,
    archive: Archive,
    generation: str,
    *,
    resume: bool = False,
    parser_version: str = PARSER_VERSION,
) -> dict[str, Any]:
    # Session lock prevents two CLI processes advancing the same replay checkpoint.
    with engine.connect() as lock:
        acquired = lock.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"),
            {"key": "replay:" + generation},
        )
        lock.commit()
        if not acquired:
            raise ValueError("This replay generation is already being processed")
        try:
            return _rebuild(
                engine, archive, generation, resume=resume, parser_version=parser_version
            )
        finally:
            lock.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                {"key": "replay:" + generation},
            )
            lock.commit()


def _rebuild(
    engine: Engine,
    archive: Archive,
    generation: str,
    *,
    resume: bool = False,
    parser_version: str = PARSER_VERSION,
) -> dict[str, Any]:
    if parser_version not in PARSER_VERSIONS:
        raise ValueError("Unsupported parser version")
    if generation in {"live", "active"} or not generation or len(generation) > 80:
        raise ValueError("Choose a new explicit generation name, not live/active")
    with engine.begin() as connection:
        current_active = connection.scalar(
            text("SELECT value FROM system_state WHERE key='active_generation'")
        )
        if current_active == generation:
            raise ValueError("Cannot rebuild the active generation; choose a fresh target")
        existing = (
            connection.execute(text("SELECT * FROM replays WHERE generation=:g"), {"g": generation})
            .mappings()
            .first()
        )
        if existing:
            if (
                not resume
                or existing["parser_version"] != parser_version
                or existing["comparison_version"] != COMPARISON_VERSION
            ):
                raise ValueError(
                    "Replay exists; resume requires the same parser and comparison versions"
                )
            ids, position = existing["event_ids"], existing["position"]
        else:
            manifests = sorted(archive.manifests(), key=lambda m: (m.fetched_at, m.event_id))
            ids, position = [m.event_id for m in manifests], 0
            connection.execute(
                text("INSERT INTO generations(name,parser_version) VALUES (:g,:p)"),
                {"g": generation, "p": parser_version},
            )
            connection.execute(
                text("""
                INSERT INTO replays(generation,parser_version,event_ids,comparison_version) VALUES (:g,:p,CAST(:ids AS jsonb),:comparison)
            """),
                {
                    "g": generation,
                    "p": parser_version,
                    "ids": canonical(ids),
                    "comparison": COMPARISON_VERSION,
                },
            )
    try:
        # Revalidate the full inventory on resume, including previously processed events.
        for event_id in ids:
            archive.load(archive.get_manifest(event_id))
        for offset in range(position, len(ids)):
            manifest = archive.get_manifest(ids[offset])
            with engine.begin() as connection:
                register_source(connection, manifest)
            if manifest.complete and 200 <= manifest.status < 300:
                process_source(engine, archive, manifest, generation)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE replays SET position=:position,state='running',error=NULL WHERE generation=:g"
                    ),
                    {"position": offset + 1, "g": generation},
                )
        with engine.begin() as connection:
            result = digest(connection, generation)
            connection.execute(
                text("""
                UPDATE replays SET state='ready',digest=:digest,completed_at=now() WHERE generation=:g
            """),
                {"digest": result["sha256"], "g": generation},
            )
            connection.execute(
                text("UPDATE generations SET status='ready',digest=:digest WHERE name=:g"),
                {"digest": result["sha256"], "g": generation},
            )
            return result
    except Exception as exc:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE replays SET state='failed',error=:error WHERE generation=:g"),
                {"g": generation, "error": type(exc).__name__ + ": " + str(exc)[:500]},
            )
        raise


def promote(engine: Engine, generation: str, expected_digest: str, *, archive: Archive) -> None:
    with engine.connect() as check:
        ids = check.scalar(
            text("SELECT event_ids FROM replays WHERE generation=:g"), {"g": generation}
        )
    if ids is None:
        raise ValueError("Replay generation does not exist")
    for event_id in ids:
        archive.load(archive.get_manifest(event_id))
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('projection-active',0))")
        )
        replay = (
            connection.execute(
                text("SELECT * FROM replays WHERE generation=:g FOR UPDATE"), {"g": generation}
            )
            .mappings()
            .one()
        )
        if (
            replay["state"] != "ready"
            or replay["comparison_version"] != COMPARISON_VERSION
            or digest(connection, generation)["sha256"] != expected_digest
        ):
            raise ArchiveIntegrityError("Replay is not ready or expected digest differs")
        previous = connection.scalar(
            text("SELECT value FROM system_state WHERE key='active_generation' FOR UPDATE")
        )
        unseen = connection.scalar(
            text("""
            SELECT count(*) FROM processing_runs WHERE generation=:old AND status='succeeded'
            AND NOT (event_id = ANY(:ids))
        """),
            {"old": previous, "ids": replay["event_ids"]},
        )
        if unseen:
            raise ArchiveIntegrityError(
                "Active projection advanced after the replay inventory; rebuild from a fresh inventory"
            )
        connection.execute(
            text("UPDATE generations SET status='ready' WHERE name=:old"), {"old": previous}
        )
        connection.execute(
            text("UPDATE generations SET status='active' WHERE name=:g"), {"g": generation}
        )
        connection.execute(
            text("UPDATE system_state SET value=:g WHERE key='active_generation'"),
            {"g": generation},
        )
