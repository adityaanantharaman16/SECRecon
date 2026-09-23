"""Reduce an ignored M6.2 performance report to a compact, committable summary.

Usage, from the repository root::

    python scripts/performance_summary.py .local/performance/<report>.json \
        docs/evidence/performance/<name>.json

Full reports stay under ignored ``.local/performance/``: they carry raw
per-sample series and complete command output. The summary is built from an
explicit allowlist of fields (never by copying and deleting), so new report
fields cannot leak into committed evidence by default. It keeps every verdict
and its checks, the frozen dataset/parameters, machine and software identity,
digests and row-level comparison, latency distributions, recovery timelines and
soak aggregates, plus the command vectors with return codes and durations (not
their output). The source report's SHA-256 ties the summary to its origin.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
FORBIDDEN_MARKERS = ("postgresql://", "postgresql+psycopg://", "password", "secret_key", "token=")


def pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: source[key] for key in keys if key in source}


def replay_summary(run: dict[str, Any]) -> dict[str, Any]:
    return pick(
        run,
        "mode",
        "label",
        "generation",
        "workers",
        "dataset_fingerprint",
        "jobs",
        "unique_sources",
        "observations",
        "duplicate_deliveries",
        "enqueue_seconds",
        "drain_seconds",
        "completed",
        "open_jobs_at_end",
        "jobs_per_second",
        "observations_per_second",
        "counts",
        "digest",
        "digest_seconds",
        "dispatcher",
        "worker_exit_codes",
        "max_sampled_worker_rss_bytes",
        "job_states",
        "attempt_outcomes",
        "progress",
    )


def load_summary(load: dict[str, Any]) -> dict[str, Any]:
    runs = {
        name: pick(
            load[name],
            "label",
            "requests",
            "offered_rps",
            "duration_seconds",
            "achieved_rps",
            "wall_seconds",
            "errors",
            "error_rate",
            "p50_ms",
            "p95_ms",
            "p99_ms",
            "max_ms",
            "mean_ms",
            "first_20_requests_max_ms",
            "per_minute_p95_ms",
            "max_items_per_page",
            "schedule_max_lag_ms",
            "api_rss_bytes_max",
            "by_route",
        )
        for name in ("cold", "warm")
        if name in load
    }
    return {
        **pick(load, "generation", "api_command", "api_ready_seconds", "query_plans"),
        **runs,
    }


def soak_summary(soak: dict[str, Any]) -> dict[str, Any]:
    samples = soak.get("samples", [])
    return {
        **pick(
            soak,
            "workers",
            "seconds",
            "offered_rate_jobs_per_second",
            "enqueued",
            "throughput_jobs_per_second",
            "drain_after_load_seconds",
            "job_states",
            "final",
            "worker_exit_codes",
            "cycle_digests",
            "reference_digest",
            "windows",
        ),
        "cycles_total": len(soak.get("cycles", [])),
        "cycles_complete": sum(
            1 for cycle in soak.get("cycles", []) if cycle["processing_runs"] == cycle["enqueued"]
        ),
        "sample_count": len(samples),
        "database_bytes_first_last": (
            [samples[0].get("database_bytes"), samples[-1].get("database_bytes")]
            if samples
            else None
        ),
    }


def summarize(report: dict[str, Any], source_sha256: str) -> dict[str, Any]:
    results = report.get("results", {})
    summary: dict[str, Any] = {
        "summary_schema_version": SCHEMA_VERSION,
        "source_report_sha256": source_sha256,
        **pick(
            report,
            "schema_version",
            "scenario",
            "guide_sized",
            "parameters",
            "targets",
            "environment",
            "requested_project",
            "project",
            "run_key",
            "started_at",
            "finished_at",
            "duration_seconds",
            "exit_status",
            "failure",
            "cleanup_error",
            "phase_seconds",
            "verdicts",
            "container_stats_by_phase",
            "container_stats_errors",
        ),
        "results": {},
        "commands": [
            pick(command, "command", "returncode", "duration_seconds")
            for command in report.get("commands", [])
        ],
    }
    out = summary["results"]
    if "seed" in results:
        out["seed"] = results["seed"]
    for key in ("replay_single", "replay_four"):
        if key in results:
            out[key] = replay_summary(results[key])
    if "rebuild" in results:
        out["rebuild"] = pick(
            results["rebuild"],
            "mode",
            "label",
            "generation",
            "seconds",
            "observations_per_second",
            "counts",
            "digest",
        )
    if "compare" in results:
        out["compare"] = results["compare"]
    if "load" in results:
        out["load"] = load_summary(results["load"])
    if "recovery" in results:
        out["recovery"] = [
            pick(
                trial,
                "trial",
                "lease_seconds",
                "heartbeat_seconds",
                "claimed_state_before_kill",
                "lease_remaining_at_kill_seconds",
                "reclaimed_after_kill_seconds",
                "state",
                "recovered",
                "recovery_seconds",
                "attempts",
                "event_states",
                "counts",
                "redis_pending_after",
                "correct",
            )
            for trial in results["recovery"]
        ]
    if "soak_rate_basis" in results:
        out["soak_rate_basis"] = results["soak_rate_basis"]
    if "soak" in results:
        out["soak"] = soak_summary(results["soak"])
    return summary


def assert_sanitized(text: str) -> None:
    lowered = text.lower()
    found = [marker for marker in FORBIDDEN_MARKERS if marker in lowered]
    if found:
        raise ValueError(f"summary contains credential-like markers {found}; refusing to write")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    source, target = Path(args[0]), Path(args[1])
    raw = source.read_bytes()
    summary = summarize(json.loads(raw), hashlib.sha256(raw).hexdigest())
    text = json.dumps(summary, indent=1, sort_keys=True) + "\n"
    assert_sanitized(text)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"performance summary: {target} ({len(text.encode())} bytes) from {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
