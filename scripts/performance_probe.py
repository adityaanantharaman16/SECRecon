"""Stack-side phases for the M6.2 performance baseline.

Run only by ``scripts/performance_baseline.py`` inside the ``test`` service of a
generated ``secrecon-perf-*`` Compose project. Every phase prints exactly one
``PERF_JSON:`` line that the host harness parses; nothing here is a benchmark
claim by itself.

The dataset is fully synthetic: CIKs 0990000000-0990000999, accessions
``0990000xxx-25-xxxxxx``, ``https://synthetic.invalid/`` URLs and entity names
beginning "Synthetic Performance Company". It never contains SEC data and no
phase makes a network request outside the isolated Compose network.

Workers are the production composition (``services()`` + ``Worker`` +
``CoreHandlers``, as ``secrecon worker``) in separate OS processes consuming
one Redis Streams consumer group and committing through the fenced SQL lease
path. The parent process plays ``secrecon dispatch``: every two seconds it runs
``store.sweep`` and one ``Queue.dispatch`` batch (at most 100 jobs).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import resource
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from secrecon.domain.types import canonical
from secrecon.ingestion.adapters import parse
from secrecon.jobs import store
from secrecon.jobs.handlers import CoreHandlers
from secrecon.jobs.queue import Queue
from secrecon.jobs.worker import Worker
from secrecon.orchestration.replay import digest, rebuild
from secrecon.runtime import services, shutdown_event
from secrecon.storage.archive import Manifest

JSON_MARKER = "PERF_JSON:"
DATASET_VERSION = "synthetic-perf-v1"
SYNTHETIC_CIK_BASE = 990_000_000
SYNTHETIC_CORRELATION = "synthetic-performance-fixture"
PERIODS = (
    ("2024-01-01", "2024-03-31", "Q1"),
    ("2024-04-01", "2024-06-30", "Q2"),
    ("2024-07-01", "2024-09-30", "Q3"),
    ("2024-10-01", "2024-12-31", "Q4"),
    ("2024-01-01", "2024-12-31", "FY"),
)
CONCEPTS = tuple(f"SyntheticMetric{index:02d}" for index in range(20))
FETCHED_AT = "2025-01-01T00:00:00+00:00"
VALUE_TOKEN = re.compile(r'"__VALUE__(-?\d+\.\d+)"')
WORK_DIR = Path("/tmp/secrecon-perf")
# Production dispatcher cadence (src/secrecon/commands/jobs.py: dispatch).
DISPATCH_INTERVAL_SECONDS = 2.0
DISPATCH_BATCH_LIMIT = 100  # Queue.dispatch LIMIT
POLL_SECONDS = 0.25


@dataclass(frozen=True)
class DatasetSpec:
    """Frozen dataset shape. The defaults are the M6 guide's required sizes."""

    companies: int = 100
    filings_per_company: int = 10
    facts_per_filing: int = 100
    duplicate_ratio: float = 0.10

    @property
    def filings(self) -> int:
        return self.companies * self.filings_per_company

    @property
    def observations(self) -> int:
        return self.filings * self.facts_per_filing

    @property
    def sources(self) -> int:
        """One Company Facts-shaped source per filing plus one discovery source per company."""
        return self.filings + self.companies

    @property
    def duplicate_deliveries(self) -> int:
        return round(self.filings * self.duplicate_ratio)

    def validate(self) -> None:
        if not 1 <= self.companies <= 999:
            raise ValueError("companies must be 1-999 to stay inside the synthetic CIK range")
        if not 1 <= self.filings_per_company <= 999:
            raise ValueError("filings_per_company must be 1-999")
        if not 1 <= self.facts_per_filing <= len(CONCEPTS) * len(PERIODS):
            raise ValueError("facts_per_filing exceeds distinct synthetic concept/period pairs")
        if not 0 <= self.duplicate_ratio <= 1:
            raise ValueError("duplicate_ratio must be between 0 and 1")


def synthetic_cik(company: int) -> str:
    return f"{SYNTHETIC_CIK_BASE + company:010d}"


def synthetic_accession(company: int, filing: int) -> str:
    return f"{synthetic_cik(company)}-25-{filing + 1:06d}"


def filing_form(filing: int) -> str:
    return "10-K" if filing % 4 == 3 else "10-Q"


def filing_date(filing: int) -> str:
    return f"2025-{(filing % 12) + 1:02d}-15"


def filing_body(spec: DatasetSpec, company: int, filing: int) -> bytes:
    """One filing-shaped Company Facts document with ``facts_per_filing`` observations.

    Every fact has a distinct (concept, period, accession), so each filing
    contributes exactly ``facts_per_filing`` unique normalized facts. Values are
    deterministic decimals with more precision than a binary float carries.
    """
    accession = synthetic_accession(company, filing)
    concepts: dict[str, Any] = {}
    for index in range(spec.facts_per_filing):
        concept = CONCEPTS[index % len(CONCEPTS)]
        start, end, fp = PERIODS[index // len(CONCEPTS)]
        value = f"{company * 1_000_003 + filing * 1_009 + index}.{(index * 7919) % 10_000:04d}"
        concepts.setdefault(concept, {"units": {"USD": []}})["units"]["USD"].append(
            {
                "start": start,
                "end": end,
                "val": "__VALUE__" + value,
                "accn": accession,
                "fy": 2024,
                "fp": fp,
                "form": filing_form(filing),
                "filed": filing_date(filing),
            }
        )
    payload = {
        "cik": SYNTHETIC_CIK_BASE + company,
        "entityName": f"Synthetic Performance Company {company:03d}",
        "facts": {"us-gaap": concepts},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    # Emit exact JSON number tokens (never binary floats) so the adapter parses Decimals.
    return VALUE_TOKEN.sub(r"\1", encoded).encode()


def submissions_body(spec: DatasetSpec, company: int) -> bytes:
    """Discovery-shaped source so company and filing list/detail routes have rows."""
    filings = range(spec.filings_per_company)
    payload = {
        "cik": SYNTHETIC_CIK_BASE + company,
        "name": f"Synthetic Performance Company {company:03d}",
        "tickers": [f"SYNP{company:03d}"],
        "filings": {
            "recent": {
                "accessionNumber": [synthetic_accession(company, f) for f in filings],
                "filingDate": [filing_date(f) for f in filings],
                "reportDate": [PERIODS[f % 4][1] for f in filings],
                "form": [filing_form(f) for f in filings],
                "primaryDocument": [f"synthetic-{f + 1:06d}.htm" for f in filings],
            },
            "files": [],
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def source_manifest(kind: str, key: str, cik: str, accession: str | None, body: bytes) -> Manifest:
    digest_hex = hashlib.sha256(body).hexdigest()
    return Manifest(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{DATASET_VERSION}/{kind}/{key}")),
        kind=kind,  # type: ignore[arg-type,unused-ignore]
        url=f"https://synthetic.invalid/{DATASET_VERSION}/{kind}/{key}.json",
        requested_at=FETCHED_AT,
        fetched_at=FETCHED_AT,
        status=200,
        headers={"content-type": "application/json"},
        sha256=digest_hex,
        byte_length=len(body),
        blob_key=f"blobs/sha256/{digest_hex}",
        cik=cik,
        accession=accession,
        correlation_id=SYNTHETIC_CORRELATION,
    )


def manifests(spec: DatasetSpec) -> Iterator[tuple[Manifest, bytes]]:
    """Deterministic sources: identical inputs for every replay mode.

    Per company: one submissions source (company and filing rows), then one
    facts source per filing (``facts_per_filing`` observations each).
    """
    for company in range(spec.companies):
        cik = synthetic_cik(company)
        body = submissions_body(spec, company)
        yield source_manifest("submissions", cik, cik, None, body), body
        for filing in range(spec.filings_per_company):
            accession = synthetic_accession(company, filing)
            body = filing_body(spec, company, filing)
            yield source_manifest("facts", accession, cik, accession, body), body


def duplicate_indexes(spec: DatasetSpec) -> list[int]:
    """Evenly spaced filing (facts-source) indexes that receive one extra delivery."""
    count = spec.duplicate_deliveries
    if count == 0:
        return []
    step = spec.filings / count
    return sorted({int(index * step) for index in range(count)})


def deliveries(spec: DatasetSpec) -> list[tuple[Manifest, int]]:
    """Every (source, delivery number) a replay enqueues, in a fixed order."""
    duplicates = set(duplicate_indexes(spec))
    result: list[tuple[Manifest, int]] = []
    filing_index = 0
    for manifest, _ in manifests(spec):
        result.append((manifest, 0))
        if manifest.kind == "facts":
            if filing_index in duplicates:
                result.append((manifest, 1))
            filing_index += 1
    return result


def dataset_fingerprint(spec: DatasetSpec) -> str:
    """SHA-256 over every source identity and checksum; proves runs replay identical bytes."""
    joined = "\n".join(m.event_id + ":" + m.sha256 for m, _ in manifests(spec))
    return hashlib.sha256(joined.encode()).hexdigest()


def read_targets(spec: DatasetSpec, fact_filings: int = 20) -> dict[str, list[str]]:
    """Read-test keys derived from the frozen dataset itself, never from the database.

    Computing them offline keeps the cold run cold: no harness query touches
    PostgreSQL between the restart and the first timed request.
    """
    accessions = [
        synthetic_accession(company, filing)
        for company in range(spec.companies)
        for filing in range(spec.filings_per_company)
    ]
    ciks = [synthetic_cik(company) for company in range(spec.companies)]
    facts: list[str] = []
    step = max(1, spec.filings // fact_filings)
    for index in range(0, spec.filings, step)[:fact_filings]:
        company, filing = divmod(index, spec.filings_per_company)
        accession = synthetic_accession(company, filing)
        body = filing_body(spec, company, filing)
        manifest = source_manifest("facts", accession, synthetic_cik(company), accession, body)
        facts.extend(fact["fingerprint"] for fact in parse(manifest, body).facts)
    return {"accessions": accessions, "ciks": ciks, "facts": facts}


def emit(payload: dict[str, Any]) -> None:
    print(JSON_MARKER + json.dumps(payload, sort_keys=True, default=str), flush=True)


def rss_bytes(pid: int | str = "self") -> int:
    """Resident set size from /proc (Linux containers); 0 when unavailable."""
    try:
        with open(f"/proc/{pid}/statm", encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def peak_rss_bytes() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024  # Linux: KiB.


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; deterministic and dependency-free."""
    if not values:
        raise ValueError("percentile of an empty sample")
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    ordered = sorted(values)
    rank = -(-round(fraction * 1_000_000) * len(ordered) // 1_000_000)
    return ordered[max(1, min(len(ordered), rank)) - 1]


def generation_name(label: str) -> str:
    if not label or not label.replace("-", "").isalnum() or len(label) > 60:
        raise ValueError("generation label must be short and alphanumeric")
    return f"perf-{label}"


def run_prefix(run: str) -> str:
    if not run or not run.replace("-", "").isalnum():
        raise ValueError("run identifiers must be alphanumeric with hyphens")
    return f"perf:{run}:"


def machine() -> dict[str, Any]:
    """CPU/memory as seen inside the container (the Docker VM on Docker Desktop)."""
    model = ""
    memory = 0
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                memory = int(line.split()[1]) * 1024
                break
    except OSError:
        pass
    return {"cpu_model": model, "cpu_count": os.cpu_count(), "memory_bytes": memory}


@contextmanager
def telemetry_disabled() -> Iterator[None]:
    """The harness parent is a load generator, not a system component; no OTLP export."""
    previous = os.environ.get("SECRECON_OTLP_ENDPOINT")
    os.environ["SECRECON_OTLP_ENDPOINT"] = ""
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("SECRECON_OTLP_ENDPOINT", None)
        else:
            os.environ["SECRECON_OTLP_ENDPOINT"] = previous


@contextmanager
def unresponsive_collector() -> Iterator[str]:
    """A local TCP endpoint that never answers: every OTLP export runs into its timeout.

    The listener never calls ``accept`` and keeps a one-connection backlog, so it
    holds no growing state itself. It models a hung telemetry backend.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        listener.close()


# ------------------------------------------------------------------ SQL helpers


def open_jobs(engine: Engine, run: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(
                text(
                    "SELECT count(*) FROM jobs WHERE state IN ('queued','running','retry_wait') "
                    "AND idempotency_key LIKE :prefix"
                ),
                {"prefix": run_prefix(run) + "%"},
            )
        )


def job_summary(engine: Engine, run: str) -> dict[str, Any]:
    params = {"p": run_prefix(run) + "%"}
    with engine.connect() as connection:
        states = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                text(
                    "SELECT state,count(*) FROM jobs WHERE idempotency_key LIKE :p GROUP BY state"
                ),
                params,
            )
        }
        attempts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                text("""
                SELECT a.outcome,count(*) FROM job_attempts a JOIN jobs j ON j.id=a.job_id
                WHERE j.idempotency_key LIKE :p GROUP BY a.outcome
            """),
                params,
            )
        }
        owners = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                text("""
                SELECT a.owner,count(*) FROM job_attempts a JOIN jobs j ON j.id=a.job_id
                WHERE j.idempotency_key LIKE :p AND a.outcome='succeeded' GROUP BY a.owner
            """),
                params,
            )
        }
        window = connection.scalar(
            text("""
            SELECT EXTRACT(epoch FROM max(a.ended_at)-min(a.started_at))
            FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE j.idempotency_key LIKE :p
        """),
            params,
        )
        latency = connection.execute(
            text("""
            SELECT percentile_cont(ARRAY[0.5,0.95,0.99]) WITHIN GROUP (
                ORDER BY EXTRACT(epoch FROM updated_at-created_at)),
              max(EXTRACT(epoch FROM updated_at-created_at))
            FROM jobs WHERE idempotency_key LIKE :p AND state='succeeded'
        """),
            params,
        ).one()
    quantiles = [round(float(value), 3) for value in latency[0]] if latency[0] else []
    return {
        "job_states": states,
        "attempt_outcomes": attempts,
        "succeeded_attempts_by_owner": owners,
        "sql_attempt_window_seconds": round(float(window or 0), 3),
        "enqueue_to_success_seconds": {
            "p50": quantiles[0] if quantiles else None,
            "p95": quantiles[1] if quantiles else None,
            "p99": quantiles[2] if quantiles else None,
            "max": round(float(latency[1]), 3) if latency[1] is not None else None,
        },
    }


def create_generation(engine: Engine, generation: str) -> None:
    with engine.begin() as connection:
        exists = connection.scalar(
            text("SELECT name FROM generations WHERE name=:g"), {"g": generation}
        )
        if exists:
            raise ValueError(f"generation {generation} already exists; use a fresh label")
        connection.execute(
            text("INSERT INTO generations(name,parser_version) VALUES (:g,'sec-json-v1')"),
            {"g": generation},
        )


def enqueue_deliveries(
    engine: Engine, planned: list[tuple[Manifest, int]], generation: str, key_prefix: str
) -> int:
    """Durable jobs, one per delivery, committed atomically with their outbox rows.

    A duplicate delivery gets a distinct idempotency key so it is separately
    published, claimed and committed; the ``processing_runs`` guard in
    ``apply_projection`` must make its commit a no-op. The digest checks that.
    """
    with engine.begin() as connection:
        for manifest, delivery in planned:
            store.enqueue(
                connection,
                "normalize",
                {"event_id": manifest.event_id, "generation": generation},
                f"{key_prefix}{manifest.event_id}:{delivery}",
            )
    return len(planned)


class Dispatcher:
    """The ``secrecon dispatch`` loop body at production cadence: sweep, then one batch."""

    def __init__(self, engine: Engine, queue: Queue) -> None:
        self.engine, self.queue = engine, queue
        self.next_tick = 0.0
        self.ticks = 0
        self.published = 0
        self.renotified = 0

    def tick(self) -> None:
        self.renotified += store.sweep(self.engine)
        self.published += self.queue.dispatch(self.engine)
        self.ticks += 1

    def maybe_tick(self) -> None:
        now = time.monotonic()
        if now >= self.next_tick:
            self.tick()
            self.next_tick = now + DISPATCH_INTERVAL_SECONDS

    def summary(self) -> dict[str, Any]:
        return {
            "interval_seconds": DISPATCH_INTERVAL_SECONDS,
            "batch_limit": DISPATCH_BATCH_LIMIT,
            "ceiling_jobs_per_second": DISPATCH_BATCH_LIMIT / DISPATCH_INTERVAL_SECONDS,
            "ticks": self.ticks,
            "published_messages": self.published,
            "sweep_renotifications": self.renotified,
        }


# ------------------------------------------------------------- worker processes


class WorkerPool:
    """Production worker loops in separate processes sharing one Redis stream."""

    def __init__(
        self,
        count: int,
        stream: str,
        label: str,
        *,
        env: dict[str, str] | None = None,
        sample_seconds: float = 15,
    ) -> None:
        self.count, self.stream, self.label = count, stream, label
        self.env = {**os.environ, **(env or {})}
        self.sample_seconds = sample_seconds
        self.processes: list[subprocess.Popen[bytes]] = []
        self.logs: list[Path] = []
        self.sample_files: list[Path] = []

    def __enter__(self) -> WorkerPool:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        for index in range(self.count):
            owner = f"perf-{self.label}-{index}"
            log = WORK_DIR / f"{owner}.log"
            samples = WORK_DIR / f"{owner}.samples.jsonl"
            with log.open("wb") as handle:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        __file__,
                        "worker",
                        "--stream",
                        self.stream,
                        "--owner",
                        owner,
                        "--samples",
                        str(samples),
                        "--sample-seconds",
                        str(self.sample_seconds),
                    ],
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    env=self.env,
                )
            self.processes.append(process)
            self.logs.append(log)
            self.sample_files.append(samples)
        return self

    def rss(self) -> list[int]:
        return [rss_bytes(process.pid) for process in self.processes]

    def alive(self) -> int:
        return sum(process.poll() is None for process in self.processes)

    def stop(self) -> list[int]:
        for process in self.processes:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
        codes: list[int] = []
        for process in self.processes:
            try:
                codes.append(process.wait(timeout=60))
            except subprocess.TimeoutExpired:
                process.kill()
                codes.append(process.wait(timeout=10))
        return codes

    def records(self) -> dict[str, list[dict[str, Any]]]:
        """Per-worker samples plus the final record each worker writes at shutdown."""
        result: dict[str, list[dict[str, Any]]] = {}
        for path in self.sample_files:
            rows: list[dict[str, Any]] = []
            if path.exists():
                for line in path.read_text(errors="replace").splitlines():
                    if line.strip():
                        rows.append(json.loads(line))
            result[path.name.removesuffix(".samples.jsonl")] = rows
        return result

    def log_tails(self, limit: int = 3000) -> dict[str, str]:
        tails: dict[str, str] = {}
        for log in self.logs:
            try:
                tails[log.name] = log.read_text(errors="replace")[-limit:]
            except OSError:
                tails[log.name] = ""
        return tails

    def __exit__(self, *exc: object) -> None:
        self.stop()


def process_sample(deps: Any, started: float) -> dict[str, Any]:
    from secrecon.telemetry import runtime as telemetry

    return {
        "t": round(time.monotonic() - started, 1),
        "rss_bytes": rss_bytes(),
        "threads": threading.active_count(),
        "pool_checked_out": int(deps.engine.pool.checkedout()),
        "telemetry": {
            name: {
                "queued": delivery.items.qsize(),
                "capacity": delivery.items.maxsize,
                "dropped": delivery.dropped,
                "failed": delivery.failed,
            }
            for name, delivery in telemetry.current.deliveries.items()
        },
    }


def worker(stream: str, owner: str, samples: str, sample_seconds: float) -> None:
    """Child process mirroring ``secrecon worker`` with an explicit stream and owner."""
    stop = shutdown_event()
    started = time.monotonic()
    with services() as deps, open(samples, "a", encoding="utf-8") as output:
        deps.archive.initialize()
        queue = Queue(deps.redis, stream=stream)
        queue.initialize()
        handlers = CoreHandlers(deps.engine, deps.archive, deps.client)

        def sample_loop() -> None:
            while not stop.wait(sample_seconds):
                output.write(json.dumps(process_sample(deps, started)) + "\n")
                output.flush()

        sampler = threading.Thread(target=sample_loop, daemon=True, name="perf-sampler")
        sampler.start()
        Worker(deps.engine, queue, deps.settings, handlers, owner).run(stop)
        sampler.join(timeout=5)
        final = process_sample(deps, started)
        final.update({"final": True, "peak_rss_bytes": peak_rss_bytes()})
        output.write(json.dumps(final) + "\n")
        output.flush()


# ------------------------------------------------------------------------ seed


def seed(deps: Any, spec: DatasetSpec) -> dict[str, Any]:
    """Preserve every synthetic source body and manifest once (create-only)."""
    started = time.monotonic()
    deadline = started + 120
    while True:
        try:
            deps.archive.initialize()
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)
    archive_ready = time.monotonic() - started

    def put(item: tuple[Manifest, bytes]) -> int:
        manifest, body = item
        deps.archive.put_once(manifest.blob_key, body, "application/json")
        deps.archive.put_once(
            f"events/{manifest.event_id}.json",
            canonical(manifest.model_dump()).encode(),
            "application/json",
        )
        return len(body)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        sizes = list(pool.map(put, manifests(spec)))
    kinds: dict[str, int] = {}
    for manifest, _ in manifests(spec):
        kinds[manifest.kind] = kinds.get(manifest.kind, 0) + 1
    return {
        "phase": "seed",
        "dataset_version": DATASET_VERSION,
        "dataset_fingerprint": dataset_fingerprint(spec),
        "spec": asdict(spec),
        "observations": spec.observations,
        "sources": len(sizes),
        "sources_by_kind": kinds,
        "source_bytes": sum(sizes),
        "planned_deliveries": len(deliveries(spec)),
        "duplicate_deliveries": len(duplicate_indexes(spec)),
        "archive_ready_seconds": round(archive_ready, 3),
        "seconds": round(time.monotonic() - started, 3),
        "machine": machine(),
    }


# ---------------------------------------------------------------------- replay


def generation_counts(deps: Any, generation: str) -> dict[str, int]:
    with deps.engine.connect() as connection:
        return {
            table: int(
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE generation=:g"), {"g": generation}
                )
                or 0
            )
            # Table identifiers come solely from this fixed tuple.
            for table in ("processing_runs", "companies", "filings", "facts", "fact_provenance")
        }


def replay(
    deps: Any, spec: DatasetSpec, workers: int, label: str, limit_s: float
) -> dict[str, Any]:
    """Drain one replay generation through worker processes, the outbox and Redis."""
    generation = generation_name(label)
    run = f"{label}-{uuid.uuid4().hex[:8]}"
    create_generation(deps.engine, generation)
    stream = f"perf:{run}"
    queue = Queue(deps.redis, stream=stream)
    queue.initialize()
    planned = deliveries(spec)
    enqueue_started = time.monotonic()
    jobs = enqueue_deliveries(deps.engine, planned, generation, run_prefix(run))
    enqueue_seconds = time.monotonic() - enqueue_started
    dispatcher = Dispatcher(deps.engine, queue)
    progress: list[dict[str, Any]] = []
    remaining = jobs
    peak_worker_rss = 0
    with WorkerPool(workers, stream, label, sample_seconds=5) as pool:
        started = time.monotonic()
        deadline = started + limit_s
        next_progress = started
        while time.monotonic() < deadline:
            dispatcher.maybe_tick()
            remaining = open_jobs(deps.engine, run)
            peak_worker_rss = max([peak_worker_rss, *pool.rss()])
            now = time.monotonic()
            if now >= next_progress or remaining == 0:
                progress.append({"t": round(now - started, 2), "open_jobs": remaining})
                next_progress = now + 5
            if remaining == 0 or pool.alive() < workers:
                break
            time.sleep(POLL_SECONDS)
        elapsed = time.monotonic() - started
        exit_codes = pool.stop()
        records = pool.records()
        tails = pool.log_tails() if any(code != 0 for code in exit_codes) or remaining else {}
    digest_started = time.monotonic()
    with deps.engine.connect() as connection:
        result_digest = digest(connection, generation)
    digest_seconds = time.monotonic() - digest_started
    return {
        "phase": "replay",
        "mode": "worker-jobs",
        "label": label,
        "generation": generation,
        "workers": workers,
        "dataset_fingerprint": dataset_fingerprint(spec),
        "jobs": jobs,
        "unique_sources": spec.sources,
        "observations": spec.observations,
        "duplicate_deliveries": jobs - spec.sources,
        "enqueue_seconds": round(enqueue_seconds, 3),
        "drain_seconds": round(elapsed, 3),
        "completed": remaining == 0,
        "open_jobs_at_end": remaining,
        "jobs_per_second": round(jobs / elapsed, 2) if elapsed else None,
        "observations_per_second": round(spec.observations / elapsed, 1) if elapsed else None,
        "counts": generation_counts(deps, generation),
        "digest": result_digest,
        "digest_seconds": round(digest_seconds, 3),
        "dispatcher": dispatcher.summary(),
        "worker_exit_codes": exit_codes,
        "worker_final": {
            owner: rows[-1] for owner, rows in records.items() if rows and rows[-1].get("final")
        },
        "worker_log_tails": tails,
        "max_sampled_worker_rss_bytes": peak_worker_rss,
        "progress": progress,
        **job_summary(deps.engine, run),
    }


def sequential_rebuild(deps: Any, spec: DatasetSpec, label: str) -> dict[str, Any]:
    """The production offline replay path (``secrecon replay``) as a third baseline."""
    generation = generation_name(label)
    started = time.monotonic()
    result = rebuild(deps.engine, deps.archive, generation)
    elapsed = time.monotonic() - started
    return {
        "phase": "rebuild",
        "mode": "sequential-rebuild",
        "label": label,
        "generation": generation,
        "workers": 1,
        "dataset_fingerprint": dataset_fingerprint(spec),
        "seconds": round(elapsed, 3),
        "observations_per_second": round(spec.observations / elapsed, 1) if elapsed else None,
        "counts": generation_counts(deps, generation),
        "digest": result,
    }


# Independent of ``replay.digest``: a row-level multiset difference in SQL per
# generation-scoped table, so a digest bug cannot mask divergent output.
# Identifiers come solely from this fixed mapping.
COMPARE_QUERIES = {
    "companies": "SELECT data FROM companies WHERE generation=:g",
    "filings": "SELECT data FROM filings WHERE generation=:g",
    "facts": "SELECT data FROM facts WHERE generation=:g",
    "fact_provenance": (
        "SELECT fingerprint,event_id,locator,parser_version FROM fact_provenance WHERE generation=:g"
    ),
    "filing_sources": "SELECT accession,event_id,role FROM filing_sources WHERE generation=:g",
    "amendment_links": (
        "SELECT l.data FROM amendment_heads h JOIN amendment_links l ON l.id=h.link_id "
        "WHERE h.generation=:g"
    ),
}


def compare(deps: Any, labels: list[str]) -> dict[str, Any]:
    """Every generation's rows must equal the first label's rows exactly (EXCEPT ALL)."""
    if len(labels) < 2:
        raise ValueError("compare needs at least two generation labels")
    reference = generation_name(labels[0])
    tables: dict[str, dict[str, Any]] = {}
    started = time.monotonic()
    with deps.engine.connect() as connection:
        for label in labels[1:]:
            other = generation_name(label)
            for table, query in COMPARE_QUERIES.items():
                a, b = query.replace(":g", ":a"), query.replace(":g", ":b")
                params = {"a": reference, "b": other}
                only_reference = int(
                    connection.scalar(text(f"SELECT count(*) FROM ({a} EXCEPT ALL {b}) d"), params)
                    or 0
                )
                only_other = int(
                    connection.scalar(text(f"SELECT count(*) FROM ({b} EXCEPT ALL {a}) d"), params)
                    or 0
                )
                rows = int(connection.scalar(text(f"SELECT count(*) FROM ({b}) r"), params) or 0)
                tables[f"{label}.{table}"] = {
                    "rows": rows,
                    f"only_in_{labels[0]}": only_reference,
                    f"only_in_{label}": only_other,
                }
    match = all(
        value == 0
        for entry in tables.values()
        for key, value in entry.items()
        if key.startswith("only_in_")
    ) and all(entry["rows"] > 0 for key, entry in tables.items() if key.endswith(".facts"))
    return {
        "phase": "compare",
        "reference": reference,
        "generations": [generation_name(label) for label in labels],
        "tables": tables,
        "match": match,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------- read latency

ROUTES = (
    "company-list",
    "company-search",
    "filings-by-cik",
    "filings-by-form",
    "filing-detail",
    "facts-by-accession",
    "facts-by-concept",
    "facts-by-cik-period",
    "fact-provenance",
    "sources-by-cik",
)


def read_request(targets: dict[str, list[str]], generation: str, index: int) -> tuple[str, str]:
    """Deterministic rotation over indexed list and detail routes, page size <= 100."""
    g = f"generation={generation}"
    turn = index // len(ROUTES)
    accession = targets["accessions"][(turn * 7) % len(targets["accessions"])]
    cik = targets["ciks"][(turn * 3) % len(targets["ciks"])]
    fact = targets["facts"][(turn * 11) % len(targets["facts"])]
    concept = CONCEPTS[turn % len(CONCEPTS)]
    paths = (
        f"/v1/companies?{g}&limit=100",
        f"/v1/companies?{g}&q=synthetic&limit=100",
        f"/v1/filings?{g}&cik={cik}&limit=100",
        f"/v1/filings?{g}&form=10-Q&limit=100",
        f"/v1/filings/{accession}?{g}",
        f"/v1/facts?{g}&accession={accession}&limit=100",
        f"/v1/facts?{g}&concept={concept}&limit=100",
        f"/v1/facts?{g}&cik={cik}&period_end=2024-12-31&limit=100",
        f"/v1/facts/{fact}/provenance?{g}&limit=100",
        f"/v1/sources?cik={cik}&limit=100",
    )
    slot = index % len(ROUTES)
    return ROUTES[slot], paths[slot]


def page_items(route: str, body: dict[str, Any]) -> int:
    if route in {"filing-detail", "fact-provenance"}:
        return len(body.get("sources", []))
    return len(body.get("items", []))


def wait_ready(base_url: str, process: subprocess.Popen[bytes], timeout_s: float = 120) -> float:
    import httpx

    started = time.monotonic()
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"API process exited with {process.returncode}")
        try:
            if httpx.get(base_url + "/health/ready", timeout=2).status_code == 200:
                return time.monotonic() - started
        except httpx.HTTPError:
            pass
        if time.monotonic() - started > timeout_s:
            raise TimeoutError("API did not become ready")
        time.sleep(0.1)


def load_run(
    base_url: str,
    generation: str,
    targets: dict[str, list[str]],
    rps: float,
    seconds: float,
    label: str,
    api_pid: int,
) -> dict[str, Any]:
    """Open-loop fixed-rate schedule: late responses do not reduce offered load.

    Latency is measured from each request's scheduled send time, so client-side
    queueing (coordinated omission) is included rather than hidden.
    """
    import httpx

    total = int(rps * seconds)
    interval = 1 / rps
    samples: list[tuple[float, float, str, str, int]] = []
    lock = threading.Lock()
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=64)
    client = httpx.Client(base_url=base_url, timeout=10, limits=limits)
    api_rss: list[int] = []
    started = time.monotonic()

    def one(index: int, due: float) -> None:
        route, path = read_request(targets, generation, index)
        status, items = "exception", 0
        try:
            response = client.get(path)
            status = str(response.status_code)
            if response.status_code == 200:
                items = page_items(route, response.json())
        except Exception as exc:  # recorded as an error class, never hidden
            status = type(exc).__name__
        finished = time.monotonic()
        with lock:
            samples.append((due - started, (finished - due) * 1000, route, status, items))

    max_lag = 0.0
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        futures = []
        for index in range(total):
            due = started + index * interval
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                max_lag = max(max_lag, -delay)
            futures.append(pool.submit(one, index, due))
            if index % int(rps * 10) == 0:
                api_rss.append(rss_bytes(api_pid))
        concurrent.futures.wait(futures)
    wall = time.monotonic() - started
    client.close()
    latencies = [sample[1] for sample in samples]
    errors: dict[str, int] = {}
    by_route: dict[str, list[float]] = {}
    per_minute: dict[int, list[float]] = {}
    for offset, latency, route, status, _ in samples:
        by_route.setdefault(route, []).append(latency)
        per_minute.setdefault(int(offset // 60), []).append(latency)
        if status != "200":
            errors[status] = errors.get(status, 0) + 1
    first_requests = [sample[1] for sample in sorted(samples)[: len(ROUTES) * 2]]
    return {
        "label": label,
        "generation": generation,
        "requests": total,
        "offered_rps": rps,
        "duration_seconds": seconds,
        "achieved_rps": round(total / wall, 2) if wall else None,
        "wall_seconds": round(wall, 3),
        "errors": errors,
        "error_rate": round(sum(errors.values()) / total, 5) if total else None,
        "p50_ms": round(percentile(latencies, 0.50), 2),
        "p95_ms": round(percentile(latencies, 0.95), 2),
        "p99_ms": round(percentile(latencies, 0.99), 2),
        "max_ms": round(max(latencies), 2),
        "mean_ms": round(statistics.fmean(latencies), 2),
        "first_20_requests_max_ms": round(max(first_requests), 2),
        "per_minute_p95_ms": [
            round(percentile(values, 0.95), 2) for _, values in sorted(per_minute.items())
        ],
        "max_items_per_page": max((sample[4] for sample in samples), default=0),
        "schedule_max_lag_ms": round(max_lag * 1000, 2),
        "api_rss_bytes_max": max(api_rss, default=0),
        "by_route": {
            route: {
                "requests": len(values),
                "p50_ms": round(percentile(values, 0.50), 2),
                "p95_ms": round(percentile(values, 0.95), 2),
                "max_ms": round(max(values), 2),
            }
            for route, values in sorted(by_route.items())
        },
    }


def query_plans(deps: Any, generation: str, targets: dict[str, list[str]]) -> dict[str, Any]:
    """EXPLAIN ANALYZE of the list-query shapes, captured after timing (keeps cold cold)."""
    accession = targets["accessions"][0]
    cik = targets["ciks"][0]
    statements = {
        "facts_by_accession": (
            "SELECT t.fingerprint,t.data FROM facts t WHERE t.fingerprint>'' AND "
            "t.generation=:g AND t.accession=:a ORDER BY t.fingerprint LIMIT 101",
            {"g": generation, "a": accession},
        ),
        "facts_by_concept": (
            "SELECT t.fingerprint,t.data FROM facts t WHERE t.fingerprint>'' AND "
            "t.generation=:g AND t.concept=:c ORDER BY t.fingerprint LIMIT 101",
            {"g": generation, "c": CONCEPTS[0]},
        ),
        "facts_by_cik_period": (
            "SELECT t.fingerprint,t.data FROM facts t WHERE t.fingerprint>'' AND "
            "t.generation=:g AND t.cik=:c AND t.data->>'end_date'=:e "
            "ORDER BY t.fingerprint LIMIT 101",
            {"g": generation, "c": cik, "e": "2024-12-31"},
        ),
        "filings_by_cik": (
            "SELECT t.accession,t.data FROM filings t WHERE t.accession>'' AND "
            "t.generation=:g AND t.cik=:c ORDER BY t.accession LIMIT 101",
            {"g": generation, "c": cik},
        ),
        "page_freshness": (
            "SELECT max(s.fetched_at) FROM source_events s JOIN processing_runs p "
            "USING(event_id) WHERE p.generation=:g AND p.status='succeeded'",
            {"g": generation},
        ),
    }
    plans: dict[str, Any] = {}
    with deps.engine.connect() as connection:
        for name, (sql, params) in statements.items():
            rows = connection.execute(text("EXPLAIN (ANALYZE, COSTS OFF) " + sql), params)
            plans[name] = [str(row[0]) for row in rows]
    return plans


def load(
    deps: Any,
    spec: DatasetSpec,
    generation_label: str,
    rps: float,
    cold_seconds: float,
    warm_seconds: float,
    plans: bool,
) -> dict[str, Any]:
    """Cold then warmed runs against one fresh ``secrecon serve`` process.

    The harness restarts PostgreSQL immediately before this phase, and the API
    process starts here, so the cold run begins with empty shared buffers, an
    empty connection pool and an unwarmed interpreter.
    """
    generation = generation_name(generation_label)
    targets = read_targets(spec)
    base_url = "http://127.0.0.1:8000"
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    log = WORK_DIR / "api.log"
    with log.open("wb") as handle:
        api = subprocess.Popen(
            [sys.executable, "-m", "secrecon.cli", "serve", "--host", "127.0.0.1"],
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    try:
        ready_seconds = wait_ready(base_url, api)
        cold = load_run(base_url, generation, targets, rps, cold_seconds, "cold", api.pid)
        warm = load_run(base_url, generation, targets, rps, warm_seconds, "warm", api.pid)
    finally:
        api.send_signal(signal.SIGTERM)
        try:
            api.wait(timeout=30)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait(timeout=10)
    result: dict[str, Any] = {
        "phase": "load",
        "generation": generation,
        "api_command": "python -m secrecon.cli serve --host 127.0.0.1 (single uvicorn process)",
        "api_ready_seconds": round(ready_seconds, 3),
        "targets": {key: len(values) for key, values in targets.items()},
        "cold": cold,
        "warm": warm,
    }
    if plans:
        result["query_plans"] = query_plans(deps, generation, targets)
    return result


# --------------------------------------------------------------- crash recovery


def attempt_timeline(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "token": int(attempt["token"]),
            "owner": attempt["owner"],
            "outcome": attempt["outcome"],
            "started_at": attempt["started_at"],
            "ended_at": attempt["ended_at"],
        }
        for attempt in record["attempt_history"]
    ]


def recovery(deps: Any, spec: DatasetSpec, limit_s: float, trial: int) -> dict[str, Any]:
    """SIGKILL a worker process that holds a lease, then time the takeover.

    The victim runs the production ``Worker`` loop with the configured lease:
    it reads the Redis delivery, claims the SQL lease, prepares the projection
    (archive read, checksum, parse) and stalls inside the fenced commit before
    any projection write. After ``kill -9``, one fresh worker process and the
    production dispatcher cadence run until SQL shows the job succeeded.
    Correctness is judged from SQL (ADR 0002): the victim's attempt ends
    ``lease_expired``, the rescuer's ``succeeded`` with the next fencing token,
    one committed processing run and exactly the source's facts/provenance.
    Remaining Redis deliveries are reported, not gated: a delivery for an
    already-terminal job is harmless transport state.
    """
    lease_seconds = deps.settings.lease_seconds
    label = f"recovery-{trial}-{uuid.uuid4().hex[:6]}"
    generation = generation_name(label)
    create_generation(deps.engine, generation)
    # The first facts source of the *seeded* dataset: its archived body carries
    # exactly ``spec.facts_per_filing`` observations, the expected SQL result.
    manifest = next(m for m, _ in manifests(spec) if m.kind == "facts")
    stream = f"perf:{label}"
    queue = Queue(deps.redis, stream=stream)
    queue.initialize()
    with deps.engine.begin() as connection:
        job = store.enqueue(
            connection,
            "normalize",
            {"event_id": manifest.event_id, "generation": generation},
            f"{run_prefix(label)}{manifest.event_id}:0",
        )
    dispatcher = Dispatcher(deps.engine, queue)
    dispatcher.tick()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    ready = WORK_DIR / f"{label}.ready"
    victim_log = WORK_DIR / f"{label}-victim.log"
    with victim_log.open("wb") as handle:
        victim_process = subprocess.Popen(
            [sys.executable, __file__, "victim", "--stream", stream, "--ready", str(ready)],
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 60
        while not ready.exists():
            if victim_process.poll() is not None:
                raise RuntimeError(
                    "victim worker exited before stalling: "
                    + victim_log.read_text(errors="replace")[-1000:]
                )
            if time.monotonic() > deadline:
                raise TimeoutError("victim worker did not claim its lease")
            time.sleep(0.02)
        claimed_state = str(store.inspect(deps.engine, job)["state"])
        victim_process.send_signal(signal.SIGKILL)
        victim_process.wait(timeout=10)
        killed_at = time.monotonic()
    finally:
        if victim_process.poll() is None:
            victim_process.kill()
            victim_process.wait(timeout=10)
        ready.unlink(missing_ok=True)
    with deps.engine.connect() as connection:
        lease_remaining = float(
            connection.scalar(
                text(
                    "SELECT EXTRACT(epoch FROM lease_until-clock_timestamp()) FROM jobs WHERE id=:id"
                ),
                {"id": job},
            )
            or 0
        )
    state = "running"
    reclaimed_after: float | None = None
    with WorkerPool(1, stream, f"rescuer-{trial}", sample_seconds=30) as pool:
        while time.monotonic() - killed_at < limit_s:
            dispatcher.maybe_tick()
            state = str(store.inspect(deps.engine, job)["state"])
            if reclaimed_after is None and state != "running":
                reclaimed_after = time.monotonic() - killed_at
            if state == "succeeded":
                break
            time.sleep(POLL_SECONDS)
        recovered = time.monotonic() - killed_at
        pool.stop()
    record = store.inspect(deps.engine, job)
    counts = generation_counts(deps, generation)
    pending = int(deps.redis.xpending(stream, "workers")["pending"])
    timeline = attempt_timeline(record)
    outcomes = [attempt["outcome"] for attempt in timeline]
    owners = [attempt["owner"] for attempt in timeline]
    tokens = [attempt["token"] for attempt in timeline]
    correct = (
        record["state"] == "succeeded"
        and outcomes == ["lease_expired", "succeeded"]
        and owners[:1] == ["perf-victim"]
        and len(owners) == 2
        and owners[1].startswith("perf-rescuer-")
        and tokens == [1, 2]
        and counts["processing_runs"] == 1
        and counts["facts"] == spec.facts_per_filing
        and counts["fact_provenance"] == spec.facts_per_filing
    )
    return {
        "phase": "recovery",
        "trial": trial,
        "lease_seconds": lease_seconds,
        "heartbeat_seconds": deps.settings.heartbeat_seconds,
        "claimed_state_before_kill": claimed_state,
        "lease_remaining_at_kill_seconds": round(lease_remaining, 3),
        "reclaimed_after_kill_seconds": (
            round(reclaimed_after, 3) if reclaimed_after is not None else None
        ),
        "state": record["state"],
        "recovered": record["state"] == "succeeded",
        "recovery_seconds": round(recovered, 3),
        "attempts": timeline,
        "event_states": [event["state"] for event in record["events"]],
        "counts": counts,
        "redis_pending_after": pending,
        "dispatcher": dispatcher.summary(),
        "correct": correct,
    }


def victim(stream: str, ready: str) -> None:
    """Child process: production worker loop that stalls inside the fenced commit."""
    with services() as deps:
        queue = Queue(deps.redis, stream=stream)
        handlers = CoreHandlers(deps.engine, deps.archive, deps.client)

        def stalled(lease: store.Lease) -> Any:
            handlers(lease)  # real preparation: manifest/body read, checksum and parse

            def commit(connection: Any) -> None:
                Path(ready).write_text("stalled before projection write", encoding="ascii")
                time.sleep(3600)  # SIGKILL arrives here, inside the uncommitted transaction

            return commit

        Worker(deps.engine, queue, deps.settings, stalled, "perf-victim").run(threading.Event())


# ------------------------------------------------------------------------ soak


def stack_sample(deps: Any, run: str, stream: str) -> dict[str, Any]:
    with deps.engine.connect() as connection:
        states = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                text(
                    "SELECT state,count(*) FROM jobs WHERE state IN "
                    "('queued','running','retry_wait') AND idempotency_key LIKE :p GROUP BY state"
                ),
                {"p": run_prefix(run) + "%"},
            )
        }
        connections = connection.scalar(
            text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()")
        )
        outbox = connection.scalar(text("SELECT count(*) FROM outbox WHERE sent_at IS NULL"))
        database_bytes = connection.scalar(text("SELECT pg_database_size(current_database())"))
        succeeded = connection.scalar(
            text("SELECT count(*) FROM jobs WHERE state='succeeded' AND idempotency_key LIKE :p"),
            {"p": run_prefix(run) + "%"},
        )
    redis_info = deps.redis.info()
    return {
        "open_jobs": sum(states.values()),
        "open_by_state": states,
        "succeeded": int(succeeded or 0),
        "outbox_unsent": int(outbox or 0),
        "redis_stream_length": int(deps.redis.xlen(stream)),
        "redis_pending": int(deps.redis.xpending(stream, "workers")["pending"]),
        "redis_used_memory_bytes": int(redis_info.get("used_memory", 0)),
        "redis_connected_clients": int(redis_info.get("connected_clients", 0)),
        "pg_connections": int(connections or 0),
        "database_bytes": int(database_bytes or 0),
    }


def soak(
    deps: Any,
    spec: DatasetSpec,
    workers: int,
    seconds: float,
    rate: float,
    sample_seconds: float,
    reference_digest: str | None,
) -> dict[str, Any]:
    """Fixed-rate arrivals of real normalize work against worker processes.

    Jobs arrive at a steady ``rate`` per second (open loop), cycling through the
    synthetic source set; every full cycle writes a fresh generation, so every
    job performs real projection work. Worker telemetry export is enabled
    against an unresponsive collector, exercising the bounded export queues
    under a hung backend. Growth verdicts are computed by the host harness.
    """
    run = "soak-" + uuid.uuid4().hex[:8]
    stream = f"perf:{run}"
    queue = Queue(deps.redis, stream=stream)
    queue.initialize()
    sources = [manifest for manifest, _ in manifests(spec)]
    total = int(seconds * rate)
    samples: list[dict[str, Any]] = []
    cycles: list[str] = []
    enqueued = 0
    dispatcher = Dispatcher(deps.engine, queue)
    with unresponsive_collector() as endpoint:
        env = {"SECRECON_OTLP_ENDPOINT": endpoint, "SECRECON_TELEMETRY_SERVICE": "perf-soak"}
        with WorkerPool(workers, stream, run, env=env, sample_seconds=sample_seconds) as pool:
            started = time.monotonic()
            next_sample = started
            while (now := time.monotonic()) - started < seconds:
                due = min(int((now - started) * rate) + 1, total)
                if due > enqueued:
                    with deps.engine.begin() as connection:
                        for index in range(enqueued, due):
                            cycle, position = divmod(index, len(sources))
                            generation = generation_name(f"{run}-c{cycle}")
                            if position == 0:
                                connection.execute(
                                    text(
                                        "INSERT INTO generations(name,parser_version) "
                                        "VALUES (:g,'sec-json-v1')"
                                    ),
                                    {"g": generation},
                                )
                                cycles.append(generation)
                            store.enqueue(
                                connection,
                                "normalize",
                                {"event_id": sources[position].event_id, "generation": generation},
                                f"{run_prefix(run)}{cycle}:{sources[position].event_id}",
                            )
                    enqueued = due
                dispatcher.maybe_tick()
                if now >= next_sample:
                    sample = stack_sample(deps, run, stream)
                    sample.update(
                        {
                            "t": round(now - started, 1),
                            "enqueued": enqueued,
                            "dispatcher_rss_bytes": rss_bytes(),
                            "worker_rss_bytes": pool.rss(),
                            "workers_alive": pool.alive(),
                        }
                    )
                    samples.append(sample)
                    next_sample += sample_seconds
                if pool.alive() < workers:
                    break
                time.sleep(POLL_SECONDS)
            load_end = time.monotonic()
            drain_deadline = load_end + 600
            while open_jobs(deps.engine, run) and time.monotonic() < drain_deadline:
                dispatcher.maybe_tick()
                time.sleep(POLL_SECONDS)
            drained = time.monotonic()
            final = stack_sample(deps, run, stream)
            final["worker_rss_bytes"] = pool.rss()
            exit_codes = pool.stop()
            records = pool.records()
            tails = pool.log_tails() if any(code != 0 for code in exit_codes) else {}
    summary = job_summary(deps.engine, run)
    with deps.engine.connect() as connection:
        per_cycle = []
        for generation in cycles:
            enqueued_in_cycle = int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM jobs WHERE idempotency_key LIKE :p "
                        "AND payload->>'generation'=:g"
                    ),
                    {"p": run_prefix(run) + "%", "g": generation},
                )
                or 0
            )
            per_cycle.append(
                {
                    "generation": generation,
                    "enqueued": enqueued_in_cycle,
                    **generation_counts(deps, generation),
                }
            )
        complete = [cycle for cycle in per_cycle if cycle["enqueued"] == len(sources)]
        checked = [complete[0], complete[-1]] if len(complete) > 1 else complete
        cycle_digests = {
            str(cycle["generation"]): digest(connection, str(cycle["generation"]))["sha256"]
            for cycle in checked
        }
        windows = [
            {
                "window_minutes": f"{int(row[0]) * 5}-{int(row[0]) * 5 + 5}",
                "jobs": int(row[1]),
                "p50_enqueue_to_success_seconds": round(float(row[2]), 3),
                "p95_enqueue_to_success_seconds": round(float(row[3]), 3),
            }
            for row in connection.execute(
                text("""
                WITH run AS (
                  SELECT created_at,updated_at FROM jobs
                  WHERE idempotency_key LIKE :p AND state='succeeded'
                ), origin AS (SELECT min(created_at) AS t0 FROM run)
                SELECT floor(EXTRACT(epoch FROM r.created_at-o.t0)/300),count(*),
                  percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(epoch FROM r.updated_at-r.created_at)),
                  percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY EXTRACT(epoch FROM r.updated_at-r.created_at))
                FROM run r CROSS JOIN origin o GROUP BY 1 ORDER BY 1
            """),
                {"p": run_prefix(run) + "%"},
            )
        ]
    succeeded = summary["job_states"].get("succeeded", 0)
    return {
        "phase": "soak",
        "workers": workers,
        "seconds": seconds,
        "offered_rate_jobs_per_second": rate,
        "sample_seconds": sample_seconds,
        "planned_jobs": total,
        "enqueued": enqueued,
        "load_seconds": round(load_end - started, 3),
        "achieved_offered_jobs_per_second": round(enqueued / (load_end - started), 3),
        "drain_after_load_seconds": round(drained - load_end, 3),
        "throughput_jobs_per_second": round(succeeded / (drained - started), 3),
        "observations_committed": sum(cycle["facts"] for cycle in per_cycle),
        "cycles": per_cycle,
        "complete_cycles": len(complete),
        "cycle_digests": cycle_digests,
        "reference_digest": reference_digest,
        "windows": windows,
        "final": final,
        "dispatcher": dispatcher.summary(),
        "dispatcher_peak_rss_bytes": peak_rss_bytes(),
        "telemetry_endpoint": "unresponsive local TCP listener (hung collector)",
        "worker_exit_codes": exit_codes,
        "worker_samples": records,
        "worker_log_tails": tails,
        "samples": samples,
        **summary,
    }


# ------------------------------------------------------------------------- CLI


def add_spec_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--companies", type=int, default=DatasetSpec.companies)
    command.add_argument("--filings-per-company", type=int, default=DatasetSpec.filings_per_company)
    command.add_argument("--facts-per-filing", type=int, default=DatasetSpec.facts_per_filing)
    command.add_argument("--duplicate-ratio", type=float, default=DatasetSpec.duplicate_ratio)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)
    add_spec_arguments(sub.add_parser("seed"))
    replay_command = sub.add_parser("replay")
    add_spec_arguments(replay_command)
    replay_command.add_argument("--workers", type=int, default=4)
    replay_command.add_argument("--label", required=True)
    replay_command.add_argument("--limit-seconds", type=float, default=1800)
    rebuild_command = sub.add_parser("rebuild")
    add_spec_arguments(rebuild_command)
    rebuild_command.add_argument("--label", required=True)
    compare_command = sub.add_parser("compare")
    compare_command.add_argument("labels", nargs="+")
    load_command = sub.add_parser("load")
    add_spec_arguments(load_command)
    load_command.add_argument("--generation-label", required=True)
    load_command.add_argument("--rps", type=float, default=20)
    load_command.add_argument("--cold-seconds", type=float, default=600)
    load_command.add_argument("--warm-seconds", type=float, default=600)
    load_command.add_argument("--plans", action="store_true")
    recovery_command = sub.add_parser("recovery")
    add_spec_arguments(recovery_command)
    recovery_command.add_argument("--limit-seconds", type=float, default=300)
    recovery_command.add_argument("--trial", type=int, default=1)
    soak_command = sub.add_parser("soak")
    add_spec_arguments(soak_command)
    soak_command.add_argument("--workers", type=int, default=4)
    soak_command.add_argument("--seconds", type=float, default=1800)
    soak_command.add_argument("--rate", type=float, required=True)
    soak_command.add_argument("--sample-seconds", type=float, default=15)
    soak_command.add_argument("--reference-digest")
    victim_command = sub.add_parser("victim")
    victim_command.add_argument("--stream", required=True)
    victim_command.add_argument("--ready", required=True)
    worker_command = sub.add_parser("worker")
    worker_command.add_argument("--stream", required=True)
    worker_command.add_argument("--owner", required=True)
    worker_command.add_argument("--samples", required=True)
    worker_command.add_argument("--sample-seconds", type=float, default=15)
    return parser.parse_args(argv)


def spec_from(args: argparse.Namespace) -> DatasetSpec:
    spec = DatasetSpec(
        companies=args.companies,
        filings_per_company=args.filings_per_company,
        facts_per_filing=args.facts_per_filing,
        duplicate_ratio=args.duplicate_ratio,
    )
    spec.validate()
    return spec


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.phase == "victim":
        victim(args.stream, args.ready)
        return
    if args.phase == "worker":
        worker(args.stream, args.owner, args.samples, args.sample_seconds)
        return
    with telemetry_disabled(), services() as deps:
        if args.phase == "seed":
            emit(seed(deps, spec_from(args)))
        elif args.phase == "replay":
            emit(replay(deps, spec_from(args), args.workers, args.label, args.limit_seconds))
        elif args.phase == "rebuild":
            emit(sequential_rebuild(deps, spec_from(args), args.label))
        elif args.phase == "compare":
            emit(compare(deps, args.labels))
        elif args.phase == "load":
            emit(
                load(
                    deps,
                    spec_from(args),
                    args.generation_label,
                    args.rps,
                    args.cold_seconds,
                    args.warm_seconds,
                    args.plans,
                )
            )
        elif args.phase == "recovery":
            emit(recovery(deps, spec_from(args), args.limit_seconds, args.trial))
        elif args.phase == "soak":
            emit(
                soak(
                    deps,
                    spec_from(args),
                    args.workers,
                    args.seconds,
                    args.rate,
                    args.sample_seconds,
                    args.reference_digest,
                )
            )


if __name__ == "__main__":
    main()
