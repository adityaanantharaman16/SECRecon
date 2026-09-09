from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from secrecon.config import Settings
from secrecon.db.session import make_engine
from secrecon.storage.archive import Archive


@pytest.fixture
def engine() -> Iterator[Engine]:
    instance = make_engine(Settings())
    yield instance
    instance.dispose()


@pytest.fixture
def archive() -> Archive:
    settings = Settings().model_copy(update={"s3_bucket": "test-" + uuid4().hex})
    instance = Archive(settings)
    instance.initialize()
    return instance


@pytest.fixture
def generation(engine: Engine) -> str:
    name = "test-" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO generations(name,parser_version) VALUES (:n,'sec-json-v1')"),
            {"n": name},
        )
    return name
