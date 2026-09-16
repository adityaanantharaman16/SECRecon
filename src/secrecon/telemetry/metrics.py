"""Operational gauges come from authoritative SQL, never Redis delivery counts."""

from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Connection, text

from secrecon.telemetry import runtime


def snapshot(
    connection: Connection, redis_url: str | None = None, live: bool = False
) -> dict[str, Any]:
    states = {
        row[0]: row[1]
        for row in connection.execute(text("SELECT state,count(*) FROM jobs GROUP BY state"))
    }
    result: dict[str, Any] = {"jobs": states, "scheduler_enabled": int(live)}
    for name, sql in {
        "runnable": "SELECT count(*) FROM jobs WHERE state IN ('queued','retry_wait') AND due_at<=now()",
        "oldest_runnable_seconds": "SELECT COALESCE(max(EXTRACT(epoch FROM now()-greatest(created_at,due_at))),0) FROM jobs WHERE state IN ('queued','retry_wait') AND due_at<=now()",
        "outbox_age_seconds": "SELECT COALESCE(max(EXTRACT(epoch FROM now()-o.created_at)),0) FROM outbox o JOIN jobs j ON j.id=o.job_id WHERE o.sent_at IS NULL AND j.due_at<=now() AND j.state IN ('queued','retry_wait')",
        "retries_total": "SELECT count(*) FROM job_events WHERE state='retry_wait'",
        "quarantine_records": "SELECT count(*) FROM quarantine_records",
        "sources_preserved": "SELECT count(*) FROM source_events",
        "filings": "SELECT count(*) FROM filings WHERE generation=(SELECT value FROM system_state WHERE key='active_generation')",
        "last_source_at": "SELECT max(fetched_at) FROM source_events",
        "ingestion_lag_seconds": "SELECT max(EXTRACT(epoch FROM p.completed_at-s.fetched_at)) FROM processing_runs p JOIN source_events s USING(event_id) WHERE p.status='succeeded' AND p.completed_at>now()-interval '24 hours'",
        "poll_age_seconds": "SELECT max(EXTRACT(epoch FROM now()-COALESCE(w.last_success_at,(SELECT min(j.created_at) FROM jobs j WHERE j.kind='discover' AND j.payload->>'cik'=w.cik AND j.payload->>'backfill_id' IS NULL),w.next_poll_at))) FROM watchlist w WHERE enabled",
        "successes_24h": "SELECT count(*) FROM job_attempts WHERE outcome='succeeded' AND ended_at>now()-interval '24 hours'",
        "failures_24h": "SELECT count(*) FROM job_attempts WHERE outcome NOT IN ('running','succeeded','cancelled') AND ended_at>now()-interval '24 hours'",
    }.items():
        value = connection.scalar(text(sql))
        result[name] = float(value) if value is not None and name != "last_source_at" else value
    result["redis_pending"] = None
    if redis_url:
        client: Redis[bytes] = Redis.from_url(
            redis_url, socket_connect_timeout=0.3, socket_timeout=0.3
        )
        try:
            if client.exists("secrecon:jobs"):
                result["redis_pending"] = int(
                    client.xpending("secrecon:jobs", "workers")["pending"]  # type: ignore[no-untyped-call]
                )
            else:
                result["redis_pending"] = 0
        except RedisError:
            pass
        finally:
            client.close()
    return result


def prometheus(connection: Connection, redis_url: str, live: bool) -> str:
    state = snapshot(connection, redis_url, live)
    lines = []
    for name in (
        "runnable",
        "oldest_runnable_seconds",
        "outbox_age_seconds",
        "retries_total",
        "quarantine_records",
        "sources_preserved",
        "filings",
        "ingestion_lag_seconds",
        "poll_age_seconds",
        "scheduler_enabled",
        "redis_pending",
    ):
        if state[name] is not None:
            lines.append(f"secrecon_{name} {max(0, state[name])}")
    lines.append(f"secrecon_redis_available {int(state['redis_pending'] is not None)}")
    for name in (
        "queued",
        "running",
        "retry_wait",
        "succeeded",
        "dead_letter",
        "quarantined",
        "cancelled",
    ):
        lines.append(f'secrecon_jobs{{state="{name}"}} {state["jobs"].get(name, 0)}')
    for row in connection.execute(
        text("SELECT outcome,count(*) FROM job_attempts GROUP BY outcome")
    ):
        outcome = (
            row[0]
            if row[0]
            in {
                "running",
                "succeeded",
                "retry_wait",
                "dead_letter",
                "quarantined",
                "cancelled",
                "lease_expired",
            }
            else "other"
        )
        lines.append(f'secrecon_attempts_total{{outcome="{outcome}"}} {row[1]}')
    for bucket in (1, 5, 15, 60, 300):
        value = connection.scalar(
            text(
                "SELECT count(*) FROM job_attempts WHERE ended_at IS NOT NULL AND EXTRACT(epoch FROM ended_at-started_at)<=:b"
            ),
            {"b": bucket},
        )
        lines.append(f'secrecon_job_duration_seconds_bucket{{le="{bucket}"}} {value}')
    row = connection.execute(
        text(
            "SELECT count(*),COALESCE(sum(EXTRACT(epoch FROM ended_at-started_at)),0) FROM job_attempts WHERE ended_at IS NOT NULL"
        )
    ).one()
    lines.extend(
        [
            f'secrecon_job_duration_seconds_bucket{{le="+Inf"}} {row[0]}',
            f"secrecon_job_duration_seconds_count {row[0]}",
            f"secrecon_job_duration_seconds_sum {row[1]}",
        ]
    )
    for status, count in connection.execute(
        text(
            "SELECT COALESCE((s.manifest->>'status')::int/100,0),count(*) FROM fetch_attempts f LEFT JOIN source_events s USING(event_id) GROUP BY 1"
        )
    ):
        lines.append(f'secrecon_fetches_total{{status_class="{status}xx"}} {count}')
    for signal, delivery in runtime.current.deliveries.items():
        lines.extend(
            [
                f'secrecon_telemetry_dropped_total{{signal="{signal}"}} {delivery.dropped}',
                f'secrecon_telemetry_failed_total{{signal="{signal}"}} {delivery.failed}',
                f'secrecon_telemetry_buffer{{signal="{signal}"}} {delivery.items.qsize()}',
            ]
        )
    types = ["# TYPE secrecon_job_duration_seconds histogram"]
    names = {line.split("{")[0].split(" ")[0] for line in lines}
    for name in sorted(names):
        if not name.startswith("secrecon_job_duration_seconds"):
            types.append(f"# TYPE {name} {'counter' if name.endswith('_total') else 'gauge'}")
    return "\n".join(types + lines) + "\n"
