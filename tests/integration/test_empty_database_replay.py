"""Rebuild a physically empty PostgreSQL database from preserved sources."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from secrecon.config import Settings
from secrecon.db.projections import process_source
from secrecon.db.session import make_engine
from secrecon.orchestration.replay import digest, rebuild

pytestmark = pytest.mark.integration


def test_archive_reconstructs_empty_database(engine, archive, generation):
    for filename, kind in (("submissions.json", "submissions"), ("facts.json", "facts")):
        source = archive.preserve(
            (Path(__file__).parents[1] / "fixtures/synthetic" / filename).read_bytes(),
            kind=kind,
            url="https://data.sec.gov/test",
            cik="0001234567",
        )
        process_source(engine, archive, source, generation)
    with engine.connect() as connection:
        expected = digest(connection, generation)
    # Generated, strictly validated test name; never derived from a user's database name.
    name = "secrecon_replay_" + uuid4().hex
    assert name.startswith("secrecon_replay_") and name.replace("_", "").isalnum()
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    target = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        target_url = url.set(database=name).render_as_string(hide_password=False)
        environment = {
            **os.environ,
            "SECRECON_DATABASE_URL": target_url,
            "SECRECON_SEC_MODE": "offline",
        }
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True
        )
        target = make_engine(settings.model_copy(update={"database_url": SecretStr(target_url)}))
        result = rebuild(target, archive, "restored")
        assert result == expected
        with target.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM source_events")) == 2
            assert (
                connection.scalar(text("SELECT count(*) FROM jobs")) == 0
            )  # Operational history is not invented.
    finally:
        if target is not None:
            target.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
