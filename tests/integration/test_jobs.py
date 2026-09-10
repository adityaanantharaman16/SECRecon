from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from redis import Redis
from sqlalchemy import text

from secrecon.config import Settings
from secrecon.db.projections import apply_projection
from secrecon.ingestion.adapters import parse
from secrecon.jobs import store
from secrecon.jobs.queue import Queue
from secrecon.jobs.worker import Worker

pytestmark = pytest.mark.integration


def enqueue(engine, kind="test", payload=None):
    with engine.begin() as connection:
        return store.enqueue(connection, kind, payload or {}, "test:" + uuid4().hex)


def make_queue():
    return Queue(Redis.from_url(Settings().redis_url), stream="test:" + uuid4().hex)


def expire(engine, job_id):
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"),
            {"id": job_id},
        )


def test_one_hundred_duplicate_deliveries_two_workers(engine, archive, generation):
    from pathlib import Path

    source = archive.preserve(
        (Path(__file__).parents[1] / "fixtures/synthetic/facts.json").read_bytes(),
        kind="facts",
        url="https://data.sec.gov/test",
        cik="0001234567",
    )
    parsed = parse(source, archive.load(source))
    job = enqueue(engine)

    def handler(lease):
        return lambda connection: apply_projection(connection, source, parsed, generation)

    queue = make_queue()
    workers = [Worker(engine, queue, Settings(), handler, "worker-" + str(i)) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda i: workers[i % 2].process(job), range(100)))
    assert all(results)
    state = store.inspect(engine, job)
    assert state["state"] == "succeeded"
    assert len(state["attempt_history"]) == 1
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM facts WHERE generation=:g"), {"g": generation}
            )
            == 2
        )


def test_expired_owner_cannot_commit_after_takeover(engine):
    job = enqueue(engine)
    stale = store.claim(engine, job, "old", 60)
    expire(engine, job)
    store.sweep(engine)
    current = store.claim(engine, job, "new", 60)
    with pytest.raises(store.LeaseLost):
        store.finish(engine, stale, lambda connection: None)
    store.finish(engine, current, lambda connection: None)
    assert [a["outcome"] for a in store.inspect(engine, job)["attempt_history"]] == [
        "lease_expired",
        "succeeded",
    ]


def test_heartbeat_never_resurrects_expired_lease(engine):
    job = enqueue(engine)
    lease = store.claim(engine, job, "old", 60)
    assert store.heartbeat(engine, lease, 60)
    expire(engine, job)
    assert not store.heartbeat(engine, lease, 60)


def test_transaction_failure_rolls_back_effect_and_retries(engine):
    job = enqueue(engine)
    lease = store.claim(engine, job, "old", 60)

    def fail_mid_commit(connection):
        connection.execute(text("INSERT INTO system_state VALUES (:id,'partial')"), {"id": job})
        raise RuntimeError("injected before commit")

    with pytest.raises(RuntimeError):
        store.finish(engine, lease, fail_mid_commit)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT value FROM system_state WHERE key=:id"), {"id": job})
            is None
        )
    assert store.fail(engine, lease, "timeout", retryable=True, retry_after=120) == "retry_wait"
    assert store.claim(engine, job, "too-soon", 60) is None


def test_exhaustion_and_redrive_keep_history(engine):
    job = enqueue(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE jobs SET max_attempts=2 WHERE id=:id"), {"id": job})
    first = store.claim(engine, job, "worker", 60)
    assert store.fail(engine, first, "temporary", retryable=True) == "retry_wait"
    with engine.begin() as connection:
        connection.execute(text("UPDATE jobs SET due_at=now() WHERE id=:id"), {"id": job})
    second = store.claim(engine, job, "worker", 60)
    assert store.fail(engine, second, "temporary", retryable=True) == "dead_letter"
    replacement = store.redrive(engine, job)
    assert store.inspect(engine, replacement)["parent_id"] == job
    assert len(store.inspect(engine, job)["attempt_history"]) == 2


def test_outbox_double_publish_and_queue_loss_recover_from_sql(engine):
    job = enqueue(engine)
    # This database deliberately survives repeated test runs; put the probe within
    # the dispatch batch even when older test jobs remain outstanding.
    with engine.begin() as connection:
        connection.execute(text("UPDATE jobs SET priority=1000000 WHERE id=:id"), {"id": job})
    queue = make_queue()
    queue.dispatch(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE outbox SET sent_at=NULL WHERE job_id=:id"), {"id": job})
    queue.dispatch(engine)
    # Duplicate notifications are harmless; wipe only this test stream and recreate it.
    queue.redis.delete(queue.stream)
    store.sweep(engine, notification_seconds=0)
    queue.dispatch(engine)
    observed = []
    for _ in range(20):
        messages = queue.read("recovery", block_ms=10)
        observed.extend(value for _, value in messages)
        if job in observed or not messages:
            break
    assert job in observed


def test_same_key_different_request_is_rejected(engine):
    key = "test:" + uuid4().hex
    with engine.begin() as connection:
        first = store.enqueue(connection, "test", {"a": 1}, key)
    with engine.begin() as connection:
        assert store.enqueue(connection, "test", {"a": 1}, key) == first
    with pytest.raises(store.IdempotencyConflict), engine.begin() as connection:
        store.enqueue(connection, "test", {"a": 2}, key)


def test_commit_before_ack_is_not_repeated(engine):
    job = enqueue(engine)
    queue = make_queue()

    def handler(lease):
        return lambda connection: connection.execute(
            text("INSERT INTO system_state VALUES (:id,'once')"), {"id": job}
        )

    worker = Worker(engine, queue, Settings(), handler, "worker")
    assert worker.process(job)
    assert worker.process(job)
    assert len(store.inspect(engine, job)["attempt_history"]) == 1
