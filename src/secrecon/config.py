from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SECRECON_", env_file=".env", extra="ignore")
    database_url: SecretStr
    redis_url: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: SecretStr
    s3_bucket: str = "secrecon-raw"
    sec_mode: Literal["offline", "live"] = "offline"
    sec_user_agent: str = ""
    sec_requests_per_second: float = Field(default=2, gt=0, le=2)
    max_response_bytes: int = Field(default=50_000_000, gt=0)
    lease_seconds: int = Field(default=60, ge=3)
    heartbeat_seconds: int = Field(default=15, ge=1)

    @model_validator(mode="after")
    def validate_runtime(self) -> "Settings":
        if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("PostgreSQL with psycopg is required")
        if self.sec_mode == "live" and "@" not in self.sec_user_agent:
            raise ValueError("Live SEC access requires an identifying contact User-Agent")
        if self.heartbeat_seconds >= self.lease_seconds / 2:
            raise ValueError("Heartbeat must be shorter than half the lease")
        return self
