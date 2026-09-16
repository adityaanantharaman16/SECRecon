import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from secrecon.api.app import create_app
from secrecon.db.generations import generation_report
from secrecon.db.projections import ProjectionVersionChanged, apply_projection, process_source
from secrecon.db.reconciliation import get_run, reconcile
from secrecon.domain.types import canonical
from secrecon.ingestion.adapters import SchemaError, parse
from secrecon.orchestration.replay import digest, rebuild

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).parents[1] / "fixtures" / "synthetic"
ORIGINAL, AMENDMENT = "0001234567-25-000001", "0001234567-25-000002"


def preserve(archive, filename, kind, body=None):
    return archive.preserve(
        body if body is not None else (FIXTURES / filename).read_bytes(),
        kind=kind,
        cik="0001234567",
        url="https://data.sec.gov/SYNTHETIC-M4",
    )


def get(engine, run_id):
    with engine.connect() as connection:
        return get_run(connection, run_id)


def test_explicit_snapshots_revision_history_and_api(engine, archive, generation):
    discovery = preserve(archive, "submissions.json", "submissions")
    original = preserve(archive, "facts.json", "facts")
    for source in (discovery, original):
        process_source(engine, archive, source, generation)
    first = reconcile(engine, ORIGINAL, AMENDMENT, generation)
    assert get(engine, first)["changes"][0]["delta"] == "1"
    assert first == reconcile(engine, ORIGINAL, AMENDMENT, generation)
    changed = preserve(
        archive,
        "facts.json",
        "facts",
        archive.load(original).replace(b"12345678901234567891.1234", b"42"),
    )
    process_source(engine, archive, changed, generation)
    second = reconcile(engine, ORIGINAL, AMENDMENT, generation)
    assert second != first and get(engine, first)["changes"][0]["delta"] == "1"
    assert get(engine, second)["amendment_event_id"] == changed.event_id
    # Choosing the old preserved snapshot recreates the original result, not an overwrite.
    assert (
        reconcile(engine, ORIGINAL, AMENDMENT, generation, original.event_id, original.event_id)
        == first
    )
    with pytest.raises(ValueError, match="snapshot"):
        reconcile(engine, ORIGINAL, AMENDMENT, generation, discovery.event_id)
    with TestClient(create_app()) as client:
        response = client.get(f"/v1/reconciliations/{first}")
        assert response.status_code == 200 and response.json()["changes"][0]["status"] == "changed"
        assert client.get("/v1/reconciliations/unknown").status_code == 404
        link = client.get("/v1/amendments", params={"generation": generation}).json()["items"][0]
        assert link["data"]["status"] == "unique" and link["run_id"] == second


def test_amendment_without_facts_and_conflicting_observations(engine, archive, generation):
    process_source(
        engine, archive, preserve(archive, "submissions.json", "submissions"), generation
    )
    payload = json.loads((FIXTURES / "facts.json").read_bytes())
    entries = payload["facts"]["us-gaap"]["Assets"]["units"]["USD"]
    entries.pop()
    source = preserve(archive, "", "facts", canonical(payload).encode())
    process_source(engine, archive, source, generation)
    run = get(engine, reconcile(engine, ORIGINAL, AMENDMENT, generation))
    assert run["coverage"]["status"] == "incomplete"
    assert run["counts"] == {"only_in_original": 1}
    entries.append({**entries[0], "val": 7})
    conflict = preserve(archive, "", "facts", canonical(payload).encode())
    process_source(engine, archive, conflict, generation)
    result = get(engine, reconcile(engine, ORIGINAL, AMENDMENT, generation))
    assert result["counts"] == {"ambiguous": 1}
    assert result["changes"][0]["delta"] is None


def test_ambiguous_link_stays_unresolved_but_explicit_pair_is_available(
    engine, archive, generation
):
    payload = json.loads((FIXTURES / "submissions.json").read_bytes())
    columns = payload["filings"]["recent"]
    for values in columns.values():
        values.append(values[0])
    columns["accessionNumber"][-1] = "0001234567-25-000003"
    source = preserve(archive, "", "submissions", canonical(payload).encode())
    process_source(engine, archive, source, generation)
    with engine.connect() as connection:
        link = connection.scalar(
            text("SELECT data FROM amendment_links WHERE generation=:g"), {"g": generation}
        )
        assert link["status"] == "ambiguous" and len(link["candidates"]) == 2
        assert (
            connection.scalar(
                text("SELECT count(*) FROM reconciliation_heads WHERE generation=:g"),
                {"g": generation},
            )
            == 0
        )
    assert (
        get(engine, reconcile(engine, ORIGINAL, AMENDMENT, generation))["coverage"]["status"]
        == "incomplete"
    )


def test_schema_change_quarantine_versioned_replay_and_generation_report(
    engine, archive, generation
):
    discovery = preserve(archive, "submissions.json", "submissions")
    facts = preserve(archive, "facts.json", "facts")
    for source in (discovery, facts):
        process_source(engine, archive, source, generation)
    optional_body = archive.load(facts).replace(
        b'"facts":', b'"futureMetadata":{"version":2},"facts":'
    )
    optional = preserve(archive, "", "facts", optional_body)
    process_source(engine, archive, optional, generation)
    evolved = preserve(
        archive,
        "",
        "facts",
        optional_body.replace(b"12345678901234567891.1234", b'"12345678901234567891.1234"'),
    )
    with pytest.raises(SchemaError):
        process_source(engine, archive, evolved, generation)
    with engine.connect() as connection:
        problem = connection.scalar(
            text("SELECT diagnostics FROM quarantine_records WHERE generation=:g AND event_id=:id"),
            {"g": generation, "id": evolved.event_id},
        )
        assert problem[0]["code"] == "invalid_numeric_type" and problem[0]["path"].endswith(
            "/1/val"
        )
        warnings = connection.scalar(
            text("SELECT diagnostics FROM source_diagnostics WHERE generation=:g AND event_id=:id"),
            {"g": generation, "id": optional.event_id},
        )
        assert warnings[0]["code"] == "unknown_optional_field"
        old_digest = digest(connection, generation)
    with pytest.raises(SchemaError):
        rebuild(engine, archive, "v1-" + uuid4().hex)
    target = "v2-" + uuid4().hex
    rebuild(engine, archive, target, parser_version="sec-json-v2")
    with engine.connect() as connection:
        assert digest(connection, generation) == old_digest
        report = generation_report(connection, generation, target)
        assert report["unchanged_assertions"] == 2
        assert report["only_in_original"] == report["only_in_target"] == []
        assert report["sources_only_in_target"] == [evolved.event_id]
        assert not report["same_successful_source_inventory"]
        assert (
            connection.scalar(
                text("SELECT count(*) FROM quarantine_records WHERE generation=:g"),
                {"g": generation},
            )
            == 1
        )
    # Preparing under v1 cannot commit to a v2 generation after promotion.
    with engine.begin() as connection, pytest.raises(ProjectionVersionChanged):
        apply_projection(connection, facts, parse(facts, archive.load(facts)), target)
    with pytest.raises(ValueError, match="same parser"):
        rebuild(engine, archive, target, resume=True)


def test_real_pair_reconciles_unchanged_with_snapshot_and_document_evidence(
    engine, archive, generation, recorded_sources
):
    for source, body in recorded_sources("0001783879"):
        archive.put_once(source.blob_key, body, "application/octet-stream")
        archive.put_once(
            f"events/{source.event_id}.json",
            canonical(source.model_dump()).encode(),
            "application/json",
        )
        process_source(engine, archive, source, generation)
    run = get(engine, reconcile(engine, "0001783879-26-000023", "0001783879-26-000029", generation))
    assert set(run["counts"]) == {"unchanged"} and run["counts"]["unchanged"] > 500
    assert run["coverage"]["status"] == "available"
    assert len(run["evidence"]["original"]["documents"]) == 1
    assert len(run["evidence"]["amendment"]["documents"]) == 1
    target = "real-replay-" + uuid4().hex
    rebuilt = rebuild(engine, archive, target)
    with engine.connect() as connection:
        assert rebuilt == digest(connection, generation)


def test_late_snapshot_and_concurrent_comparisons_converge(engine, archive, generation):
    discovery = preserve(archive, "submissions.json", "submissions")
    old = preserve(archive, "facts.json", "facts")
    latest = preserve(
        archive, "", "facts", archive.load(old).replace(b"12345678901234567891.1234", b"42")
    )
    for source in (discovery, latest, old):
        process_source(engine, archive, source, generation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(
            pool.map(lambda _: reconcile(engine, ORIGINAL, AMENDMENT, generation), range(6))
        )
    assert len(set(runs)) == 1
    result = get(engine, runs[0])
    assert result["original_event_id"] == result["amendment_event_id"] == latest.event_id
    assert len(result["changes"][0]["amendment"]) == 1  # no mixing old and new snapshot values
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM reconciliation_runs WHERE id=:id"), {"id": runs[0]}
            )
            == 1
        )
