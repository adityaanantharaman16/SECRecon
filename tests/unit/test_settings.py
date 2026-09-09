import pytest
from pydantic import ValidationError

from secrecon.config import Settings


def config(**overrides: object) -> Settings:
    values = dict(
        database_url="postgresql+psycopg://user:secret@localhost/test",
        redis_url="redis://localhost:6379/0",
        s3_endpoint="http://localhost:8333",
        s3_access_key="test",
        s3_secret_key="test",
        sec_mode="offline",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_invalid_database_rejected() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL"):
        config(database_url="sqlite:///fake.db")


def test_live_mode_requires_contact() -> None:
    with pytest.raises(ValidationError, match="User-Agent"):
        config(sec_mode="live", sec_user_agent="")


def test_invalid_lease_rejected() -> None:
    with pytest.raises(ValidationError, match="Heartbeat"):
        config(lease_seconds=10)


def test_secrets_not_in_repr() -> None:
    assert "user:secret" not in repr(config())


def test_missing_required_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRECON_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)
