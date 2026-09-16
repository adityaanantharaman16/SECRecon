"""Create-only bodies and manifests; a manifest is the archive commit marker."""

import hashlib
from collections.abc import Iterator
from typing import Literal
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, Field

from secrecon.config import Settings
from secrecon.domain.types import canonical, utcnow
from secrecon.telemetry import runtime as telemetry


class ArchiveIntegrityError(ValueError):
    pass


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal[1] = 1
    kind: Literal["submissions", "facts", "document"]
    url: str
    requested_at: str
    fetched_at: str
    status: int
    headers: dict[str, str]
    sha256: str
    byte_length: int
    blob_key: str
    cik: str
    accession: str | None = None
    complete: bool = True
    fetcher_version: str = "1"
    correlation_id: str
    content_encoding: str = "identity"
    storage_encoding: str = "identity"


class Archive:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            region_name="us-east-1",
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            config=Config(
                connect_timeout=5,
                read_timeout=15,
                retries={"max_attempts": 2, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )

    def initialize(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if str(exc.response["Error"]["Code"]) not in {"404", "NoSuchBucket"}:
                raise
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except ClientError as race:
                if race.response["Error"]["Code"] not in {
                    "BucketAlreadyExists",
                    "BucketAlreadyOwnedByYou",
                }:
                    raise

    def put_once(self, key: str, body: bytes, media_type: str) -> None:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType=media_type,
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if str(exc.response["Error"]["Code"]) not in {"412", "PreconditionFailed"}:
                raise
            if self.read(key) != body:
                raise ArchiveIntegrityError(f"Existing immutable object differs: {key}") from exc

    def read(self, key: str) -> bytes:
        result = self.client.get_object(Bucket=self.bucket, Key=key)
        with result["Body"] as stream:
            return stream.read()

    @telemetry.traced("archive.preserve")
    def preserve(
        self,
        body: bytes,
        *,
        kind: Literal["submissions", "facts", "document"],
        url: str,
        cik: str,
        status: int = 200,
        headers: dict[str, str] | None = None,
        requested_at: str | None = None,
        accession: str | None = None,
        complete: bool = True,
        correlation_id: str | None = None,
        event_id: str | None = None,
    ) -> Manifest:
        now = utcnow().isoformat()
        digest = hashlib.sha256(body).hexdigest()
        manifest = Manifest(
            event_id=event_id or str(uuid4()),
            kind=kind,
            url=url,
            cik=cik,
            requested_at=requested_at or now,
            fetched_at=now,
            status=status,
            headers=headers or {},
            sha256=digest,
            byte_length=len(body),
            blob_key=f"blobs/sha256/{digest}",
            accession=accession,
            complete=complete,
            correlation_id=correlation_id or telemetry.ids()[0],
            content_encoding=(headers or {}).get("content-encoding", "identity"),
        )
        self.put_once(
            manifest.blob_key, body, (headers or {}).get("content-type", "application/octet-stream")
        )
        self.put_once(
            f"events/{manifest.event_id}.json",
            canonical(manifest.model_dump()).encode(),
            "application/json",
        )
        from opentelemetry import trace

        trace.get_current_span().set_attributes(
            {
                "source_event_id": manifest.event_id,
                "cik": manifest.cik,
                "accession": manifest.accession or "",
                "correlation_id": manifest.correlation_id,
            }
        )
        return manifest

    def load(self, manifest: Manifest) -> bytes:
        body = self.read(manifest.blob_key)
        if hashlib.sha256(body).hexdigest() != manifest.sha256 or len(body) != manifest.byte_length:
            raise ArchiveIntegrityError(f"Checksum mismatch for event {manifest.event_id}")
        return body

    def manifests(self) -> Iterator[Manifest]:
        pages = self.client.get_paginator("list_objects_v2")
        for page in pages.paginate(Bucket=self.bucket, Prefix="events/"):
            for item in page.get("Contents", []):
                key = item["Key"]
                if key.endswith(".json"):
                    yield Manifest.model_validate_json(self.read(key))

    def get_manifest(self, event_id: str) -> Manifest:
        return Manifest.model_validate_json(self.read(f"events/{event_id}.json"))
