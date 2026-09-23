"""Compact, allowlisted summaries of ignored performance reports."""

import hashlib
import json

import pytest

from scripts import performance_summary as summary


def full_report():
    return {
        "schema_version": 1,
        "scenario": "m6.2_performance_baseline",
        "guide_sized": True,
        "exit_status": 0,
        "failure": None,
        "environment": {"git_commit": "a" * 40, "docker_cpu_count": 2},
        "verdicts": [{"measurement": "throughput_replay", "status": "pass", "details": {}}],
        "container_stats": [{"t": 1.0, "memory_bytes": 1}] * 500,
        "commands": [
            {
                "command": ["docker", "compose", "ps"],
                "returncode": 0,
                "duration_seconds": 0.5,
                "stdout": "very long output",
            }
        ],
        "future_field": {"never": "copied"},
        "results": {
            "replay_single": {"workers": 1, "digest": {"sha256": "d" * 64}, "extra": [1] * 99},
            "load": {
                "generation": "perf-four",
                "cold": {"p95_ms": 15.0, "raw_latencies": [1.0] * 12000},
                "warm": {"p95_ms": 14.0},
            },
            "recovery": [{"trial": 1, "recovery_seconds": 61.0, "correct": True, "log": "x"}],
            "soak": {
                "seconds": 1800,
                "samples": [{"t": 0, "database_bytes": 10}, {"t": 15, "database_bytes": 20}],
                "worker_samples": {"w": [{"rss_bytes": 1}] * 100},
                "cycles": [{"enqueued": 3, "processing_runs": 3}],
            },
        },
    }


def test_summary_is_allowlisted_and_keeps_verdicts_and_provenance():
    result = summary.summarize(full_report(), "f" * 64)
    assert result["source_report_sha256"] == "f" * 64
    assert result["verdicts"][0]["status"] == "pass"
    assert result["environment"]["git_commit"] == "a" * 40
    assert "future_field" not in result and "container_stats" not in result
    assert result["commands"] == [
        {"command": ["docker", "compose", "ps"], "returncode": 0, "duration_seconds": 0.5}
    ]
    assert "extra" not in result["results"]["replay_single"]
    assert "raw_latencies" not in result["results"]["load"]["cold"]
    assert result["results"]["recovery"] == [
        {"trial": 1, "recovery_seconds": 61.0, "correct": True}
    ]
    soak = result["results"]["soak"]
    assert "samples" not in soak and "worker_samples" not in soak
    assert soak["sample_count"] == 2 and soak["database_bytes_first_last"] == [10, 20]
    assert soak["cycles_total"] == soak["cycles_complete"] == 1


def test_summary_refuses_credential_like_content():
    with pytest.raises(ValueError, match="credential-like"):
        summary.assert_sanitized('{"url": "postgresql+psycopg://secrecon:x@postgres/db"}')
    summary.assert_sanitized('{"digest": "abc"}')


def test_cli_writes_summary_with_source_hash(tmp_path):
    source = tmp_path / "report.json"
    source.write_text(json.dumps(full_report()), encoding="utf-8")
    target = tmp_path / "out" / "summary.json"
    assert summary.main([str(source), str(target)]) == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["source_report_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert summary.main([str(source)]) == 2
