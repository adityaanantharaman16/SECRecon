"""Host-side M6.2 performance baseline for isolated Compose projects only.

Run from the repository root with a working Docker daemon::

    python scripts/performance_baseline.py --project secrecon-perf-m6-baseline

The base project must match ``secrecon-perf-<short-unique-name>``; the
development, release and ordinary test projects (and every other name) are
refused before any Docker command runs. Each run derives a fresh child project,
proves it has no labeled containers/volumes/networks, always pins
``compose.yaml`` plus ``compose.test.yaml``, and afterwards removes only that
child project's resources. The harness itself is standard-library only; all
measurement code runs inside the isolated ``test`` container via
``scripts/performance_probe.py``.

Exit status: 0 when every correctness check passes and every proposed target is
met; 3 when correctness passes but at least one proposed target is missed (the
report explains which); 1 on any correctness failure or harness error; 2 on a
refused project or invalid arguments.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    # Executed as ``python scripts/performance_baseline.py``: make ``scripts`` importable.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.service_drills import (  # noqa: E402
    IsolatedCompose,
    UnsafeProject,
    validate_isolated_project,
)

PROJECT_PREFIX = "secrecon-perf-"
PROJECT_PATTERN = re.compile(r"^secrecon-perf-[a-z0-9][a-z0-9-]{0,39}$")
DEFAULT_REPORT_DIR = Path(".local/performance")
JSON_MARKER = "PERF_JSON:"
PROBE = "scripts/performance_probe.py"
# Everything the ``secrecon:test`` image and Compose stack are built from.
IMAGE_INPUTS = (
    "Dockerfile",
    "compose.yaml",
    "compose.test.yaml",
    "pyproject.toml",
    "uv.lock",
    "alembic.ini",
    "src",
    "migrations",
    "scripts",
    "tests",
)

# The guide's frozen dataset and load shape (docs/PROJECT_GUIDE.md, "Initial
# performance targets"). tests/unit/test_performance_baseline.py pins these to
# the probe's DatasetSpec defaults so the two cannot drift apart.
GUIDE_DATASET = {
    "companies": 100,
    "filings_per_company": 10,
    "facts_per_filing": 100,
    "duplicate_ratio": 0.10,
}
GUIDE_WORKERS = 4
GUIDE_READ_RPS = 20.0
GUIDE_READ_SECONDS = 600.0
GUIDE_SOAK_SECONDS = 1800.0
TARGETS = {
    "replay_seconds": 600.0,
    "read_p95_ms": 300.0,
    "read_error_rate": 0.01,
    "read_max_page": 100,
    "recovery_seconds": 120.0,
    "lease_seconds": 60,
}
# Pre-declared leak rule for the soak (see docs/evidence/M6.md): after a
# warm-up, the late-window maximum may exceed the early-window maximum by at
# most REL_TOLERANCE plus the metric's absolute tolerance.
SOAK_WARMUP_SECONDS = 120.0
REL_TOLERANCE = 0.10
MIB = 1024 * 1024
DISPATCH_CEILING_JOBS_PER_SECOND = 50.0  # Queue.dispatch batch 100 per 2 s tick


def validate_project_name(project: str) -> str:
    """Allow only an explicit performance namespace; never target normal projects."""
    return validate_isolated_project(project, PROJECT_PATTERN, PROJECT_PREFIX)


def parse_payload(output: str, phase: str) -> dict[str, Any]:
    payloads = [
        line.removeprefix(JSON_MARKER)
        for line in output.splitlines()
        if line.startswith(JSON_MARKER)
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"expected one {JSON_MARKER} payload from {phase}, got {len(payloads)}")
    value = json.loads(payloads[0])
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid {phase} probe payload")
    return value


SIZE_UNITS = {
    "B": 1,
    "kB": 1000,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
}


def parse_size(value: str) -> int:
    match = re.fullmatch(r"\s*([0-9.]+)\s*([A-Za-z]+)\s*", value)
    if not match or match.group(2) not in SIZE_UNITS:
        raise ValueError(f"unrecognized size {value!r}")
    return int(float(match.group(1)) * SIZE_UNITS[match.group(2)])


def parse_stats_line(line: str) -> dict[str, Any]:
    """One ``docker stats --format '{{json .}}'`` record reduced to numbers."""
    raw = json.loads(line)
    used = raw.get("MemUsage", "0B / 0B").split("/")[0]
    return {
        "name": raw.get("Name", ""),
        "cpu_percent": float(raw.get("CPUPerc", "0%").rstrip("%") or 0),
        "memory_bytes": parse_size(used),
        "pids": int(raw.get("PIDs", "0") or 0),
    }


def service_of(container: str, project: str) -> str:
    """Compose container names are ``<project>-<service>-<n>`` or ``<project>-<service>-run-<id>``."""
    name = container.removeprefix(project + "-")
    name = re.sub(r"-run-[0-9a-f]+$", "", name)
    return re.sub(r"-\d+$", "", name)


# Stateful services whose cgroup v2 ``memory.stat`` is sampled so container
# memory can be split into anonymous (process) memory, shared memory (e.g.
# PostgreSQL shared_buffers) and reclaimable file-backed page cache.
CGROUP_SERVICES = ("postgres", "redis", "object-store")
CGROUP_FIELDS = ("anon", "file", "active_file", "inactive_file", "shmem", "file_mapped", "kernel")


def parse_memory_stat(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in CGROUP_FIELDS and parts[1].isdigit():
            values[parts[0]] = int(parts[1])
    return values


class StatsSampler:
    """Background ``docker stats`` sampling of the child project's containers.

    Uses plain subprocess calls (not the recorded command log) so hundreds of
    samples do not swamp the report's command history.
    """

    def __init__(self, project: str, interval: float) -> None:
        self.project, self.interval = project, interval
        self.phase = "setup"
        self.samples: list[dict[str, Any]] = []
        self.errors = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="docker-stats")
        self.started = time.monotonic()

    def start(self) -> None:
        self.started = time.monotonic()
        self.thread.start()

    def sample(self) -> None:
        ids = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={self.project}"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        ).stdout.split()
        if not ids:
            return
        output = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids],
            check=True,
            text=True,
            capture_output=True,
            timeout=60,
        ).stdout
        at = round(time.monotonic() - self.started, 1)
        for line in output.splitlines():
            if line.strip():
                record = parse_stats_line(line)
                record.update(
                    {
                        "t": at,
                        "phase": self.phase,
                        "service": service_of(record["name"], self.project),
                    }
                )
                if record["service"] in CGROUP_SERVICES:
                    record["cgroup"] = self.memory_stat(record["name"])
                self.samples.append(record)

    def memory_stat(self, container: str) -> dict[str, int] | None:
        try:
            output = subprocess.run(
                ["docker", "exec", container, "cat", "/sys/fs/cgroup/memory.stat"],
                check=True,
                text=True,
                capture_output=True,
                timeout=30,
            ).stdout
        except (subprocess.SubprocessError, OSError):
            self.errors += 1
            return None
        return parse_memory_stat(output) or None

    def loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.sample()
            except (subprocess.SubprocessError, OSError, ValueError):
                self.errors += 1
            self.stop_event.wait(self.interval)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=90)

    def by_phase(self) -> dict[str, dict[str, dict[str, float]]]:
        """Peak memory and mean/peak CPU per phase and service."""
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for sample in self.samples:
            grouped.setdefault(sample["phase"], {}).setdefault(sample["service"], []).append(sample)
        return {
            phase: {
                service: {
                    "samples": len(rows),
                    "cpu_mean_percent": round(statistics.fmean(r["cpu_percent"] for r in rows), 1),
                    "cpu_max_percent": round(max(r["cpu_percent"] for r in rows), 1),
                    "memory_max_bytes": max(r["memory_bytes"] for r in rows),
                }
                for service, rows in sorted(services.items())
            }
            for phase, services in grouped.items()
        }


# ------------------------------------------------------------------- verdicts


def verdict(
    measurement: str,
    target: str,
    result: str,
    *,
    correct: bool,
    met: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "measurement": measurement,
        "target": target,
        "result": result,
        "correct": correct,
        "target_met": met,
        "status": "fail" if not correct else "pass" if met else "miss",
        "details": details or {},
    }


def replay_verdict(
    single: dict[str, Any],
    four: dict[str, Any],
    compare: dict[str, Any] | None,
    rebuild: dict[str, Any] | None = None,
) -> dict[str, Any]:
    digests = {"single": single["digest"]["sha256"], "four": four["digest"]["sha256"]}
    if rebuild is not None:
        digests["rebuild"] = rebuild["digest"]["sha256"]
    expected_facts = single["observations"]
    runs = {"single": single, "four": four}
    correct = (
        single["completed"]
        and four["completed"]
        and len(set(digests.values())) == 1
        and (compare is None or compare["match"])
        and all(run["counts"]["facts"] == expected_facts for run in runs.values())
        and all(run["counts"]["processing_runs"] == run["unique_sources"] for run in runs.values())
        and all(run["job_states"] == {"succeeded": run["jobs"]} for run in runs.values())
        and single["dataset_fingerprint"] == four["dataset_fingerprint"]
    )
    seconds = float(four["drain_seconds"])
    return verdict(
        "throughput_replay",
        f"{four['workers']} workers replay {expected_facts:,} observations "
        f"(+{four['duplicate_deliveries']} duplicate deliveries) in < "
        f"{TARGETS['replay_seconds']:.0f} s; digest equals single-worker baseline",
        f"{four['workers']} workers {seconds:.1f} s ({four['jobs_per_second']} jobs/s); "
        f"1 worker {float(single['drain_seconds']):.1f} s; digest match "
        f"{len(set(digests.values())) == 1}",
        correct=bool(correct),
        met=bool(four["completed"]) and seconds < TARGETS["replay_seconds"],
        details={"digests": digests, "compare_match": compare["match"] if compare else None},
    )


def read_verdict(load: dict[str, Any]) -> dict[str, Any]:
    runs = {name: load[name] for name in ("cold", "warm")}
    correct = all(run["max_items_per_page"] <= TARGETS["read_max_page"] for run in runs.values())
    met = all(
        run["p95_ms"] < TARGETS["read_p95_ms"] and run["error_rate"] < TARGETS["read_error_rate"]
        for run in runs.values()
    )
    return verdict(
        "read_latency",
        f"{runs['cold']['offered_rps']:.0f} req/s for {runs['cold']['duration_seconds']:.0f} s "
        f"each (cold, then warmed): p95 < {TARGETS['read_p95_ms']:.0f} ms, errors < 1%, "
        f"max page {TARGETS['read_max_page']}",
        "; ".join(
            f"{name} p95 {run['p95_ms']} ms p99 {run['p99_ms']} ms errors {run['error_rate']:.3%}"
            f" max page {run['max_items_per_page']}"
            for name, run in runs.items()
        ),
        correct=correct,
        met=met,
        details={
            name: {
                key: run[key]
                for key in ("requests", "achieved_rps", "p50_ms", "p95_ms", "p99_ms", "max_ms")
            }
            for name, run in runs.items()
        },
    )


def recovery_verdict(trials: list[dict[str, Any]]) -> dict[str, Any]:
    correct = bool(trials) and all(trial["correct"] for trial in trials)
    times = [float(trial["recovery_seconds"]) for trial in trials]
    lease_ok = all(trial["lease_seconds"] == TARGETS["lease_seconds"] for trial in trials)
    met = correct and lease_ok and all(value <= TARGETS["recovery_seconds"] for value in times)
    return verdict(
        "crash_recovery",
        f"SIGKILLed worker's job recovered <= {TARGETS['recovery_seconds']:.0f} s under a "
        f"{TARGETS['lease_seconds']} s lease; SQL correctness mandatory",
        f"{len(trials)} trials: " + ", ".join(f"{value:.1f} s" for value in times),
        correct=correct,
        met=met,
        details={"lease_seconds": [trial["lease_seconds"] for trial in trials]},
    )


def slope_per_hour(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    mean_t = statistics.fmean(t for t, _ in points)
    mean_v = statistics.fmean(v for _, v in points)
    denominator = sum((t - mean_t) ** 2 for t, _ in points)
    if denominator == 0:
        return 0.0
    return sum((t - mean_t) * (v - mean_v) for t, v in points) / denominator * 3600


def growth(
    series: list[tuple[float, float]], start: float, end: float, absolute_tolerance: float
) -> dict[str, Any]:
    window = [(t, v) for t, v in series if start <= t < end]
    if len(window) < 3:
        return {
            "rule": "early/late window growth",
            "bounded": False,
            "reason": "too few samples",
            "samples": len(window),
        }
    third = max(1, len(window) // 3)
    early = [v for _, v in window[:third]]
    late = [v for _, v in window[-third:]]
    limit = max(early) * (1 + REL_TOLERANCE) + absolute_tolerance
    return {
        "rule": "early/late window growth",
        "bounded": max(late) <= limit,
        "early_max": max(early),
        "late_max": max(late),
        "early_median": statistics.median(early),
        "late_median": statistics.median(late),
        "limit": round(limit, 3),
        "slope_per_hour": round(slope_per_hour(window), 3),
        "samples": len(window),
    }


def soak_series(soak: dict[str, Any]) -> dict[str, tuple[list[tuple[float, float]], float]]:
    """Every sampled resource with its pre-declared absolute tolerance."""
    samples = soak["samples"]
    rate = float(soak["offered_rate_jobs_per_second"])
    queue_tolerance = max(20.0, rate * 4)  # two dispatcher ticks of arrivals
    series: dict[str, tuple[list[tuple[float, float]], float]] = {
        "open_jobs": ([(s["t"], s["open_jobs"]) for s in samples], queue_tolerance),
        "outbox_unsent": ([(s["t"], s["outbox_unsent"]) for s in samples], queue_tolerance),
        "redis_stream_length": (
            [(s["t"], s["redis_stream_length"]) for s in samples],
            queue_tolerance,
        ),
        "redis_pending": ([(s["t"], s["redis_pending"]) for s in samples], queue_tolerance),
        "redis_used_memory_bytes": (
            [(s["t"], s["redis_used_memory_bytes"]) for s in samples],
            8 * MIB,
        ),
        "pg_connections": ([(s["t"], s["pg_connections"]) for s in samples], 2),
        "dispatcher_rss_bytes": ([(s["t"], s["dispatcher_rss_bytes"]) for s in samples], 16 * MIB),
    }
    for owner, rows in sorted(soak["worker_samples"].items()):
        periodic = [row for row in rows if not row.get("final")]
        series[f"{owner}.rss_bytes"] = ([(r["t"], r["rss_bytes"]) for r in periodic], 16 * MIB)
        series[f"{owner}.threads"] = ([(r["t"], r["threads"]) for r in periodic], 2)
        series[f"{owner}.pool_checked_out"] = (
            [(r["t"], r["pool_checked_out"]) for r in periodic],
            2,
        )
    return series


def telemetry_checks(soak: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Telemetry export queues are fixed-capacity by construction.

    Against the soak's unresponsive collector they are *expected* to fill to
    capacity and then drop, so an early/late growth rule would misread the
    fill-up as a leak. Their bound is the hard capacity: every sample (periodic
    and final) must stay at or below it; drops are reported, not gated.
    """
    checks: dict[str, dict[str, Any]] = {}
    for owner, rows in sorted(soak["worker_samples"].items()):
        for signal in sorted({name for r in rows for name in r.get("telemetry", {})}):
            values = [r["telemetry"][signal] for r in rows if signal in r.get("telemetry", {})]
            checks[f"{owner}.telemetry_{signal}_queued"] = {
                "rule": "fixed capacity",
                "bounded": all(v["queued"] <= v["capacity"] for v in values),
                "max": max(v["queued"] for v in values),
                "capacity": max(v["capacity"] for v in values),
                "dropped_at_end": values[-1]["dropped"],
                "failed_exports_at_end": values[-1].get("failed"),
                "samples": len(values),
            }
    return checks


def soak_verdict(
    soak: dict[str, Any],
    reference_digest: str,
    container_samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    start = SOAK_WARMUP_SECONDS
    end = float(soak["seconds"]) + 1
    series = soak_series(soak)
    for service, tolerance in (
        ("postgres", 32 * MIB),
        ("redis", 8 * MIB),
        ("object-store", 32 * MIB),
    ):
        rows = [
            s for s in container_samples or [] if s["phase"] == "soak" and s["service"] == service
        ]
        if rows:
            origin = rows[0]["t"]
            series[f"container.{service}.memory_bytes"] = (
                [(r["t"] - origin, r["memory_bytes"]) for r in rows],
                tolerance,
            )
    checks = {name: growth(points, start, end, tol) for name, (points, tol) in series.items()}
    checks.update(telemetry_checks(soak))
    diagnostics = memory_diagnostics(soak, container_samples or [], start, end)
    # Telemetry export queues must also never exceed their fixed capacity.
    capacity_ok = all(
        check["bounded"] for check in checks.values() if check.get("rule") == "fixed capacity"
    )
    dropped = {
        owner: {signal: data["dropped"] for signal, data in rows[-1].get("telemetry", {}).items()}
        for owner, rows in soak["worker_samples"].items()
        if rows
    }
    finals_present = all(rows and rows[-1].get("final") for rows in soak["worker_samples"].values())
    digests = set(soak["cycle_digests"].values())
    enqueued = int(soak["enqueued"])
    correct = (
        soak["job_states"] == {"succeeded": enqueued}
        and soak["final"]["open_jobs"] == 0
        and all(code == 0 for code in soak["worker_exit_codes"])
        and bool(digests)
        and digests == {reference_digest}
        and all(cycle["processing_runs"] == cycle["enqueued"] for cycle in soak["cycles"])
    )
    bounded = all(check["bounded"] for check in checks.values()) and capacity_ok and finals_present
    peak_worker = max(
        (row.get("peak_rss_bytes", 0) for rows in soak["worker_samples"].values() for row in rows),
        default=0,
    )
    return verdict(
        "soak_leak_check",
        f"{float(soak['seconds']) / 60:.0f}-minute fixture soak: no unbounded queue, connection, "
        "memory or telemetry-buffer growth; capture peak memory and queue drain rate",
        f"{'bounded' if bounded else 'GROWTH DETECTED'}; {enqueued} jobs at "
        f"{soak['offered_rate_jobs_per_second']} jobs/s offered, drained "
        f"{soak['throughput_jobs_per_second']} jobs/s, {soak['drain_after_load_seconds']} s "
        f"post-load drain; peak worker RSS {peak_worker / MIB:.1f} MiB",
        correct=correct,
        met=bounded,
        details={
            "checks": checks,
            "telemetry_capacity_respected": capacity_ok,
            "telemetry_dropped": dropped,
            "cycle_digests_match_reference": digests == {reference_digest},
            "peak_worker_rss_bytes": peak_worker,
            "diagnostics_not_gated": diagnostics,
        },
    )


def memory_diagnostics(
    soak: dict[str, Any], container_samples: list[dict[str, Any]], start: float, end: float
) -> dict[str, Any]:
    """Reported-only context for container memory; never changes a verdict.

    ``docker stats`` memory includes active file-backed page cache, which grows
    with data volume. The soak deliberately writes a fresh generation every
    cycle, so database size grows for the whole run. This splits each stateful
    container's cgroup memory and records database growth for correlation.
    """
    result: dict[str, Any] = {
        "database_bytes": growth(
            [(s["t"], s["database_bytes"]) for s in soak["samples"] if "database_bytes" in s],
            start,
            end,
            0,
        )
    }
    for service in CGROUP_SERVICES:
        rows = [
            s
            for s in container_samples
            if s["phase"] == "soak" and s["service"] == service and s.get("cgroup")
        ]
        if not rows:
            continue
        origin = rows[0]["t"]
        for field in ("anon", "shmem", "active_file", "file"):
            points = [(r["t"] - origin, r["cgroup"][field]) for r in rows if field in r["cgroup"]]
            if points:
                result[f"container.{service}.cgroup_{field}_bytes"] = growth(
                    points, start, end, 16 * MIB
                )
    return result


def overall_status(verdicts: list[dict[str, Any]], failure: str | None) -> int:
    if failure is not None or any(not item["correct"] for item in verdicts):
        return 1
    if any(not item["target_met"] for item in verdicts):
        return 3
    return 0


def summary_lines(verdicts: list[dict[str, Any]]) -> list[str]:
    lines = []
    for item in verdicts:
        label = {"pass": "PASS", "miss": "TARGET MISSED", "fail": "CORRECTNESS FAIL"}[
            item["status"]
        ]
        lines.append(f"{label}: {item['measurement']}: {item['result']}")
        lines.append(f"  target: {item['target']}")
    return lines


# --------------------------------------------------------------------- harness


class Baseline(IsolatedCompose):
    def __init__(self, project: str, options: argparse.Namespace) -> None:
        super().__init__(validate_project_name(project), options.report_dir)
        self.options = options
        self.key = "perf-" + uuid.uuid4().hex
        self.results: dict[str, Any] = {}
        self.phase_seconds: dict[str, float] = {}
        self.stats = StatsSampler(self.project, options.stats_seconds)

    def spec_arguments(self) -> list[str]:
        o = self.options
        return [
            "--companies",
            str(o.companies),
            "--filings-per-company",
            str(o.filings_per_company),
            "--facts-per-filing",
            str(o.facts_per_filing),
            "--duplicate-ratio",
            str(o.duplicate_ratio),
        ]

    def guide_sized(self) -> bool:
        o = self.options
        return (
            {
                "companies": o.companies,
                "filings_per_company": o.filings_per_company,
                "facts_per_filing": o.facts_per_filing,
                "duplicate_ratio": o.duplicate_ratio,
            }
            == GUIDE_DATASET
            and o.workers == GUIDE_WORKERS
            and o.read_rps == GUIDE_READ_RPS
            and o.read_seconds == GUIDE_READ_SECONDS
            and o.soak_seconds == GUIDE_SOAK_SECONDS
        )

    def phase(self, name: str, *arguments: str, timeout_seconds: float) -> dict[str, Any]:
        self.stats.phase = name
        print(f"performance phase {name}: started {datetime.now(UTC).isoformat()}", flush=True)
        started = time.monotonic()
        result = self.run(
            "run",
            "--no-deps",
            "--rm",
            "test",
            "python",
            PROBE,
            *arguments,
            capture=True,
            timeout_seconds=timeout_seconds,
        )
        self.phase_seconds[name] = round(time.monotonic() - started, 3)
        payload = parse_payload(result.stdout, name)
        print(f"performance phase {name}: finished in {self.phase_seconds[name]:.1f} s", flush=True)
        return payload

    def host_facts(self) -> dict[str, Any]:
        facts: dict[str, Any] = {}
        try:
            facts["host_loadavg_at_start"] = Path("/proc/loadavg").read_text().strip()
        except OSError:
            facts["host_loadavg_at_start"] = None
        facts["test_image_id"] = self.run_command(
            ["docker", "image", "inspect", "secrecon:test", "--format", "{{.Id}}"],
            display=False,
            timeout_seconds=30,
        ).stdout.strip()
        facts["container_resource_limits"] = "none set by compose.yaml/compose.test.yaml"
        # ``git_commit`` alone would misattribute results measured from an
        # uncommitted tree; record exactly which image inputs differ from it.
        status = self.run_command(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", *IMAGE_INPUTS],
            display=False,
            timeout_seconds=30,
        ).stdout
        facts["git_image_inputs_clean"] = not status.strip()
        facts["git_image_inputs_uncommitted"] = status.splitlines()
        return facts

    def measure(self) -> None:
        o = self.options
        spec = self.spec_arguments()
        results = self.results
        results["seed"] = self.phase("seed", "seed", *spec, timeout_seconds=900)
        limit = str(o.replay_limit_seconds)
        results["replay_single"] = self.phase(
            "replay-single",
            "replay",
            *spec,
            "--workers",
            "1",
            "--label",
            "single",
            "--limit-seconds",
            limit,
            timeout_seconds=o.replay_limit_seconds + 600,
        )
        results["replay_four"] = self.phase(
            "replay-four",
            "replay",
            *spec,
            "--workers",
            str(o.workers),
            "--label",
            "four",
            "--limit-seconds",
            limit,
            timeout_seconds=o.replay_limit_seconds + 600,
        )
        labels = ["single", "four"]
        if not o.skip_rebuild:
            results["rebuild"] = self.phase(
                "rebuild", "rebuild", *spec, "--label", "rebuild", timeout_seconds=3600
            )
            labels.append("rebuild")
        results["compare"] = self.phase("compare", "compare", *labels, timeout_seconds=1200)
        # Cold read: a fresh PostgreSQL process (empty shared buffers) and, inside
        # the probe, a fresh API process with an empty connection pool.
        self.stats.phase = "restart-postgres"
        self.run("restart", "postgres", timeout_seconds=120)
        self.run("up", "-d", "--wait", "--wait-timeout", "120", "postgres", timeout_seconds=180)
        results["load"] = self.phase(
            "load",
            "load",
            *spec,
            "--generation-label",
            "four",
            "--rps",
            str(o.read_rps),
            "--cold-seconds",
            str(o.read_seconds),
            "--warm-seconds",
            str(o.read_seconds),
            "--plans",
            timeout_seconds=2 * o.read_seconds + 900,
        )
        results["recovery"] = [
            self.phase(
                f"recovery-{trial}",
                "recovery",
                *spec,
                "--trial",
                str(trial),
                "--limit-seconds",
                str(o.recovery_limit_seconds),
                timeout_seconds=o.recovery_limit_seconds + 300,
            )
            for trial in range(1, o.recovery_trials + 1)
        ]
        rate = o.soak_rate
        if rate is None:
            measured = float(results["replay_four"]["jobs_per_second"] or 0)
            rate = round(min(0.5 * measured, 0.8 * DISPATCH_CEILING_JOBS_PER_SECOND), 1)
        results["soak_rate_basis"] = (
            "explicit --soak-rate" if o.soak_rate is not None else "50% of four-worker replay"
        )
        results["soak"] = self.phase(
            "soak",
            "soak",
            *spec,
            "--workers",
            str(o.workers),
            "--seconds",
            str(o.soak_seconds),
            "--rate",
            str(rate),
            "--sample-seconds",
            str(o.sample_seconds),
            "--reference-digest",
            results["replay_single"]["digest"]["sha256"],
            timeout_seconds=o.soak_seconds + 1500,
        )

    def verdicts(self) -> list[dict[str, Any]]:
        r = self.results
        items: list[dict[str, Any]] = []
        if "replay_single" in r and "replay_four" in r:
            items.append(
                replay_verdict(
                    r["replay_single"], r["replay_four"], r.get("compare"), r.get("rebuild")
                )
            )
        if "load" in r:
            items.append(read_verdict(r["load"]))
        if r.get("recovery"):
            items.append(recovery_verdict(r["recovery"]))
        if "soak" in r:
            items.append(
                soak_verdict(r["soak"], r["replay_single"]["digest"]["sha256"], self.stats.samples)
            )
        return items

    def execute(self) -> tuple[Path, int]:
        started_at = datetime.now(UTC)
        failure: str | None = None
        verdicts: list[dict[str, Any]] = []
        try:
            self.assert_project_unused()
            self.collect_environment()
            self.stats.start()
            self.run(
                "run",
                "--build",
                "--rm",
                "test",
                "python",
                "-c",
                "print('performance baseline dependencies ready')",
                timeout_seconds=1800,
            )
            self.environment.update(self.host_facts())
            self.measure()
        except BaseException as exc:
            failure = f"{type(exc).__name__}: {exc}"
            if not isinstance(exc, Exception):
                raise
        finally:
            self.stats.stop()
            cleanup_error = self.down()
            if cleanup_error is not None and failure is None:
                failure = cleanup_error
            try:
                verdicts = self.verdicts()
            except (KeyError, TypeError, ValueError) as exc:
                failure = failure or f"verdict evaluation failed: {type(exc).__name__}: {exc}"
            finished_at = datetime.now(UTC)
            status = overall_status(verdicts, failure)
            report = {
                "schema_version": 1,
                "scenario": "m6.2_performance_baseline",
                "guide_sized": self.guide_sized(),
                "parameters": {
                    key: (str(value) if isinstance(value, Path) else value)
                    for key, value in vars(self.options).items()
                },
                "targets": TARGETS,
                "environment": self.environment,
                "requested_project": self.requested_project,
                "project": self.project,
                "run_key": self.key,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
                "exit_status": status,
                "failure": failure,
                "cleanup_error": cleanup_error,
                "phase_seconds": self.phase_seconds,
                "verdicts": verdicts,
                "container_stats_by_phase": self.stats.by_phase(),
                "container_stats_errors": self.stats.errors,
                "container_stats": self.stats.samples,
                "results": self.results,
                "commands": self.command_records(),
            }
            self.report_dir.mkdir(parents=True, exist_ok=True)
            path = self.report_dir / f"{started_at:%Y%m%dT%H%M%SZ}-{self.key}.json"
            path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"performance report: {path}")
        return path, status


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--project",
        default=PROJECT_PREFIX + uuid.uuid4().hex[:12],
        help=f"base name for a generated, isolated Compose project ({PROJECT_PREFIX}<name>)",
    )
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--companies", type=int, default=GUIDE_DATASET["companies"])
    parser.add_argument(
        "--filings-per-company", type=int, default=GUIDE_DATASET["filings_per_company"]
    )
    parser.add_argument("--facts-per-filing", type=int, default=GUIDE_DATASET["facts_per_filing"])
    parser.add_argument("--duplicate-ratio", type=float, default=GUIDE_DATASET["duplicate_ratio"])
    parser.add_argument("--workers", type=int, default=GUIDE_WORKERS)
    parser.add_argument("--replay-limit-seconds", type=float, default=1800)
    parser.add_argument("--skip-rebuild", action="store_true")
    parser.add_argument("--read-rps", type=float, default=GUIDE_READ_RPS)
    parser.add_argument("--read-seconds", type=float, default=GUIDE_READ_SECONDS)
    parser.add_argument("--recovery-trials", type=int, default=3)
    parser.add_argument("--recovery-limit-seconds", type=float, default=300)
    parser.add_argument("--soak-seconds", type=float, default=GUIDE_SOAK_SECONDS)
    parser.add_argument(
        "--soak-rate",
        type=float,
        default=None,
        help="steady arrival rate in jobs/s (default: 50%% of the measured four-worker rate)",
    )
    parser.add_argument("--sample-seconds", type=float, default=15)
    parser.add_argument("--stats-seconds", type=float, default=15)
    return parser.parse_args(argv)


def validate_options(options: argparse.Namespace) -> None:
    if options.workers < 1 or options.recovery_trials < 1:
        raise ValueError("--workers and --recovery-trials must be at least 1")
    for name in ("read_rps", "read_seconds", "soak_seconds", "sample_seconds", "stats_seconds"):
        if getattr(options, name) <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be positive")
    if (
        options.soak_rate is not None
        and not 0 < options.soak_rate < DISPATCH_CEILING_JOBS_PER_SECOND
    ):
        raise ValueError("--soak-rate must be positive and below the 50 jobs/s dispatch ceiling")


def main(argv: list[str] | None = None) -> int:
    options = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        project = validate_project_name(options.project)
    except UnsafeProject as exc:
        print(f"SAFETY REFUSAL: {exc}", file=sys.stderr)
        return 2
    try:
        validate_options(options)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"performance baseline: {project}")
    baseline = Baseline(project, options)
    print(f"guide-sized dataset and durations: {baseline.guide_sized()}")
    path, status = baseline.execute()
    report = json.loads(path.read_text(encoding="utf-8"))
    for line in summary_lines(report["verdicts"]):
        print(line)
    if report["failure"]:
        print(f"FAIL: {report['failure']}", file=sys.stderr)
    print(
        {
            0: "PASS: all correctness checks and proposed targets",
            1: "FAIL: correctness failure or harness error",
            3: "CORRECT, TARGET MISSED: see report and explain before revising targets",
        }[status]
    )
    print(f"  {path}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
