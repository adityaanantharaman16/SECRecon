import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from redis import Redis
from sqlalchemy import text
from test_jobs import expire

from secrecon.config import Settings
from secrecon.db.projections import process_source
from secrecon.ingestion.client import SecClient
from secrecon.ingestion.rate_limit import RateLimiter
from secrecon.jobs import store
from secrecon.jobs.handlers import CoreHandlers
from secrecon.jobs.queue import Queue
from secrecon.jobs.worker import Worker
from secrecon.orchestration import planner
from secrecon.orchestration.replay import digest, inventory, promote, rebuild
from secrecon.storage.archive import ArchiveIntegrityError

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).parents[1] / "fixtures/synthetic"


@pytest.fixture
def active(engine, generation):
    with engine.begin() as connection:
        previous = connection.scalar(
            text("SELECT value FROM system_state WHERE key='active_generation'")
        )
        connection.execute(
            text("UPDATE system_state SET value=:g WHERE key='active_generation'"),
            {"g": generation},
        )
    yield generation
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE system_state SET value=:g WHERE key='active_generation'"), {"g": previous}
        )


def fake_sec(engine, archive, *, facts_available=True):
    root = json.loads((FIXTURES / "submissions.json").read_bytes())
    root["filings"]["recent"] = {
        key: [values[1]] for key, values in root["filings"]["recent"].items()
    }
    root["filings"]["files"] = [
        {
            "name": "CIK0001234567-submissions-001.json",
            "filingFrom": "2025-01-01",
            "filingTo": "2025-02-28",
        }
    ]
    historical = json.loads((FIXTURES / "submissions.json").read_bytes())["filings"]["recent"]
    historical = {key: [values[0]] for key, values in historical.items()}
    state = {"facts_available": facts_available, "requests": []}

    def respond(request):
        state["requests"].append(str(request.url))
        if "companyfacts" in str(request.url):
            body = (
                (FIXTURES / "facts.json").read_bytes()
                if state["facts_available"]
                else b'{"cik":1234567,"facts":{"us-gaap":{}}}'
            )
        elif "submissions-001" in str(request.url):
            body = json.dumps(historical).encode()
        elif "/submissions/" in str(request.url):
            body = json.dumps(root).encode()
        else:
            body = (FIXTURES / "original.htm").read_bytes()
        return httpx.Response(200, content=body)

    settings = Settings().model_copy(
        update={"sec_mode": "live", "sec_user_agent": "SECRecon tests@example.invalid"}
    )
    redis = Redis.from_url(settings.redis_url)
    client = SecClient(
        settings,
        engine,
        archive,
        RateLimiter(redis, namespace="test:" + uuid4().hex),
        transport=httpx.MockTransport(respond),
    )
    handler = CoreHandlers(engine, archive, client)
    worker = Worker(
        engine, Queue(redis, stream="test:" + uuid4().hex), settings, handler, "test-worker"
    )
    return client, handler, worker, state


def next_operation_job(engine, operation):
    with engine.connect() as connection:
        return connection.scalar(
            text("""
            SELECT id FROM jobs WHERE (payload->>'backfill_id'=:id
              OR id IN (SELECT job_id FROM backfill_normalizations WHERE backfill_id=:id))
              AND state IN ('queued','retry_wait') AND due_at<=now()
            ORDER BY priority DESC,created_at,id LIMIT 1
        """),
            {"id": operation},
        )


def drain(engine, worker, operation):
    for _ in range(50):
        job = next_operation_job(engine, operation)
        if not job:
            break
        assert worker.process(job)
    planner.refresh_backfills(engine)
    with engine.connect() as connection:
        states = list(
            connection.execute(
                text("SELECT state,error FROM jobs WHERE payload->>'backfill_id'=:id"),
                {"id": operation},
            ).mappings()
        )
        assert all(row["state"] == "succeeded" for row in states), states
        assert (
            connection.scalar(text("SELECT state FROM backfills WHERE id=:id"), {"id": operation})
            == "completed"
        )


def test_crashed_multipage_backfill_overlap_and_pending_facts(engine, archive, active):
    client, handler, worker, upstream = fake_sec(engine, archive, facts_available=False)
    try:
        operation = planner.create_backfill(
            engine, ["1234567"], date(2025, 1, 1), date(2025, 3, 31)
        )
        root = next_operation_job(engine, operation)
        lease = store.claim(engine, root, "crashed", 60)
        handler(lease)  # HTTP archived, then the worker dies before checkpoint/child commit.
        expire(engine, root)
        store.sweep(engine)
        drain(engine, worker, operation)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM backfill_pages WHERE backfill_id=:id"),
                    {"id": operation},
                )
                == 2
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM facts WHERE generation=:g"), {"g": active}
                )
                == 0
            )
        upstream["facts_available"] = True
        # A second, overlapping request sees the later facts snapshot.
        overlap = planner.create_backfill(engine, ["1234567"], date(2025, 1, 1), date(2025, 3, 31))
        drain(engine, worker, overlap)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM facts WHERE generation=:g"), {"g": active}
                )
                == 2
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM filings WHERE generation=:g"), {"g": active}
                )
                == 2
            )
            assert (
                connection.scalar(
                    text("SELECT state FROM enrichment WHERE accession='0001234567-25-000001'")
                )
                == "available"
            )
    finally:
        client.close()


def test_polling_restart_catches_gap_and_deduplicates_schedulers(engine):
    cik = str(int(uuid4().hex[:7], 16)).zfill(10)
    planner.add_watchlist(engine, [cik])
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE watchlist SET last_success_at=now()-interval '3 days',next_poll_at=now()-interval '2 days' WHERE cik=:cik"
            ),
            {"cik": cik},
        )
    planner.tick(engine)
    planner.tick(engine)
    with engine.connect() as connection:
        rows = list(
            connection.execute(
                text("SELECT payload FROM jobs WHERE kind='discover' AND payload->>'cik'=:cik"),
                {"cik": cik},
            ).scalars()
        )
        assert len(rows) == 1
        assert date.fromisoformat(rows[0]["from"]) <= date.today()
        assert (date.today() - date.fromisoformat(rows[0]["from"])).days >= 9
        connection.execute(text("SELECT 1"))


def test_cancelled_backfill_stops_claimed_work(engine, archive, active):
    client, handler, worker, _ = fake_sec(engine, archive)
    try:
        operation = planner.create_backfill(
            engine, ["1234567"], date(2025, 1, 1), date(2025, 3, 31)
        )
        job = next_operation_job(engine, operation)
        lease = store.claim(engine, job, "cancelled-worker", 60)
        commit = handler(lease)
        planner.cancel_backfill(engine, operation)
        with pytest.raises(store.Cancelled):
            store.finish(engine, lease, commit)
        store.cancel_claim(engine, lease)
        assert store.inspect(engine, job)["state"] == "cancelled"
        assert list(archive.manifests())  # Source remains preserved.
    finally:
        client.close()


def test_replay_is_offline_deterministic_and_promotes_safely(engine, archive, active, monkeypatch):
    sources = []
    for filename, kind in (("submissions.json", "submissions"), ("facts.json", "facts")):
        sources.append(
            archive.preserve(
                (FIXTURES / filename).read_bytes(),
                kind=kind,
                url="https://data.sec.gov/test",
                cik="0001234567",
            )
        )
    for source in reversed(sources):
        process_source(engine, archive, source, active)
    with engine.connect() as connection:
        expected = digest(connection, active)

    def forbid_network(*args, **kwargs):
        raise AssertionError("Replay attempted HTTP")

    monkeypatch.setattr(httpx.Client, "send", forbid_network)
    name = "replay-" + uuid4().hex
    result = rebuild(engine, archive, name)
    assert result == expected
    with pytest.raises(ArchiveIntegrityError):
        promote(engine, name, "wrong", archive=archive)
    promote(engine, name, expected["sha256"], archive=archive)
    # New work follows the promoted generation rather than silently writing to 'live'.
    additional = archive.preserve(
        (FIXTURES / "original.htm").read_bytes(),
        kind="document",
        url="https://www.sec.gov/test",
        cik="0001234567",
        accession="0001234567-25-000001",
    )
    process_source(engine, archive, additional)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM filing_sources WHERE generation=:g AND role='document'"),
                {"g": name},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM filing_sources WHERE generation=:g AND role='document'"),
                {"g": active},
            )
            == 0
        )


def test_corruption_blocks_replay_and_retains_active(engine, archive, active):
    source = archive.preserve(
        b'{"cik":1234567,"facts":{}}',
        kind="facts",
        url="https://data.sec.gov/test",
        cik="0001234567",
    )
    archive.client.put_object(Bucket=archive.bucket, Key=source.blob_key, Body=b"corrupt")
    name = "replay-" + uuid4().hex
    with pytest.raises(ArchiveIntegrityError):
        rebuild(engine, archive, name)
    with pytest.raises(ArchiveIntegrityError):
        promote(engine, name, "anything", archive=archive)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT value FROM system_state WHERE key='active_generation'"))
            == active
        )
        assert (
            connection.scalar(text("SELECT state FROM replays WHERE generation=:g"), {"g": name})
            == "failed"
        )


def test_inventory_repairs_sql_registration_idempotently(engine, archive):
    source = archive.preserve(
        (FIXTURES / "facts.json").read_bytes(),
        kind="facts",
        url="https://data.sec.gov/test",
        cik="0001234567",
    )
    assert inventory(engine, archive) == 1
    assert inventory(engine, archive) == 1
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM source_events WHERE event_id=:id"),
                {"id": source.event_id},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM jobs WHERE payload->>'event_id'=:id"),
                {"id": source.event_id},
            )
            == 1
        )


def test_replay_resumes_after_interruption(engine, archive, active, monkeypatch):
    from secrecon.orchestration import replay

    for filename, kind in (("submissions.json", "submissions"), ("facts.json", "facts")):
        source = archive.preserve(
            (FIXTURES / filename).read_bytes(),
            kind=kind,
            url="https://data.sec.gov/test",
            cik="0001234567",
        )
        process_source(engine, archive, source, active)
    original = replay.process_source
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected replay interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(replay, "process_source", interrupted)
    name = "replay-" + uuid4().hex
    with pytest.raises(RuntimeError):
        rebuild(engine, archive, name)
    monkeypatch.setattr(replay, "process_source", original)
    result = rebuild(engine, archive, name, resume=True)
    with engine.connect() as connection:
        assert result == digest(connection, active)
