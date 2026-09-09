import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from secrecon.domain.types import decimal_text, fingerprint
from secrecon.ingestion.adapters import SchemaError, parse
from secrecon.storage.archive import Manifest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "synthetic"


def manifest(kind: str) -> Manifest:
    return Manifest(
        kind=kind,
        url="https://data.sec.gov/test",
        cik="0001234567",
        requested_at="2025-01-01T00:00:00+00:00",
        fetched_at="2025-01-01T00:00:00+00:00",
        status=200,
        headers={},
        sha256="unused",
        byte_length=0,
        blob_key="unused",
        correlation_id="test",
    )


def test_exact_decimal_and_different_accessions() -> None:
    result = parse(manifest("facts"), (FIXTURES / "facts.json").read_bytes())
    assert result.facts[0]["value"] == "12345678901234567890.1234"
    assert result.facts[0]["fingerprint"] != result.facts[1]["fingerprint"]


def test_column_alignment_is_not_silently_truncated() -> None:
    payload = json.loads((FIXTURES / "submissions.json").read_bytes())
    payload["filings"]["recent"]["form"].pop()
    with pytest.raises(SchemaError, match="unequal"):
        parse(manifest("submissions"), json.dumps(payload).encode())


def test_bad_financial_type_fails_entire_source() -> None:
    body = (FIXTURES / "facts.json").read_bytes().replace(b"12345678901234567891.1234", b'"wrong"')
    with pytest.raises(SchemaError, match="JSON number"):
        parse(manifest("facts"), body)


@given(st.decimals(allow_nan=False, allow_infinity=False, places=5))
def test_decimal_roundtrip(value: object) -> None:
    from decimal import Decimal

    assert Decimal(decimal_text(value)) == value


def test_fingerprint_is_order_independent() -> None:
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
