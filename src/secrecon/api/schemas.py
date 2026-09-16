from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from secrecon.domain.types import cik_text, utcnow


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComparisonRequest(RequestBody):
    original: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    amendment: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    generation: str = Field(default="active", min_length=1, max_length=80)
    original_event: str | None = Field(default=None, max_length=80)
    amendment_event: str | None = Field(default=None, max_length=80)


class BackfillRequest(RequestBody):
    ciks: list[str] = Field(min_length=1, max_length=25)
    start: date
    end: date
    max_jobs: int = Field(default=1000, ge=1, le=10000)

    @field_validator("ciks")
    @classmethod
    def normalize(cls, values: list[str]) -> list[str]:
        return sorted({cik_text(value) for value in values})

    @model_validator(mode="after")
    def bounds(self) -> "BackfillRequest":
        if (
            self.start > self.end
            or self.end > utcnow().date()
            or (self.end - self.start).days > 3653
            or self.max_jobs < len(self.ciks)
        ):
            raise ValueError("Invalid historical backfill bounds or capacity")
        return self


class ReplayRequest(RequestBody):
    parser_version: Literal["sec-json-v1", "sec-json-v2"] = "sec-json-v1"


class EmptyRequest(RequestBody):
    pass
