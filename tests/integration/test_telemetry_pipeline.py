import threading
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from redis import Redis
from sqlalchemy import text

from secrecon.config import Settings
from secrecon.ingestion.client import SecClient
from secrecon.ingestion.rate_limit import RateLimiter
from secrecon.jobs import store
from secrecon.jobs.handlers import CoreHandlers
from secrecon.jobs.queue import Queue
from secrecon.jobs.worker import Worker
from secrecon.telemetry import runtime

pytestmark = pytest.mark.integration


def test_trace_connects_fetch_archive_and_projection(engine, archive, monkeypatch):
    telemetry = runtime.Telemetry()
    monkeypatch.setattr(runtime, "current", telemetry)
    exporter = InMemorySpanExporter()
    telemetry.provider.add_span_processor(SimpleSpanProcessor(exporter))
    settings = Settings().model_copy(
        update={"sec_mode": "live", "sec_user_agent": "Test fixture@example.invalid"}
    )
    redis = Redis.from_url(settings.redis_url)
    fixture = (Path(__file__).parents[1] / "fixtures/synthetic/facts.json").read_bytes()
    client = SecClient(
        settings,
        engine,
        archive,
        RateLimiter(redis, 2),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=fixture)),
    )
    try:
        with engine.begin() as c:
            job = store.enqueue(
                c,
                "fetch",
                {"url": "https://data.sec.gov/test", "kind": "facts", "cik": "0001234567"},
                uuid4().hex,
            )
        worker = Worker(
            engine, Queue(redis), settings, CoreHandlers(engine, archive, client), "trace-test"
        )
        assert worker.process(job)
        record = store.inspect(engine, job)
        with engine.connect() as c:
            children = list(
                c.execute(
                    text("SELECT id FROM jobs WHERE kind='normalize' AND correlation_id=:t"),
                    {"t": record["correlation_id"]},
                ).scalars()
            )
        assert children
        for child in children:
            assert worker.process(child)
            assert store.inspect(engine, child)["state"] == "succeeded"
        spans = exporter.get_finished_spans()
        assert {"source.fetch", "archive.preserve", "projection.commit", "job.process"} <= {
            s.name for s in spans
        }
        assert len({s.context.trace_id for s in spans}) == 1
        projection = next(s for s in spans if s.name == "projection.commit")
        assert projection.attributes["source_event_id"]
        assert record["attempt_history"][0]["trace_id"] == format(
            projection.context.trace_id, "032x"
        )
        with engine.begin() as c:
            failed = store.enqueue(c, "unsupported", {}, uuid4().hex)
        assert worker.process(failed)
        failure = store.inspect(engine, failed)
        assert failure["state"] == "dead_letter"
        trace_id = failure["attempt_history"][0]["trace_id"]
        failure_spans = [
            s
            for s in exporter.get_finished_spans()
            if format(s.context.trace_id, "032x") == trace_id and s.name == "job.process"
        ]
        assert len(failure_spans) == 1 and failure_spans[0].status.status_code == StatusCode.ERROR
    finally:
        client.close()
        redis.close()
        telemetry.close()


def test_blocked_exporter_does_not_block_sql_job_commit(engine, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class Blocked:
        def export(self, items):
            entered.set()
            release.wait(10)
            return SpanExportResult.SUCCESS

    telemetry = runtime.Telemetry()
    delivery = runtime.Delivery(Blocked(), capacity=4)
    telemetry.provider.add_span_processor(runtime.SpanDelivery(delivery))
    monkeypatch.setattr(runtime, "current", telemetry)
    settings = Settings()
    redis = Redis.from_url(settings.redis_url)
    try:
        with engine.begin() as c:
            job = store.enqueue(c, "normalize", {}, uuid4().hex)
        assert entered.wait(2)
        for _ in range(20):
            with runtime.span("test.overflow"):
                pass
        worker = Worker(
            engine, Queue(redis), settings, lambda lease: lambda connection: None, "export-outage"
        )
        assert worker.process(job)
        assert store.inspect(engine, job)["state"] == "succeeded"
        assert not release.is_set() and delivery.dropped > 0 and delivery.items.qsize() <= 4
    finally:
        release.set()
        delivery.flush()
        telemetry.close()
        redis.close()
