"""Core job handlers. Side effects are archive appends or fenced SQL commits."""

from collections.abc import Callable

from sqlalchemy import Connection, Engine

from secrecon.db.projections import apply_projection, generation_parser, quarantine, register_source
from secrecon.ingestion.adapters import PARSER_VERSION, ParsedSource, SchemaError, parse
from secrecon.ingestion.client import SecClient
from secrecon.jobs.store import Lease, enqueue
from secrecon.orchestration import planner
from secrecon.storage.archive import Archive, ArchiveIntegrityError, Manifest


class CoreHandlers:
    def __init__(self, engine: Engine, archive: Archive, client: SecClient) -> None:
        self.engine, self.archive, self.client = engine, archive, client

    def prepare_source(self, source: Manifest, generation: str) -> ParsedSource:
        with self.engine.begin() as connection:
            version = generation_parser(connection, generation)
        try:
            return parse(source, self.archive.load(source), version)
        except (SchemaError, ArchiveIntegrityError) as exc:
            with self.engine.begin() as connection:
                quarantine(connection, source, generation, exc, version)
            raise

    def __call__(self, lease: Lease) -> Callable[[Connection], None]:
        payload = lease.payload
        if lease.kind == "operation":
            from secrecon.orchestration.operations import prepare

            return prepare(self.engine, self.archive, lease)
        if payload.get("backfill_id"):
            with self.engine.begin() as connection:
                planner.assert_operation_active(connection, payload)
        if lease.kind == "normalize":
            source = self.archive.get_manifest(payload["event_id"])
            generation = payload.get("generation", "active")
            parsed = self.prepare_source(source, generation)

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
            discovered = self.prepare_source(source, "active") if lease.kind == "discover" else None

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
