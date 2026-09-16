import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import text

from secrecon.api.app import create_app
from secrecon.config import Settings
from secrecon.db.projections import process_source
from secrecon.domain.types import canonical
from secrecon.ingestion.client import SecClient
from secrecon.ingestion.rate_limit import RateLimiter
from secrecon.jobs import store
from secrecon.jobs.handlers import CoreHandlers
from secrecon.jobs.queue import Queue
from secrecon.jobs.worker import Worker
from secrecon.orchestration import planner
from secrecon.orchestration.operations import submit
from secrecon.orchestration.replay import rebuild
from secrecon.telemetry.metrics import snapshot

pytestmark = pytest.mark.integration
TOKEN = "test-only-operator-token-" + "x" * 32
HEADERS = {"Authorization": "Bearer " + TOKEN}
ORIGINAL, AMENDMENT = "0001234567-25-000001", "0001234567-25-000002"


@pytest.fixture
def client():
    with TestClient(
        create_app(Settings().model_copy(update={"admin_token": SecretStr(TOKEN)}))
    ) as instance:
        yield instance


def seed(engine, archive, generation):
    for name, kind in (("submissions.json", "submissions"), ("facts.json", "facts")):
        source = archive.preserve(
            (Path(__file__).parents[1] / "fixtures" / "synthetic" / name).read_bytes(),
            kind=kind,
            cik="0001234567",
            url="https://data.sec.gov/SYNTHETIC-M5",
        )
        process_source(engine, archive, source, generation)
    return source


def process(engine, archive, job_id):
    settings = Settings()
    redis = Redis.from_url(settings.redis_url)
    sec = SecClient(settings, engine, archive, RateLimiter(redis, 2))
    try:
        worker = Worker(
            engine, Queue(redis), settings, CoreHandlers(engine, archive, sec), "m5-test"
        )
        assert worker.process(job_id)
        return store.inspect(engine, job_id)
    finally:
        sec.close()
        redis.close()


def test_keyset_filters_pin_generation_and_reject_mismatched_cursors(engine, client, generation):
    with engine.begin() as c:
        for index in range(6):
            cik = f"{index + 1:010d}"
            c.execute(
                text("INSERT INTO companies VALUES (:g,:c,CAST(:d AS jsonb),'seed')"),
                {
                    "g": generation,
                    "c": cik,
                    "d": canonical(
                        {
                            "cik": cik,
                            "name": "Search Co " + str(index),
                            "tickers": ["SC" + str(index)],
                        }
                    ),
                },
            )
    params = {"generation": generation, "limit": 2, "q": "Search Co"}
    first = client.get("/v1/companies", params=params).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = client.get("/v1/companies", params={**params, "cursor": first["next_cursor"]}).json()
    third = client.get("/v1/companies", params={**params, "cursor": second["next_cursor"]}).json()
    ids = [row["id"] for batch in [first, second, third] for row in batch["items"]]
    assert len(ids) == len(set(ids)) == 6 and not third["next_cursor"]
    assert (
        client.get(
            "/v1/companies", params={**params, "q": "different", "cursor": first["next_cursor"]}
        ).status_code
        == 422
    )
    assert client.get("/v1/facts", params={"cursor": first["next_cursor"]}).status_code == 422
    assert client.get("/v1/companies", params={"cursor": "%%%"}).status_code == 422
    assert client.get("/v1/facts?limit=201").status_code == 422
    assert (
        client.get("/v1/companies", params={"generation": generation, "q": "SC3"}).json()["items"][
            0
        ]["id"]
        == "0000000004"
    )
    # A cursor pins its original generation even if the active pointer changes.
    continued = client.get(
        "/v1/companies", params={"cursor": first["next_cursor"], "q": "Search Co", "limit": 2}
    ).json()
    assert continued["generation"] == generation and continued["items"] == second["items"]


def test_authenticated_idempotent_comparison_completes_and_retains_audit(
    engine, archive, generation, client
):
    seed(engine, archive, generation)
    # Aggregate captures have no manifest accession, but remain filing evidence.
    sources = client.get(
        "/v1/sources", params={"generation": generation, "accession": ORIGINAL}
    ).json()["items"]
    assert {"submissions", "facts"} <= {row["data"]["kind"] for row in sources}
    body = {"original": ORIGINAL, "amendment": AMENDMENT, "generation": generation}
    key = uuid4().hex
    headers = {**HEADERS, "Idempotency-Key": key}
    assert client.post("/v1/admin/reconciliations", json=body).status_code == 401
    assert client.post("/v1/admin/reconciliations", json=body, headers=HEADERS).status_code == 422
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda _: client.post("/v1/admin/reconciliations", json=body, headers=headers),
                range(4),
            )
        )
    assert {r.status_code for r in responses} == {202}
    assert len({r.json()["operation_id"] for r in responses}) == 1
    accepted = responses[0].json()
    assert (
        client.post(
            "/v1/admin/reconciliations", json={**body, "original": AMENDMENT}, headers=headers
        ).status_code
        == 409
    )
    job = process(engine, archive, accepted["job_id"])
    assert job["state"] == "succeeded"
    outcome = client.get(accepted["status_url"], headers=HEADERS).json()
    comparison = client.get("/v1/reconciliations/" + outcome["result"]["comparison_id"]).json()
    assert comparison["changes"][0]["delta"] == "1"
    assert job["attempt_history"][0]["trace_id"] == job["correlation_id"]
    assert job["trace_context"]["traceparent"]
    assert client.get("/v1/admin/jobs/" + job["id"]).status_code == 401
    assert (
        client.get("/v1/admin/jobs/" + job["id"], headers=HEADERS).json()["events"][-1]["state"]
        == "succeeded"
    )


def test_session_csrf_origin_logout_and_no_secret_echo(client):
    assert client.get("/ui/jobs", follow_redirects=False).status_code == 303
    assert (
        client.post(
            "/ui/login", data={"token": TOKEN}, headers={"Origin": "https://attacker.invalid"}
        ).status_code
        == 403
    )
    signed = client.post("/ui/login", data={"token": TOKEN}, follow_redirects=False)
    assert signed.status_code == 303
    assert (
        "HttpOnly" in signed.headers["set-cookie"]
        and "SameSite=strict" in signed.headers["set-cookie"]
    )
    assert client.get("/v1/admin/jobs").status_code == 200
    headers = {"Idempotency-Key": uuid4().hex}
    assert client.post("/v1/admin/replays", json={}, headers=headers).status_code == 403
    csrf = client.cookies.get("secrecon_csrf")
    valid = {**headers, "X-CSRF-Token": csrf}
    assert (
        client.post(
            "/v1/admin/replays", json={}, headers={**valid, "Origin": "https://attacker.invalid"}
        ).status_code
        == 403
    )
    assert client.post("/v1/admin/replays", json={}, headers=valid).status_code == 202
    # Extra fields are rejected without returning submitted secrets in errors.
    invalid = client.post("/v1/admin/replays", json={"secret": TOKEN}, headers=valid)
    assert invalid.status_code == 422 and TOKEN not in invalid.text
    assert client.post("/ui/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/v1/admin/jobs").status_code == 401


def test_backfill_redrive_cancel_and_background_replay(engine, archive, generation, client):
    seed(engine, archive, generation)
    planner.add_watchlist(engine, ["0001234567"])

    def post(path, body):
        response = client.post(
            "/v1/admin/" + path, json=body, headers={**HEADERS, "Idempotency-Key": uuid4().hex}
        )
        assert response.status_code == 202, response.text
        job = process(engine, archive, response.json()["job_id"])
        assert job["state"] == "succeeded", job
        return client.get(response.json()["status_url"], headers=HEADERS).json()["result"]

    backfill = post(
        "backfills",
        {"ciks": ["1234567"], "start": "2025-01-01", "end": "2025-04-01", "max_jobs": 10},
    )["backfill_id"]
    post("backfills/" + backfill + "/cancel", {})
    assert (
        client.get("/v1/admin/backfills/" + backfill, headers=HEADERS).json()["state"]
        == "cancelled"
    )
    with engine.begin() as c:
        failed = store.enqueue(c, "normalize", {"event_id": "missing"}, uuid4().hex)
    lease = store.claim(engine, failed, "test", 60)
    store.fail(engine, lease, "permanent source error", retryable=False)
    new = post("jobs/" + failed + "/redrive", {})["job_id"]
    assert store.inspect(engine, new)["parent_id"] == failed
    assert store.inspect(engine, failed)["state"] == "dead_letter"
    rebuilt = post("replays", {})
    details = client.get("/v1/admin/replays/" + rebuilt["generation"], headers=HEADERS).json()
    assert details["state"] == "ready" and details["total_sources"] == 2
    with engine.connect() as c:
        assert (
            c.scalar(text("SELECT value FROM system_state WHERE key='active_generation'"))
            != rebuilt["generation"]
        )


def test_replay_rejects_stale_worker_without_projection_write(engine, archive, generation):
    seed(engine, archive, generation)
    with engine.begin() as c:
        job = store.enqueue(c, "operation", {}, uuid4().hex)
    lease = store.claim(engine, job, "expired", 60)
    with engine.begin() as c:
        c.execute(
            text("UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=:id"), {"id": job}
        )
    target = "fenced-" + uuid4().hex
    with pytest.raises(store.LeaseLost):
        rebuild(engine, archive, target, guard=lambda c: store.owned(c, lease))
    with engine.connect() as c:
        assert c.scalar(text("SELECT name FROM generations WHERE name=:g"), {"g": target}) is None


def test_metrics_use_sql_and_exclude_future_retry(engine):
    with engine.begin() as c:
        baseline = snapshot(c)
        due = store.enqueue(c, "normalize", {}, uuid4().hex)
        future = store.enqueue(c, "normalize", {}, uuid4().hex)
        c.execute(
            text(
                "UPDATE jobs SET due_at=now()-interval '10 minutes',created_at=now()-interval '10 minutes' WHERE id=:id"
            ),
            {"id": due},
        )
        c.execute(
            text("UPDATE jobs SET state='retry_wait',due_at=now()+interval '1 day' WHERE id=:id"),
            {"id": future},
        )
        measured = snapshot(c)
        assert measured["runnable"] == baseline["runnable"] + 1
        assert measured["oldest_runnable_seconds"] >= 599
        assert measured["jobs"].get("retry_wait", 0) == baseline["jobs"].get("retry_wait", 0) + 1


def test_ui_real_data_autoescapes_and_routes_render(engine, archive, generation, client):
    seed(engine, archive, generation)
    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE companies SET data=jsonb_set(data,'{name}',CAST(:name AS jsonb)) WHERE generation=:g"
            ),
            {"name": json.dumps('<script>alert("x")</script>'), "g": generation},
        )
    client.post("/ui/login", data={"token": TOKEN})
    for page in (
        "overview",
        "companies",
        "filings",
        "amendments",
        "jobs",
        "quarantine",
        "sources",
        "recovery",
        "replays",
        "backfills",
    ):
        response = client.get("/ui/" + page, params={"generation": generation})
        assert response.status_code == 200, (page, response.text)
        assert '<script>alert("x")</script>' not in response.text
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    facts = client.get(
        "/v1/facts",
        params={
            "generation": generation,
            "accession": ORIGINAL,
            "concept": "Assets",
            "period_end": "2024-12-31",
        },
    ).json()
    assert len(facts["items"]) == 1
    fact_id = facts["items"][0]["id"]
    assert client.get("/ui/facts/" + fact_id, params={"generation": generation}).status_code == 200
    assert (
        client.get("/ui/filings/" + ORIGINAL, params={"generation": generation}).status_code == 200
    )
    assert client.get("/metrics").status_code == 200
    assert client.get("/v1/admin/jobs/unknown", headers=HEADERS).status_code == 404


def test_invalid_admin_scope_and_unknown_generation(engine, client):
    headers = {**HEADERS, "Idempotency-Key": uuid4().hex}
    assert (
        client.post(
            "/v1/admin/backfills",
            json={"ciks": ["9999999999"], "start": "2025-01-01", "end": "2025-02-01"},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/admin/backfills",
            json={"ciks": ["123"], "start": "2025-02-01", "end": "2025-01-01"},
            headers=headers,
        ).status_code
        == 422
    )
    assert client.get("/v1/facts?generation=missing").status_code == 422
    with engine.begin() as c:
        with pytest.raises(ValueError):
            submit(c, "not-supported", {}, uuid4().hex, "test")


def test_docs_are_offline_and_have_nonce_and_bearer_contract(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "cdn.jsdelivr.net" not in response.text
    assert 'script nonce="' in response.text
    assert "'nonce-" in response.headers["content-security-policy"]
    assert client.get("/assets/swagger/swagger-ui-bundle.js").status_code == 200
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"


def test_comparison_and_provenance_pagination(engine, archive, generation, client):
    seed(engine, archive, generation)
    from secrecon.db.projections import process_source
    from secrecon.db.reconciliation import reconcile

    # Identical values in separate captures retain separate provenance entries.
    source = seed(engine, archive, generation)
    process_source(engine, archive, source, generation)
    fact = client.get("/v1/facts", params={"generation": generation, "accession": ORIGINAL}).json()[
        "items"
    ][0]
    url = "/v1/facts/" + fact["id"] + "/provenance"
    first = client.get(url, params={"generation": generation, "limit": 1}).json()
    second = client.get(
        url, params={"generation": generation, "limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert (
        first["sources"][0]["manifest"]["event_id"] != second["sources"][0]["manifest"]["event_id"]
    )
    assert second["next_cursor"] is None
    run = reconcile(engine, ORIGINAL, AMENDMENT, generation)
    page = client.get("/v1/reconciliations/" + run, params={"limit": 1}).json()
    assert len(page["changes"]) == 1 and page["next_cursor"] is None
