import boto3
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import text

from secrecon.api.app import create_app
from secrecon.config import Settings
from secrecon.db.session import make_engine

pytestmark = pytest.mark.integration


def test_real_dependencies_and_readiness() -> None:
    settings = Settings()
    engine = make_engine(settings)
    with engine.connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version"))
    assert Redis.from_url(settings.redis_url).ping()
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name="us-east-1",
    )
    assert "Buckets" in s3.list_buckets()
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
    engine.dispose()
