"""Core job handlers. Side effects are archive appends or fenced SQL commits."""

from collections.abc import Callable

from sqlalchemy import Connection, Engine

from secrecon.db.projections import apply_projection, quarantine, register_source
from secrecon.ingestion.adapters import PARSER_VERSION, SchemaError, parse
from secrecon.ingestion.client import SecClient
from secrecon.jobs.store import Lease, enqueue
from secrecon.orchestration import planner
from secrecon.storage.archive import Archive, ArchiveIntegrityError


class CoreHandlers:
    def __init__(self, engine: Engine, archive: Archive, client: SecClient) -> None:
        self.engine, self.archive, self.client = engine, archive, client

    def __call__(self, lease: Lease) -> Callable[[Connection], None]:
        payload = lease.payload
        if payload.get("backfill_id"):
            with self.engine.begin() as connection:
                planner.assert_operation_active(connection, payload)
        if lease.kind == "normalize":
            source = self.archive.get_manifest(payload["event_id"])
            generation = payload.get("generation", "active")
            try:
                parsed = parse(source, self.archive.load(source))
            except (SchemaError, ArchiveIntegrityError) as exc:
                with self.engine.begin() as connection:
                    quarantine(connection, source, generation, str(exc))
                raise

            def normalize(connection: Connection) -> None:
                apply_projection(connection, source, parsed, generation)
                if source.kind == "facts" and generation == "active":
                    planner.after_facts(connection, source, parsed)

            return normalize
        if lease.kind in {"fetch", "discover"}:
            source = self.client.fetch(
                payload["url"],
                "submissions" if lease.kind == "discover" else payload["kind"],
                payload["cik"],
                payload.get("accession"),
                payload.get("backfill_id"),
            )
            discovered = (
                parse(source, self.archive.load(source)) if lease.kind == "discover" else None
            )

            def commit(connection: Connection) -> None:
                planner.assert_operation_active(connection, payload)
                register_source(connection, source)
                enqueue(
                    connection,
                    "normalize",
                    {"event_id": source.event_id, "generation": "active"},
                    f"normalize:active:{PARSER_VERSION}:{source.event_id}",
                    priority=10,
                )
                if discovered is not None:
                    apply_projection(connection, source, discovered)
                    planner.after_discovery(connection, source, discovered, payload, lease.job_id)

            return commit
        raise ValueError(f"Unsupported job kind: {lease.kind}")
