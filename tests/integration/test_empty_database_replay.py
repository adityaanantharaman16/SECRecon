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
from secrecon.db.projections import process_source, register_source
from secrecon.db.session import make_engine
from secrecon.orchestration.replay import digest, rebuild

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("starting_revision", ["head", "0004", "0005"])
def test_archive_reconstructs_empty_database(engine, archive, generation, starting_revision):
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
            [sys.executable, "-m", "alembic", "upgrade", starting_revision],
            env=environment,
            check=True,
        )
        target = make_engine(settings.model_copy(update={"database_url": SecretStr(target_url)}))
        if starting_revision in {"0004", "0005"}:
            with target.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO generations(name,parser_version) VALUES ('legacy-replay','sec-json-v1')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO replays(generation,parser_version,event_ids,state) VALUES ('legacy-replay','sec-json-v1','[]','ready')"
                    )
                )
                register_source(connection, source)
                connection.execute(
                    text("""
                    INSERT INTO facts VALUES ('live','legacy','0001234567','0001234567-25-000001','Assets',123.45,'{"value":"123.45"}')
                """)
                )
                connection.execute(
                    text(
                        "INSERT INTO quarantine_records(generation,event_id,parser_version,reason) VALUES ('live',:id,'sec-json-v1','legacy rejection')"
                    ),
                    {"id": source.event_id},
                )
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True
            )
            with target.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT comparison_version FROM replays WHERE generation='legacy-replay'"
                        )
                    )
                    == "legacy-no-reconciliation"
                )
                assert (
                    str(
                        connection.scalar(
                            text("SELECT value FROM facts WHERE fingerprint='legacy'")
                        )
                    )
                    == "123.45"
                )
                assert (
                    connection.scalar(
                        text("SELECT diagnostics FROM quarantine_records WHERE generation='live'")
                    )
                    == []
                )
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
