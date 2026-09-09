"""Run identical checks locally and in CI; integration is opt-in via environment."""

import subprocess
import sys

commands = [
    ["ruff", "check", "."],
    ["ruff", "format", "--check", "."],
    ["mypy", "src"],
    ["pytest"],
]
for command in commands:
    subprocess.run([sys.executable, "-m", *command], check=True)
