"""Offline worker trace through fetch, preservation and projection; uses a stub SEC."""

import json
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import text

from secrecon.ingestion.client import SecClient
from secrecon.ingestion.rate_limit import RateLimiter
from secrecon.jobs import store
from secrecon.jobs.handlers import CoreHandlers
from secrecon.jobs.worker import Worker
from secrecon.runtime import services
from secrecon.telemetry import runtime as telemetry

with services() as deps:
    # This mode only applies to the explicitly supplied in-process HTTP stub.
    config = deps.settings.model_copy(
        update={
            "sec_mode": "live",
            "sec_user_agent": "SECRecon offline fixture recorder@example.invalid",
        }
    )
    fixture = (Path(__file__).parents[1] / "tests/fixtures/synthetic/facts.json").read_bytes()
    client = SecClient(
        config,
        deps.engine,
        deps.archive,
        RateLimiter(deps.redis, 2),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=fixture)),
    )
    try:
        with deps.engine.begin() as connection:
            job = store.enqueue(
                connection,
                "fetch",
                {
                    "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0001234567.json",
                    "kind": "facts",
                    "cik": "0001234567",
                },
                "observability-smoke:" + uuid4().hex,
            )
        worker = Worker(
            deps.engine,
            deps.queue,
            config,
            CoreHandlers(deps.engine, deps.archive, client),
            "observability-smoke",
        )
        assert worker.process(job)
        record = store.inspect(deps.engine, job)
        assert record["state"] == "succeeded"
        with deps.engine.connect() as connection:
            children = list(
                connection.execute(
                    text("SELECT id FROM jobs WHERE kind='normalize' AND correlation_id=:trace"),
                    {"trace": record["correlation_id"]},
                ).scalars()
            )
        assert children
        for child in children:
            assert worker.process(child)
            assert store.inspect(deps.engine, child)["state"] == "succeeded"
        for delivery in telemetry.current.deliveries.values():
            delivery.flush(2000)
        print(
            json.dumps(
                {
                    "fetch_job": job,
                    "normalization_jobs": children,
                    "trace_id": record["correlation_id"],
                    "status": "succeeded",
                }
            )
        )
    finally:
        client.close()
