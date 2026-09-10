"""Child process for real crash gates; invoked only by tests."""

import sys
import time
from pathlib import Path

from sqlalchemy import text

from secrecon.config import Settings
from secrecon.db.session import make_engine
from secrecon.jobs import store

job_id, phase, signal_path = sys.argv[1:]
engine = make_engine(Settings())
lease = store.claim(engine, job_id, "crash-probe", 3)
assert lease is not None
if phase == "before":
    with engine.begin() as connection:
        store.owned(connection, lease)
        connection.execute(
            text("INSERT INTO system_state VALUES (:id,'uncommitted')"), {"id": job_id}
        )
        Path(signal_path).write_text("ready")
        time.sleep(60)
else:
    store.finish(
        engine,
        lease,
        lambda connection: connection.execute(
            text("INSERT INTO system_state VALUES (:id,'committed')"), {"id": job_id}
        ),
    )
    Path(signal_path).write_text("ready")
    time.sleep(60)
