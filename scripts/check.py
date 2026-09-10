"""Run identical checks locally and in CI; integration is opt-in via environment."""

import os
import subprocess
import sys

commands = [
    ["ruff", "check", "."],
    ["ruff", "format", "--check", "."],
    ["mypy", "src"],
]
pytest_args = ["pytest"]
if os.environ.get("SECRECON_INTEGRATION") == "1":
    pytest_args += [
        "--cov=secrecon.domain",
        "--cov=secrecon.jobs",
        "--cov-branch",
        "--cov-fail-under=80",
        "--cov-report=term-missing",
    ]
commands.append(pytest_args)
for command in commands:
    subprocess.run([sys.executable, "-m", *command], check=True)
