"""Fixture-driven host-side failure drills for isolated Compose projects only."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_PREFIX = "secrecon-drill-"
FORBIDDEN_PROJECTS = frozenset({"secrecon", "secrecon-dev", "secrecon-release", "secrecon-test"})
PROJECT_PATTERN = re.compile(r"^secrecon-drill-[a-z0-9][a-z0-9-]{0,39}$")
TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
DEFAULT_FIXTURE = Path("tests/fixtures/failure_drills/database_outage.json")
DEFAULT_REPORT_DIR = Path(".local/failure-drills")
JSON_MARKER = "DRILL_JSON:"


class UnsafeProject(ValueError):
    """Raised before Compose mutates a non-isolated or pre-existing project."""


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    duration_seconds: float
    stdout: str


def validate_project_name(project: str) -> str:
    """Allow only an explicit drill namespace; never target normal projects."""
    normalized = project.strip().lower()
    if normalized in FORBIDDEN_PROJECTS or not PROJECT_PATTERN.fullmatch(normalized):
        raise UnsafeProject(
            f"refusing unsafe Compose project {project!r}; use {PROJECT_PREFIX}<short-unique-name>"
        )
    return normalized


def load_fixture(path: Path) -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture.get("schema_version") != 1:
        raise ValueError("failure-drill fixture schema_version must be 1")
    if fixture.get("scenario") != "database_outage_and_object_store_restart":
        raise ValueError("unsupported failure-drill scenario")
    for phase in ("before", "after"):
        if phase not in fixture.get("expected", {}):
            raise ValueError(f"fixture is missing expected.{phase}")
    return fixture


class Harness:
    def __init__(self, project: str, fixture: dict[str, Any], report_dir: Path) -> None:
        self.requested_project = validate_project_name(project)
        self.project = f"{self.requested_project}-{uuid.uuid4().hex[:10]}"
        self.fixture = fixture
        self.report_dir = report_dir
        self.key = "drill-" + uuid.uuid4().hex
        self.compose = [
            "docker",
            "compose",
            "-p",
            self.project,
            "-f",
            "compose.yaml",
            "-f",
            "compose.test.yaml",
        ]
        self.commands: list[CommandResult] = []
        self.owns_project = False
        self.environment: dict[str, Any] = {
            "python": sys.version,
        }

    def run_command(
        self, command: list[str], *, display: bool = True, timeout_seconds: float = 600
    ) -> CommandResult:
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            measured = CommandResult(command, 124, time.monotonic() - started, output)
            self.commands.append(measured)
            raise
        except OSError as exc:
            measured = CommandResult(command, 127, time.monotonic() - started, str(exc))
            self.commands.append(measured)
            raise
        measured = CommandResult(
            command=command,
            returncode=result.returncode,
            duration_seconds=time.monotonic() - started,
            stdout=result.stdout or "",
        )
        self.commands.append(measured)
        if display and measured.stdout:
            print(measured.stdout, end="" if measured.stdout.endswith("\n") else "\n")
        if measured.returncode != 0:
            raise subprocess.CalledProcessError(
                measured.returncode, measured.command, output=measured.stdout
            )
        return measured

    def run(
        self, *arguments: str, capture: bool = False, timeout_seconds: float = 600
    ) -> CommandResult:
        return self.run_command(
            [*self.compose, *arguments],
            display=not capture,
            timeout_seconds=timeout_seconds,
        )

    def assert_project_unused(self) -> None:
        resource_queries = {
            "containers": [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
            ],
            "volumes": [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
            ],
            "networks": [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
            ],
        }
        existing = {
            kind: result.stdout.split()
            for kind, command in resource_queries.items()
            if (
                result := self.run_command(command, display=False, timeout_seconds=30)
            ).stdout.strip()
        }
        if existing:
            raise UnsafeProject(
                f"refusing pre-existing Compose project {self.project!r}: "
                + json.dumps(existing, sort_keys=True)
            )
        self.owns_project = True

    def collect_environment(self) -> None:
        git_commit = self.run_command(
            ["git", "rev-parse", "HEAD"], display=False, timeout_seconds=30
        ).stdout.strip()
        docker_version = self.run_command(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            display=False,
            timeout_seconds=30,
        ).stdout.strip()
        compose_version = self.run_command(
            ["docker", "compose", "version", "--short"],
            display=False,
            timeout_seconds=30,
        ).stdout.strip()
        info = self.run_command(
            [
                "docker",
                "info",
                "--format",
                "{{.NCPU}}|{{.MemTotal}}|{{.OperatingSystem}}|{{.Architecture}}",
            ],
            display=False,
            timeout_seconds=30,
        ).stdout.strip()
        cpu_count, memory_bytes, operating_system, architecture = info.split("|", 3)
        self.environment.update(
            {
                "git_commit": git_commit,
                "docker_version": docker_version,
                "compose_version": compose_version,
                "docker_cpu_count": int(cpu_count),
                "docker_memory_bytes": int(memory_bytes),
                "docker_operating_system": operating_system,
                "docker_architecture": architecture,
            }
        )

    def probe(self, phase: str, *, capture: bool = False) -> dict[str, Any] | None:
        result = self.run(
            "run",
            "--no-deps",
            "--rm",
            "test",
            "python",
            "scripts/outage_probe.py",
            phase,
            self.key,
            capture=capture,
        )
        if not capture:
            return None
        payloads = [
            line.removeprefix(JSON_MARKER)
            for line in result.stdout.splitlines()
            if line.startswith(JSON_MARKER)
        ]
        if len(payloads) != 1:
            raise RuntimeError(
                f"expected one {JSON_MARKER} payload from {phase}, got {len(payloads)}"
            )
        value = json.loads(payloads[0])
        if not isinstance(value, dict):
            raise RuntimeError(f"invalid {phase} probe payload")
        return value

    def assert_invariants(self, phase: str, actual: dict[str, Any]) -> None:
        expected = self.fixture["expected"][phase]
        mismatches = {
            key: {"expected": value, "actual": actual.get(key)}
            for key, value in expected.items()
            if actual.get(key) != value
        }
        if mismatches:
            raise AssertionError(
                f"{phase} SQL invariants failed: {json.dumps(mismatches, sort_keys=True)}"
            )

    def assert_trace_correlation(self, snapshot: dict[str, Any]) -> None:
        correlation_id = snapshot["correlation_id"]
        if not TRACE_ID_PATTERN.fullmatch(correlation_id) or correlation_id == "0" * 32:
            raise AssertionError("durable job correlation ID is not a valid nonzero trace ID")
        attempts = snapshot["attempts"]
        if len(attempts) != snapshot["attempt_count"]:
            raise AssertionError("attempt detail count does not match SQL attempt count")
        for attempt in attempts:
            if attempt["trace_id"] != correlation_id:
                raise AssertionError("an attempt is not correlated to the durable job trace")
            span_id = attempt["span_id"] or ""
            if not SPAN_ID_PATTERN.fullmatch(span_id) or span_id == "0" * 16:
                raise AssertionError("an attempt is missing a valid nonzero span ID")
        traceparent = snapshot["trace_context"].get("traceparent", "")
        parts = traceparent.split("-")
        if len(parts) != 4 or parts[1] != correlation_id:
            raise AssertionError("persisted job trace context does not match its correlation ID")

    def execute(self) -> Path:
        started_at = datetime.now(UTC)
        failure: str | None = None
        before: dict[str, Any] | None = None
        after: dict[str, Any] | None = None
        try:
            self.assert_project_unused()
            self.collect_environment()
            self.run(
                "run",
                "--build",
                "--rm",
                "test",
                "python",
                "-c",
                "print('failure drill dependencies ready')",
                timeout_seconds=900,
            )
            self.probe("seed")
            before = self.probe("snapshot", capture=True)
            assert before is not None
            self.assert_invariants("before", before)
            self.assert_trace_correlation(before)
            self.run("stop", "postgres", timeout_seconds=120)
            self.probe("outage")
            self.run(
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "120",
                "postgres",
                timeout_seconds=180,
            )
            self.run("restart", "object-store", timeout_seconds=120)
            self.probe("recover")
            after = self.probe("snapshot", capture=True)
            assert after is not None
            self.assert_invariants("after", after)
            self.assert_trace_correlation(after)
            if before["correlation_id"] != after["correlation_id"]:
                raise AssertionError("job correlation ID changed across recovery")
        except BaseException as exc:
            failure = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            cleanup_error: str | None = None
            if self.owns_project:
                try:
                    self.run(
                        "down",
                        "--volumes",
                        "--remove-orphans",
                        timeout_seconds=120,
                    )
                except Exception as exc:
                    cleanup_error = f"{type(exc).__name__}: {exc}"
                    if failure is None:
                        failure = cleanup_error
            finished_at = datetime.now(UTC)
            report = {
                "schema_version": 1,
                "scenario": self.fixture["scenario"],
                "fixture": self.fixture,
                "environment": self.environment,
                "requested_project": self.requested_project,
                "project": self.project,
                "run_key": self.key,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
                "status": "passed" if failure is None else "failed",
                "failure": failure,
                "cleanup_error": cleanup_error,
                "before": before,
                "after": after,
                "commands": [
                    {
                        "command": item.command,
                        "returncode": item.returncode,
                        "duration_seconds": round(item.duration_seconds, 3),
                        "stdout": item.stdout,
                    }
                    for item in self.commands
                ],
            }
            self.report_dir.mkdir(parents=True, exist_ok=True)
            report_path = self.report_dir / f"{started_at:%Y%m%dT%H%M%SZ}-{self.key}.json"
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"failure drill report: {report_path}")
        if failure is not None:
            raise RuntimeError(failure)
        return report_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=PROJECT_PREFIX + uuid.uuid4().hex[:12],
        help=(
            "base name for a generated, isolated Compose project; must match "
            f"{PROJECT_PREFIX}<short-unique-name>"
        ),
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="number of consecutive clean runs required (use 3 for the M6 gate)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        project = validate_project_name(args.project)
    except UnsafeProject as exc:
        print(f"SAFETY REFUSAL: {exc}", file=sys.stderr)
        return 2
    if args.runs < 1:
        print("--runs must be at least 1", file=sys.stderr)
        return 2
    fixture = load_fixture(args.fixture)
    reports = []
    for run_number in range(1, args.runs + 1):
        print(f"failure drill run {run_number}/{args.runs}: {project}")
        try:
            reports.append(Harness(project, fixture, args.report_dir).execute())
        except Exception as exc:
            print(f"FAIL: failure drill run {run_number}/{args.runs}: {exc}", file=sys.stderr)
            return 1
    print(f"PASS: {args.runs} consecutive {fixture['name']} run(s)")
    for report in reports:
        print(f"  {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
