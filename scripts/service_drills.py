"""Host-side real dependency drill. Targets only the hard-coded secrecon-test project."""

import subprocess
import uuid

compose = [
    "docker",
    "compose",
    "-p",
    "secrecon-test",
    "-f",
    "compose.yaml",
    "-f",
    "compose.test.yaml",
]
key = "drill-" + uuid.uuid4().hex


def run(*arguments):
    subprocess.run([*compose, *arguments], check=True)


def probe(phase):
    run("run", "--no-deps", "--rm", "test", "python", "scripts/outage_probe.py", phase, key)


probe("seed")
try:
    run("stop", "postgres")
    probe("outage")
finally:
    run("up", "-d", "--wait", "postgres")
run("restart", "object-store")
probe("recover")
print("PASS: database outage and object-store restart")
