from pathlib import Path

import pytest
from sqlalchemy import text

from secrecon.db.projections import fact_provenance, process_source
from secrecon.ingestion.adapters import SchemaError
from secrecon.storage.archive import ArchiveIntegrityError

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).parents[1] / "fixtures" / "synthetic"


def preserve(archive, name, kind, **extra):
    return archive.preserve(
        (FIXTURES / name).read_bytes(),
        kind=kind,
        url="https://data.sec.gov/test",
        cik="0001234567",
        **extra,
    )


def test_duplicates_provenance_and_precision(engine, archive, generation):
    discovery = preserve(archive, "submissions.json", "submissions")
    facts = preserve(archive, "facts.json", "facts")
    document = preserve(archive, "original.htm", "document", accession="0001234567-25-000001")
    for _ in range(5):
        for source in (discovery, facts, document):
            process_source(engine, archive, source, generation)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM facts WHERE generation=:g"), {"g": generation}
            )
            == 2
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM processing_runs WHERE generation=:g"), {"g": generation}
            )
            == 3
        )
        fact_id = connection.scalar(
            text(
                "SELECT fingerprint FROM facts WHERE generation=:g AND accession='0001234567-25-000001'"
            ),
            {"g": generation},
        )
        provenance = fact_provenance(connection, generation, fact_id)
        assert provenance["fact"]["value"] == "12345678901234567890.1234"
        assert provenance["sources"][0]["manifest"]["sha256"] == facts.sha256
        assert provenance["sources"][0]["locator"].endswith("/USD/0")
        assert provenance["documents"][0]["event_id"] == document.event_id


def test_conditional_create_is_atomic_and_detects_collision(archive):
    archive.put_once("probe", b"original", "text/plain")
    archive.put_once("probe", b"original", "text/plain")
    with pytest.raises(ArchiveIntegrityError):
        archive.put_once("probe", b"changed", "text/plain")
    assert archive.read("probe") == b"original"


def test_bad_source_is_preserved_and_atomically_quarantined(engine, archive, generation):
    body = (
        (FIXTURES / "facts.json").read_bytes().replace(b"12345678901234567891.1234", b'"invalid"')
    )
    source = archive.preserve(body, kind="facts", url="https://data.sec.gov/test", cik="0001234567")
    with pytest.raises(SchemaError):
        process_source(engine, archive, source, generation)
    assert archive.load(source) == body
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM facts WHERE generation=:g"), {"g": generation}
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM quarantine_records WHERE generation=:g"),
                {"g": generation},
            )
            == 1
        )


def test_conflicting_values_are_retained(engine, archive, generation):
    source = preserve(archive, "facts.json", "facts")
    process_source(engine, archive, source, generation)
    body = archive.load(source).replace(b"12345678901234567890.1234", b"42")
    changed = archive.preserve(body, kind="facts", url=source.url, cik=source.cik)
    process_source(engine, archive, changed, generation)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM facts WHERE generation=:g"), {"g": generation}
            )
            == 3
        )


def test_manifest_is_commit_marker_and_survives_sql_failure(
    engine, archive, generation, monkeypatch
):
    source = preserve(archive, "facts.json", "facts")
    # No SQL registration yet: discovery from immutable inventory repairs the boundary.
    assert archive.get_manifest(source.event_id) == source
    process_source(engine, archive, list(archive.manifests())[0], generation)
    original = archive.put_once

    def fail_manifest(key, body, media_type):
        if key.startswith("events/"):
            raise RuntimeError("injected object-store failure")
        original(key, body, media_type)

    monkeypatch.setattr(archive, "put_once", fail_manifest)
    with pytest.raises(RuntimeError):
        preserve(archive, "original.htm", "document")
    assert len(list(archive.manifests())) == 1


def test_corruption_never_commits(engine, archive, generation):
    source = preserve(archive, "facts.json", "facts")
    archive.client.put_object(
        Bucket=archive.bucket, Key=source.blob_key, Body=b"corrupted test copy"
    )
    with pytest.raises(ArchiveIntegrityError):
        process_source(engine, archive, source, generation)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM facts WHERE generation=:g"), {"g": generation}
            )
            == 0
        )
