import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import service_drills


@pytest.mark.parametrize(
    "project",
    [
        "secrecon",
        "secrecon-dev",
        "secrecon-release",
        "secrecon-test",
        "secrecon_m6",
        "other-drill-safe-looking",
        "secrecon-drill-",
        "secrecon-drill-" + "x" * 41,
    ],
)
def test_failure_harness_refuses_non_drill_projects(project):
    with pytest.raises(service_drills.UnsafeProject):
        service_drills.validate_project_name(project)


def test_failure_harness_accepts_only_explicit_drill_namespace():
    assert (
        service_drills.validate_project_name("secrecon-drill-m6-run-01")
        == "secrecon-drill-m6-run-01"
    )


def test_harness_pins_generated_project_and_test_compose_overlay(tmp_path):
    harness = service_drills.Harness(
        "secrecon-drill-unit",
        {"expected": {"before": {}, "after": {}}},
        tmp_path,
    )
    assert harness.requested_project == "secrecon-drill-unit"
    assert harness.project.startswith("secrecon-drill-unit-")
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


def test_harness_refuses_preexisting_generated_project(monkeypatch, tmp_path):
    harness = service_drills.Harness(
        "secrecon-drill-unit",
        {"expected": {"before": {}, "after": {}}},
        tmp_path,
    )
    monkeypatch.setattr(
        service_drills.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="existing\n"),
    )
    with pytest.raises(service_drills.UnsafeProject, match="pre-existing Compose project"):
        harness.assert_project_unused()
    assert not harness.owns_project


def test_command_timeout_is_recorded_before_propagation(monkeypatch, tmp_path):
    harness = service_drills.Harness(
        "secrecon-drill-unit",
        {"expected": {"before": {}, "after": {}}},
        tmp_path,
    )

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1, output="partial output")

    monkeypatch.setattr(service_drills.subprocess, "run", time_out)
    with pytest.raises(subprocess.TimeoutExpired):
        harness.run_command(["docker", "info"], timeout_seconds=1)
    assert harness.commands[-1].returncode == 124
    assert harness.commands[-1].stdout == "partial output"


def test_cli_refuses_development_project_before_compose_runs():
    result = subprocess.run(
        [sys.executable, "scripts/service_drills.py", "--project", "secrecon"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "SAFETY REFUSAL" in result.stderr
    assert "refusing unsafe Compose project 'secrecon'" in result.stderr


def test_cli_rejects_nonpositive_consecutive_run_count():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/service_drills.py",
            "--project",
            "secrecon-drill-unit",
            "--runs",
            "0",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "--runs must be at least 1" in result.stderr


def test_failure_fixture_declares_before_and_after_sql_invariants():
    path = Path("tests/fixtures/failure_drills/database_outage.json")
    fixture = service_drills.load_fixture(path)
    assert fixture["expected"]["before"]["job_state"] == "running"
    assert fixture["expected"]["after"]["job_state"] == "succeeded"
    assert fixture["expected"]["after"]["attempt_outcomes"] == [
        "lease_expired",
        "succeeded",
    ]
    assert json.loads(path.read_text())["schema_version"] == 1


def test_invariant_comparison_reports_exact_mismatch(tmp_path):
    fixture = {
        "expected": {
            "before": {"job_state": "running", "redis_pending": 1},
            "after": {},
        }
    }
    harness = service_drills.Harness("secrecon-drill-unit", fixture, tmp_path)
    with pytest.raises(AssertionError, match='"actual": 0'):
        harness.assert_invariants("before", {"job_state": "running", "redis_pending": 0})


def test_trace_correlation_requires_every_attempt_and_persisted_parent(tmp_path):
    harness = service_drills.Harness(
        "secrecon-drill-unit",
        {"expected": {"before": {}, "after": {}}},
        tmp_path,
    )
    trace_id = "1" * 32
    snapshot = {
        "correlation_id": trace_id,
        "attempt_count": 2,
        "attempts": [
            {"trace_id": trace_id, "span_id": "2" * 16},
            {"trace_id": trace_id, "span_id": "3" * 16},
        ],
        "trace_context": {"traceparent": f"00-{trace_id}-{'4' * 16}-01"},
    }
    harness.assert_trace_correlation(snapshot)
    snapshot["attempts"][1]["trace_id"] = None
    with pytest.raises(AssertionError, match="an attempt is not correlated"):
        harness.assert_trace_correlation(snapshot)
