import gzip
import hashlib
import json
import os
from pathlib import Path

import pytest

from secrecon.storage.archive import Manifest


@pytest.fixture
def recorded_sources():
    """Load exact decoded HTTP bodies; gzip is only repository storage compression."""

    def load(cik):
        directory = Path(__file__).parent / "fixtures" / "recorded" / cik
        sources = []
        for entry in json.loads((directory / "manifest.json").read_text()):
            body = gzip.decompress((directory / entry["file"]).read_bytes())
            assert hashlib.sha256(body).hexdigest() == entry["sha256"]
            assert len(body) == entry["byte_length"]
            source = Manifest(
                kind=entry["kind"],
                cik=entry["cik"],
                accession=entry["accession"],
                url=entry["url"],
                requested_at=entry["fetched_at"],
                fetched_at=entry["fetched_at"],
                status=200,
                headers={},
                sha256=entry["sha256"],
                byte_length=entry["byte_length"],
                blob_key="blobs/sha256/" + entry["sha256"],
                correlation_id="recorded-fixture",
            )
            sources.append((source, body))
        return sources

    return load


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.getenv("SECRECON_INTEGRATION") != "1":
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(
                    pytest.mark.skip(reason="Set SECRECON_INTEGRATION=1 for real services")
                )
