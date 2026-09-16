import pytest
from sqlalchemy import text

from secrecon.db.projections import fact_provenance, process_source
from secrecon.domain.types import canonical
from secrecon.orchestration.replay import digest

pytestmark = pytest.mark.integration


def test_real_pair_five_deliveries_preserve_digest_and_both_document_links(
    engine, archive, generation, recorded_sources
):
    sources = recorded_sources("0001783879")
    # Import preserved manifests without pretending that today's replay is a new SEC fetch.
    for source, body in sources:
        archive.put_once(source.blob_key, body, "application/octet-stream")
        archive.put_once(
            f"events/{source.event_id}.json",
            canonical(source.model_dump()).encode(),
            "application/json",
        )
    baseline = None
    for _ in range(5):
        for source, _ in sources:
            process_source(engine, archive, source, generation)
        with engine.connect() as connection:
            current = digest(connection, generation)
            if baseline is None:
                baseline = current
            assert current == baseline
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM processing_runs WHERE generation=:g"), {"g": generation}
            )
            == 4
        )
        for accession in ("0001783879-26-000023", "0001783879-26-000029"):
            rows = connection.execute(
                text("""
                SELECT fingerprint FROM facts WHERE generation=:g AND accession=:a
                AND data->>'concept'='Assets' AND data->>'end_date'='2025-12-31'
            """),
                {"g": generation, "a": accession},
            ).all()
            assert len(rows) == 1
            provenance = fact_provenance(connection, generation, rows[0][0])
            assert provenance["fact"]["value"] == "38137000000"
            facts_source = next(m for m, _ in sources if m.kind == "facts")
            assert provenance["sources"][0]["manifest"]["sha256"] == facts_source.sha256
            document = next(m for m, _ in sources if m.accession == accession)
            assert provenance["documents"][0]["event_id"] == document.event_id
            assert archive.load(document) == next(b for m, b in sources if m == document)
