"""Host-harness safety and verdict logic for the M6.2 baseline (no Docker required)."""

import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts import performance_baseline as baseline
from scripts import performance_probe as probe


@pytest.mark.parametrize(
    "project",
    [
        "secrecon",
        "secrecon-dev",
        "secrecon-release",
        "secrecon-test",
        "secrecon-drill-m6",
        "secrecon_perf",
        "other-perf-safe-looking",
        "secrecon-perf-",
        "secrecon-perf-" + "x" * 41,
        "SECRECON",
    ],
)
def test_performance_harness_refuses_non_perf_projects(project):
    with pytest.raises(baseline.UnsafeProject):
        baseline.validate_project_name(project)


def test_performance_harness_accepts_only_explicit_perf_namespace():
    assert baseline.validate_project_name("secrecon-perf-m6-01") == "secrecon-perf-m6-01"


def options(**overrides):
    args = baseline.parse_args(["--project", "secrecon-perf-unit"])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_harness_pins_generated_child_project_and_test_overlay(tmp_path):
    harness = baseline.Baseline("secrecon-perf-unit", options(report_dir=tmp_path))
    assert harness.project.startswith("secrecon-perf-unit-")
    assert harness.compose == [
        "docker",
        "compose",
        "-p",
        harness.project,
        "-f",
        "compose.yaml",
        "-f",
        "compose.test.yaml",
    ]
    assert not harness.owns_project
    assert harness.down() is None  # never removes a project it did not prove unused


def test_harness_refuses_preexisting_child_project(monkeypatch, tmp_path):
    harness = baseline.Baseline("secrecon-perf-unit", options(report_dir=tmp_path))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="existing\n"),
    )
    with pytest.raises(baseline.UnsafeProject, match="pre-existing Compose project"):
        harness.assert_project_unused()
    assert not harness.owns_project


def test_guide_constants_match_probe_defaults():
    assert baseline.GUIDE_DATASET == asdict(probe.DatasetSpec())
    assert baseline.Baseline("secrecon-perf-unit", options()).guide_sized()
    assert not baseline.Baseline("secrecon-perf-unit", options(soak_seconds=60)).guide_sized()
    assert baseline.TARGETS["lease_seconds"] == 60
    assert baseline.DISPATCH_CEILING_JOBS_PER_SECOND == (
        probe.DISPATCH_BATCH_LIMIT / probe.DISPATCH_INTERVAL_SECONDS
    )


def test_every_measured_phase_is_a_valid_probe_command(monkeypatch, tmp_path):
    """The host harness and the stack probe must agree on every argument vector."""
    harness = baseline.Baseline("secrecon-perf-unit", options(report_dir=tmp_path))
    parsed = []

    def fake_phase(name, *arguments, timeout_seconds):
        assert timeout_seconds > 0
        parsed.append((name, probe.parse_args(list(arguments))))
        return {"jobs_per_second": 40.0, "digest": {"sha256": "a" * 64}}

    monkeypatch.setattr(harness, "phase", fake_phase)
    monkeypatch.setattr(harness, "run", lambda *arguments, **kwargs: None)
    harness.measure()
    phases = [(name, args.phase) for name, args in parsed]
    assert phases == [
        ("seed", "seed"),
        ("replay-single", "replay"),
        ("replay-four", "replay"),
        ("rebuild", "rebuild"),
        ("compare", "compare"),
        ("load", "load"),
        ("recovery-1", "recovery"),
        ("recovery-2", "recovery"),
        ("recovery-3", "recovery"),
        ("soak", "soak"),
    ]
    by_name = dict(parsed)
    assert by_name["replay-single"].workers == 1
    assert by_name["replay-four"].workers == baseline.GUIDE_WORKERS
    assert by_name["compare"].labels == ["single", "four", "rebuild"]
    assert by_name["load"].rps == baseline.GUIDE_READ_RPS and by_name["load"].plans
    assert by_name["soak"].seconds == baseline.GUIDE_SOAK_SECONDS
    assert by_name["soak"].rate == 20.0  # min(50% of 40 jobs/s, 80% of dispatch ceiling)
    assert by_name["soak"].reference_digest == "a" * 64
    for _, args in parsed:
        if hasattr(args, "companies"):
            assert probe.spec_from(args) == probe.DatasetSpec()
    # Recovery must process a source of the *seeded* dataset, so it gets the spec.
    assert all(hasattr(args, "companies") for name, args in parsed if name != "compare")


def test_recorded_image_inputs_exist():
    """Tree-cleanliness provenance must cover real paths, including this harness."""
    root = Path(__file__).resolve().parents[2]
    assert all((root / path).exists() for path in baseline.IMAGE_INPUTS)
    assert "scripts" in baseline.IMAGE_INPUTS and "src" in baseline.IMAGE_INPUTS


def test_cli_refuses_development_project_before_docker_runs():
    result = subprocess.run(
        [sys.executable, "scripts/performance_baseline.py", "--project", "secrecon"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "SAFETY REFUSAL" in result.stderr
    assert "refusing unsafe Compose project 'secrecon'" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["--workers", "0"],
        ["--read-seconds", "0"],
        ["--soak-rate", "50"],
        ["--recovery-trials", "0"],
    ],
)
def test_cli_rejects_invalid_options_before_docker_runs(arguments):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/performance_baseline.py",
            "--project",
            "secrecon-perf-unit",
            *arguments,
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2


def test_payload_parser_requires_exactly_one_marker():
    assert baseline.parse_payload('noise\nPERF_JSON:{"a": 1}\n', "x") == {"a": 1}
    with pytest.raises(RuntimeError):
        baseline.parse_payload("no payload", "x")
    with pytest.raises(RuntimeError):
        baseline.parse_payload('PERF_JSON:{"a": 1}\nPERF_JSON:{"a": 2}', "x")


def test_docker_stats_parsing():
    record = baseline.parse_stats_line(
        '{"Name":"secrecon-perf-u-1a-postgres-1","CPUPerc":"12.50%",'
        '"MemUsage":"256.5MiB / 7.755GiB","PIDs":"17"}'
    )
    assert record["cpu_percent"] == 12.5
    assert record["memory_bytes"] == int(256.5 * 1024 * 1024)
    assert record["pids"] == 17
    assert baseline.parse_size("1.5GB") == 1_500_000_000
    with pytest.raises(ValueError):
        baseline.parse_size("12 parsecs")
    assert baseline.service_of("p-x-postgres-1", "p-x") == "postgres"
    assert baseline.service_of("p-x-test-run-0a1b2c3d4e5f", "p-x") == "test"
    assert baseline.service_of("p-x-object-store-1", "p-x") == "object-store"


def test_cgroup_memory_stat_parsing():
    stat = "anon 1000\nfile 5000\nactive_file 3000\nshmem 200\nunrelated 7\nbroken\n"
    assert baseline.parse_memory_stat(stat) == {
        "anon": 1000,
        "file": 5000,
        "active_file": 3000,
        "shmem": 200,
    }


def test_memory_diagnostics_are_reported_but_never_gate():
    result = soak_result()
    for sample in result["samples"]:
        sample["database_bytes"] = 1_000_000 + sample["t"] * 1000  # grows by design
    cache = [
        {
            "t": float(t),
            "phase": "soak",
            "service": "postgres",
            "memory_bytes": 200 * baseline.MIB,
            "cgroup": {"anon": 50 * baseline.MIB, "active_file": (100 + t) * baseline.MIB},
        }
        for t in range(0, 1800, 15)
    ]
    verdict = baseline.soak_verdict(result, "d" * 64, cache)
    diagnostics = verdict["details"]["diagnostics_not_gated"]
    assert not diagnostics["database_bytes"]["bounded"]
    assert not diagnostics["container.postgres.cgroup_active_file_bytes"]["bounded"]
    assert diagnostics["container.postgres.cgroup_anon_bytes"]["bounded"]
    assert verdict["status"] == "pass"  # diagnostics alone never change the verdict


def replay_result(digest="d" * 64, *, seconds=100.0, completed=True, workers=4, jobs=1200):
    return {
        "completed": completed,
        "digest": {"sha256": digest},
        "observations": 100_000,
        "unique_sources": 1100,
        "duplicate_deliveries": 100,
        "jobs": jobs,
        "workers": workers,
        "drain_seconds": seconds,
        "jobs_per_second": round(jobs / seconds, 2),
        "counts": {"facts": 100_000, "processing_runs": 1100},
        "job_states": {"succeeded": jobs},
        "dataset_fingerprint": "f" * 64,
    }


def test_replay_verdict_pass_miss_and_digest_failure():
    single = replay_result(seconds=400, workers=1)
    passing = baseline.replay_verdict(single, replay_result(), {"match": True})
    assert passing["status"] == "pass"
    slow = baseline.replay_verdict(single, replay_result(seconds=700), {"match": True})
    assert slow["status"] == "miss" and slow["correct"]
    mismatch = baseline.replay_verdict(single, replay_result("e" * 64), {"match": False})
    assert mismatch["status"] == "fail"
    rebuild = {"digest": {"sha256": "e" * 64}}
    assert baseline.replay_verdict(single, replay_result(), None, rebuild)["status"] == "fail"
    lost = replay_result()
    lost["job_states"] = {"succeeded": 1199, "dead_letter": 1}
    assert baseline.replay_verdict(single, lost, {"match": True})["status"] == "fail"
    incomplete = replay_result(completed=False)
    assert baseline.replay_verdict(single, incomplete, {"match": True})["status"] == "fail"


def load_run(p95, errors=0.0, max_page=100):
    return {
        "offered_rps": 20.0,
        "duration_seconds": 600.0,
        "requests": 12000,
        "achieved_rps": 20.0,
        "p50_ms": 5.0,
        "p95_ms": p95,
        "p99_ms": p95 * 2,
        "max_ms": p95 * 3,
        "error_rate": errors,
        "max_items_per_page": max_page,
    }


def test_read_verdict_reports_cold_and_warm_separately():
    assert baseline.read_verdict({"cold": load_run(50), "warm": load_run(20)})["status"] == "pass"
    cold_miss = baseline.read_verdict({"cold": load_run(450), "warm": load_run(20)})
    assert cold_miss["status"] == "miss"
    assert "cold p95 450" in cold_miss["result"]
    errors = baseline.read_verdict({"cold": load_run(50), "warm": load_run(20, errors=0.02)})
    assert errors["status"] == "miss"
    page = baseline.read_verdict({"cold": load_run(50, max_page=101), "warm": load_run(20)})
    assert page["status"] == "fail"


def trial(seconds, correct=True, lease=60):
    return {"recovery_seconds": seconds, "correct": correct, "lease_seconds": lease}


def test_recovery_verdict_keeps_correctness_separate_from_timing():
    assert baseline.recovery_verdict([trial(62), trial(63)])["status"] == "pass"
    assert baseline.recovery_verdict([trial(62), trial(130)])["status"] == "miss"
    assert baseline.recovery_verdict([trial(62, correct=False)])["status"] == "fail"
    assert baseline.recovery_verdict([trial(5, lease=3)])["status"] == "miss"
    assert baseline.recovery_verdict([])["status"] == "fail"


def test_growth_rule_detects_monotonic_leak_but_tolerates_noise():
    flat = [(float(t), 100.0 + (t % 3)) for t in range(0, 1800, 15)]
    assert baseline.growth(flat, 120, 1801, 2)["bounded"]
    leak = [(float(t), 100.0 + t) for t in range(0, 1800, 15)]
    result = baseline.growth(leak, 120, 1801, 2)
    assert not result["bounded"]
    assert result["slope_per_hour"] == pytest.approx(3600, rel=0.01)
    assert not baseline.growth(flat[:2], 0, 1801, 2)["bounded"]  # too few samples


def soak_result(worker_rss=lambda t: 80 * baseline.MIB, open_jobs=lambda t: 3, digests=None):
    times = range(0, 1800, 15)
    worker_rows = [
        {
            "t": t,
            "rss_bytes": worker_rss(t),
            "threads": 4,
            "pool_checked_out": 1,
            "telemetry": {"traces": {"queued": 256, "capacity": 256, "dropped": t, "failed": 1}},
        }
        for t in times
    ]
    worker_rows.append({**worker_rows[-1], "final": True, "peak_rss_bytes": 90 * baseline.MIB})
    return {
        "seconds": 1800,
        "offered_rate_jobs_per_second": 5.0,
        "enqueued": 9000,
        "job_states": {"succeeded": 9000},
        "final": {"open_jobs": 0},
        "worker_exit_codes": [0, 0, 0, 0],
        "cycle_digests": digests if digests is not None else {"perf-soak-c0": "d" * 64},
        "cycles": [{"enqueued": 1100, "processing_runs": 1100}],
        "throughput_jobs_per_second": 5.0,
        "drain_after_load_seconds": 1.2,
        "samples": [
            {
                "t": t,
                "open_jobs": open_jobs(t),
                "outbox_unsent": 2,
                "redis_stream_length": 1,
                "redis_pending": 1,
                "redis_used_memory_bytes": 2 * baseline.MIB,
                "pg_connections": 9,
                "dispatcher_rss_bytes": 70 * baseline.MIB,
            }
            for t in times
        ],
        "worker_samples": {f"perf-soak-{i}": worker_rows for i in range(4)},
    }


def test_soak_verdict_bounded_leak_and_correctness():
    ok = baseline.soak_verdict(soak_result(), "d" * 64)
    assert ok["status"] == "pass", ok["details"]["checks"]
    assert ok["details"]["telemetry_capacity_respected"]
    leak = baseline.soak_verdict(
        soak_result(worker_rss=lambda t: (80 + t / 10) * baseline.MIB), "d" * 64
    )
    assert leak["status"] == "miss"
    backlog = baseline.soak_verdict(soak_result(open_jobs=lambda t: t), "d" * 64)
    assert backlog["status"] == "miss"
    wrong = baseline.soak_verdict(soak_result(digests={"perf-soak-c0": "e" * 64}), "d" * 64)
    assert wrong["status"] == "fail"


def with_telemetry(result, queued):
    """Rewrite every worker's trace-queue depth as ``queued(t)``."""
    for owner, rows in result["worker_samples"].items():
        result["worker_samples"][owner] = [
            {
                **row,
                "telemetry": {"traces": {**row["telemetry"]["traces"], "queued": queued(row["t"])}},
            }
            for row in rows
        ]
    return result


def test_soak_telemetry_queue_filling_to_capacity_is_bounded_not_a_leak():
    # Observed in the first real smoke run: against the unresponsive collector the
    # fixed-size export queue fills from ~150 to its 256 capacity, then drops.
    filling = with_telemetry(soak_result(), lambda t: min(256, 100 + t // 5))
    result = baseline.soak_verdict(filling, "d" * 64)
    check = result["details"]["checks"]["perf-soak-0.telemetry_traces_queued"]
    assert check["rule"] == "fixed capacity" and check["bounded"] and check["max"] == 256
    assert result["status"] == "pass"
    overflow = with_telemetry(soak_result(), lambda t: 257)
    result = baseline.soak_verdict(overflow, "d" * 64)
    assert not result["details"]["telemetry_capacity_respected"]
    assert result["status"] == "miss"


def test_soak_verdict_includes_container_memory_series():
    samples = [
        {"t": float(t), "phase": "soak", "service": "postgres", "memory_bytes": (200 + t) * 2**20}
        for t in range(0, 1800, 15)
    ]
    result = baseline.soak_verdict(soak_result(), "d" * 64, samples)
    assert not result["details"]["checks"]["container.postgres.memory_bytes"]["bounded"]
    assert result["status"] == "miss"


def test_overall_status_distinguishes_failure_from_target_miss():
    passed = {"correct": True, "target_met": True}
    missed = {"correct": True, "target_met": False}
    failed = {"correct": False, "target_met": True}
    assert baseline.overall_status([passed], None) == 0
    assert baseline.overall_status([passed, missed], None) == 3
    assert baseline.overall_status([missed, failed], None) == 1
    assert baseline.overall_status([passed], "RuntimeError: boom") == 1
    lines = baseline.summary_lines(
        [
            {
                "status": "miss",
                "measurement": "read_latency",
                "result": "cold p95 450 ms",
                "target": "p95 < 300 ms",
            }
        ]
    )
    assert lines[0].startswith("TARGET MISSED: read_latency")
