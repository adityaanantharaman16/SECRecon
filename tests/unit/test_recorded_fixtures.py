import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from secrecon.ingestion.adapters import parse


def test_recorder_refuses_to_overwrite_existing_capture(tmp_path):
    original = tmp_path / "manifest.json"
    original.write_text("preserved evidence")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/record_fixture.py",
            "--cik",
            "1783879",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=Path(__file__).parents[2],
        env={**os.environ, "SEC_FIXTURE_USER_AGENT": "offline-test tests@example.invalid"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Output directory is not empty" in result.stderr
    assert original.read_text() == "preserved evidence"


@pytest.mark.parametrize("cik", ["0001018724", "0001783879", "0001874178"])
def test_recorded_sources_match_identity_checksums_and_document_accessions(recorded_sources, cik):
    sources = recorded_sources(cik)
    parsed = {source.kind: parse(source, body) for source, body in sources}
    assert parsed["submissions"].company["cik"] == cik
    assert parsed["facts"].company["cik"] == cik
    assert parsed["facts"].facts
    filings = {row["accession"]: row for row in parsed["submissions"].filings}
    for source, _ in sources:
        if source.kind == "document":
            assert source.url == filings[source.accession]["document_url"]
            assert any(fact["accession"] == source.accession for fact in parsed["facts"].facts)


def test_real_amendment_assets_agree_with_independent_inline_xbrl(recorded_sources):
    sources = recorded_sources("0001783879")
    source, body = next(item for item in sources if item[0].kind == "facts")
    facts = parse(source, body).facts
    raw = json.loads(body, parse_float=Decimal)
    for accession, form in (
        ("0001783879-26-000023", "10-K"),
        ("0001783879-26-000029", "10-K/A"),
    ):
        selected = [f for f in facts if f["accession"] == accession]
        assert len(selected) == 592
        assets = next(
            f for f in selected if f["concept"] == "Assets" and f["end_date"] == "2025-12-31"
        )
        assert assets["value"] == "38137000000"
        assert assets["unit"] == "USD"
        assert assets["form"] == form
        assert assets["start_date"] is None
        pointed = raw
        for token in assets["locator"].lstrip("/").split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            pointed = pointed[int(token)] if isinstance(pointed, list) else pointed[token]
        assert pointed["accn"] == accession
        assert Decimal(pointed["val"]) == Decimal(assets["value"])
        document = next(b for m, b in sources if m.accession == accession).decode()
        # Fixture-specific independent check of the document's unit, instant and scale.
        tag = re.search(
            r'<ix:nonFraction\b(?=[^>]*name="us-gaap:Assets")(?=[^>]*contextRef="c-6")[^>]*>([^<]+)</ix:nonFraction>',
            document,
        )
        assert tag is not None
        assert 'unitRef="usd"' in tag.group(0) and 'scale="6"' in tag.group(0)
        assert Decimal(tag.group(1).replace(",", "")) * Decimal(10) ** 6 == Decimal(assets["value"])
        context = re.search(r'<xbrli:context id="c-6">(.*?)</xbrli:context>', document)
        assert context is not None and "<xbrli:instant>2025-12-31</xbrli:instant>" in context.group(
            1
        )
        assert '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure>' in document
