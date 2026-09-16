import threading
import time

import pytest
from sqlalchemy import text
from test_jobs import enqueue, make_queue

from secrecon.config import Settings
from secrecon.db.projections import ProjectionVersionChanged
from secrecon.ingestion.adapters import SchemaError
from secrecon.ingestion.client import FetchError
from secrecon.jobs import store
from secrecon.jobs.worker import Worker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "error,state",
    [
        (SchemaError("bad numeric shape"), "quarantined"),
        (FetchError("upstream unavailable", retryable=True, retry_after=120), "retry_wait"),
        (ValueError("unsupported input"), "dead_letter"),
        (OSError("temporary dependency failure"), "retry_wait"),
        (ProjectionVersionChanged("active parser advanced"), "retry_wait"),
    ],
)
def test_worker_records_classified_failures(engine, error, state):
    job = enqueue(engine)

    def handler(lease):
        raise error

    worker = Worker(engine, make_queue(), Settings(), handler, "failure-worker")
    assert worker.process(job)
    history = store.inspect(engine, job)
    assert history["state"] == state
    assert history["attempt_history"][0]["outcome"] == state
    assert history["events"][-1]["state"] == state


def test_heartbeat_keeps_real_long_running_work_owned(engine):
    job = enqueue(engine)
    settings = Settings().model_copy(update={"lease_seconds": 3, "heartbeat_seconds": 1})

    def handler(lease):
        time.sleep(2.2)

        def commit(connection):
            remaining = connection.scalar(
                text(
                    "SELECT extract(epoch FROM lease_until-clock_timestamp()) FROM jobs WHERE id=:id"
                ),
                {"id": job},
            )
            assert remaining > 1.5

        return commit

    assert Worker(engine, make_queue(), settings, handler, "heartbeat-worker").process(job)
    assert store.inspect(engine, job)["state"] == "succeeded"


def test_worker_loop_acknowledges_only_after_durable_commit(engine):
    job = enqueue(engine)
    queue = make_queue()
    queue.initialize()
    queue.publish(job)
    stop = threading.Event()

    def handler(lease):
        def commit(connection):
            connection.execute(
                text("INSERT INTO system_state VALUES (:id,'committed')"), {"id": job}
            )
            stop.set()

        return commit

    Worker(engine, queue, Settings(), handler, "loop-worker").run(stop)
    assert store.inspect(engine, job)["state"] == "succeeded"
    assert queue.redis.xpending(queue.stream, queue.group)["pending"] == 0


def test_failure_after_commit_does_not_rewrite_success(engine):
    job = enqueue(engine)

    def handler(lease):
        return lambda connection: None

    def lose_ack():
        raise OSError("lost acknowledgement")

    worker = Worker(engine, make_queue(), Settings(), handler, "committed-worker")
    assert worker.process(job, after_commit=lose_ack)
    assert store.inspect(engine, job)["state"] == "succeeded"
    assert worker.process(job)
    assert len(store.inspect(engine, job)["attempt_history"]) == 1
