"""Core job handlers. Side effects are archive appends or fenced SQL commits."""

from collections.abc import Callable

from sqlalchemy import Connection, Engine

from secrecon.db.projections import apply_projection, quarantine, register_source
from secrecon.ingestion.adapters import PARSER_VERSION, SchemaError, parse
from secrecon.ingestion.client import SecClient
from secrecon.jobs.store import Lease, enqueue
from secrecon.storage.archive import Archive, ArchiveIntegrityError


class CoreHandlers:
    def __init__(self, engine: Engine, archive: Archive, client: SecClient) -> None:
        self.engine, self.archive, self.client = engine, archive, client

    def __call__(self, lease: Lease) -> Callable[[Connection], None]:
        payload = lease.payload
        if lease.kind == "normalize":
            source = self.archive.get_manifest(payload["event_id"])
            generation = payload.get("generation", "live")
            try:
                parsed = parse(source, self.archive.load(source))
            except (SchemaError, ArchiveIntegrityError) as exc:
                with self.engine.begin() as connection:
                    quarantine(connection, source, generation, str(exc))
                raise
            return lambda connection: apply_projection(connection, source, parsed, generation)
        if lease.kind == "fetch":
            source = self.client.fetch(
                payload["url"], payload["kind"], payload["cik"], payload.get("accession")
            )

            def commit(connection: Connection) -> None:
                register_source(connection, source)
                enqueue(
                    connection,
                    "normalize",
                    {"event_id": source.event_id, "generation": "live"},
                    f"normalize:live:{PARSER_VERSION}:{source.event_id}",
                    priority=10,
                )

            return commit
        raise ValueError(f"Unsupported job kind: {lease.kind}")
