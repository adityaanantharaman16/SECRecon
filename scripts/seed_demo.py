"""Load explicitly synthetic sample sources into the selected local environment."""

import json
from pathlib import Path

from sqlalchemy import text

from secrecon.db.projections import process_source
from secrecon.orchestration.replay import digest
from secrecon.runtime import services

with services() as deps:
    deps.archive.initialize()
    for filename, kind, accession in (
        ("submissions.json", "submissions", None),
        ("facts.json", "facts", None),
        ("original.htm", "document", "0001234567-25-000001"),
    ):
        source = deps.archive.preserve(
            (Path("tests/fixtures/synthetic") / filename).read_bytes(),
            kind=kind,
            cik="0001234567",
            url="https://data.sec.gov/SYNTHETIC-DEMO",
            accession=accession,
        )
        process_source(deps.engine, deps.archive, source)
    with deps.engine.connect() as connection:
        generation = connection.scalar(
            text("SELECT value FROM system_state WHERE key='active_generation'")
        )
        print(
            json.dumps(
                {
                    "synthetic_demo": True,
                    "generation": generation,
                    **digest(connection, generation),
                },
                indent=2,
            )
        )
