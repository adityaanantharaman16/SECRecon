import subprocess
import sys
import time

import pytest
from sqlalchemy import text
from test_jobs import enqueue, expire

from secrecon.jobs import store

pytestmark = [pytest.mark.integration, pytest.mark.fault]


@pytest.mark.parametrize("phase", ["before", "after"])
def test_real_process_kill_at_commit_boundary(engine, tmp_path, phase):
    job = enqueue(engine)
    signal = tmp_path / "ready"
    process = subprocess.Popen([sys.executable, "scripts/fault_worker.py", job, phase, str(signal)])
    try:
        deadline = time.monotonic() + 15
        while not signal.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Crash probe exited before its checkpoint")
            time.sleep(0.05)
        assert signal.exists()
        process.kill()
        process.wait(timeout=5)
        if phase == "before":
            expire(engine, job)
            store.sweep(engine)
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT value FROM system_state WHERE key=:id"), {"id": job}
                    )
                    is None
                )
            lease = store.claim(engine, job, "recovery", 60)
            store.finish(engine, lease, lambda connection: None)
        else:
            assert store.claim(engine, job, "recovery", 60) is None
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT value FROM system_state WHERE key=:id"), {"id": job}
                    )
                    == "committed"
                )
        assert store.inspect(engine, job)["state"] == "succeeded"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
