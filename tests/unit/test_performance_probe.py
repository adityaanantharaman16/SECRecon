"""Pure-logic checks for the M6.2 performance dataset and statistics (no services)."""

import json
from decimal import Decimal

import pytest

from scripts import performance_probe as probe
from secrecon.ingestion.adapters import parse


def small() -> probe.DatasetSpec:
    return probe.DatasetSpec(companies=3, filings_per_company=4, facts_per_filing=7)


def test_default_dataset_matches_guide_budget_shape():
    spec = probe.DatasetSpec()
    spec.validate()
    assert spec.filings == 1_000
    assert spec.observations == 100_000
    assert spec.duplicate_deliveries == 100
    assert spec.sources == 1_100  # 1,000 filing facts sources + 100 discovery sources
    duplicates = probe.duplicate_indexes(spec)
    assert len(duplicates) == 100
    assert all(0 <= index < spec.filings for index in duplicates)


def test_deliveries_add_exactly_one_extra_delivery_per_duplicate_filing():
    spec = small()
    planned = probe.deliveries(spec)
    assert len(planned) == spec.sources + spec.duplicate_deliveries
    by_event: dict[str, list[int]] = {}
    for manifest, delivery in planned:
        by_event.setdefault(manifest.event_id, []).append(delivery)
    assert len(by_event) == spec.sources
    duplicated = [event for event, numbers in by_event.items() if numbers == [0, 1]]
    assert len(duplicated) == spec.duplicate_deliveries
    kinds = {m.event_id: m.kind for m, _ in planned}
    assert all(kinds[event] == "facts" for event in duplicated)


def test_synthetic_identities_are_outside_real_formats_and_labelled():
    spec = small()
    seen = set()
    for manifest, body in probe.manifests(spec):
        payload = json.loads(body)
        assert manifest.cik.startswith("0990000")
        assert manifest.url.startswith("https://synthetic.invalid/synthetic-perf-v1/")
        assert manifest.correlation_id == "synthetic-performance-fixture"
        name = payload.get("entityName") or payload.get("name")
        assert name.startswith("Synthetic Performance Company")
        if manifest.kind == "facts":
            assert manifest.accession and manifest.accession.startswith(manifest.cik + "-25-")
        seen.add(manifest.event_id)
    assert len(seen) == spec.sources


def test_manifests_and_fingerprint_are_deterministic_across_invocations():
    first = [(m.model_dump(), body) for m, body in probe.manifests(small())]
    second = [(m.model_dump(), body) for m, body in probe.manifests(small())]
    assert first == second
    assert probe.dataset_fingerprint(small()) == probe.dataset_fingerprint(small())
    assert probe.dataset_fingerprint(small()) != probe.dataset_fingerprint(probe.DatasetSpec())


def test_every_facts_body_parses_to_exact_decimal_unique_facts():
    spec = small()
    fingerprints = set()
    for manifest, body in probe.manifests(spec):
        if manifest.kind != "facts":
            continue
        parsed = parse(manifest, body)
        assert len(parsed.facts) == spec.facts_per_filing
        assert not parsed.warnings
        for fact in parsed.facts:
            assert fact["accession"] == manifest.accession
            # Exact JSON number tokens survive: 4 fractional digits, no float rounding.
            assert Decimal(fact["value"]) == Decimal(fact["value"]).quantize(Decimal("0.0001"))
            fingerprints.add(fact["fingerprint"])
    assert len(fingerprints) == spec.observations


def test_submissions_bodies_parse_to_every_synthetic_filing():
    spec = small()
    accessions = set()
    for manifest, body in probe.manifests(spec):
        if manifest.kind == "submissions":
            parsed = parse(manifest, body)
            assert parsed.company["name"].startswith("Synthetic Performance Company")
            assert len(parsed.filings) == spec.filings_per_company
            accessions.update(filing["accession"] for filing in parsed.filings)
    assert len(accessions) == spec.filings


def test_values_are_json_numbers_not_strings():
    manifest, body = next(m for m in probe.manifests(small()) if m[0].kind == "facts")
    assert b"__VALUE__" not in body
    entry = json.loads(body, parse_float=Decimal)["facts"]["us-gaap"]["SyntheticMetric00"]
    assert isinstance(entry["units"]["USD"][0]["val"], Decimal)


def test_offline_read_targets_match_parsed_dataset():
    spec = small()
    targets = probe.read_targets(spec, fact_filings=2)
    assert len(targets["accessions"]) == spec.filings
    assert len(targets["ciks"]) == spec.companies
    assert len(targets["facts"]) == 2 * spec.facts_per_filing
    parsed = {
        fact["fingerprint"]
        for manifest, body in probe.manifests(spec)
        if manifest.kind == "facts"
        for fact in parse(manifest, body).facts
    }
    assert set(targets["facts"]) <= parsed


@pytest.mark.parametrize(
    "spec",
    [
        probe.DatasetSpec(companies=0),
        probe.DatasetSpec(companies=1_000),
        probe.DatasetSpec(filings_per_company=1_000),
        probe.DatasetSpec(facts_per_filing=101),
        probe.DatasetSpec(duplicate_ratio=1.5),
    ],
)
def test_dataset_spec_rejects_out_of_range_shapes(spec):
    with pytest.raises(ValueError):
        spec.validate()


def test_nearest_rank_percentile():
    values = [float(v) for v in range(1, 101)]
    assert probe.percentile(values, 0.95) == 95.0
    assert probe.percentile(values, 0.50) == 50.0
    assert probe.percentile(values, 1.0) == 100.0
    assert probe.percentile([7.0], 0.95) == 7.0
    assert probe.percentile([1.0, 2.0, 3.0], 0.95) == 3.0
    with pytest.raises(ValueError):
        probe.percentile([], 0.95)
    with pytest.raises(ValueError):
        probe.percentile(values, 0)


def test_generation_label_and_run_prefix_are_restricted():
    assert probe.generation_name("four") == "perf-four"
    assert probe.run_prefix("soak-1a2b") == "perf:soak-1a2b:"
    for bad in ("", "x" * 61, "a;drop", "a b", "a%b"):
        with pytest.raises(ValueError):
            probe.generation_name(bad)
    for bad in ("", "a%", "a_b"):
        with pytest.raises(ValueError):
            probe.run_prefix(bad)


def test_read_rotation_covers_every_route_within_page_budget():
    targets = probe.read_targets(small(), fact_filings=1)
    requests = [probe.read_request(targets, "perf-x", index) for index in range(40)]
    routes = [route for route, _ in requests]
    assert routes[:10] == list(probe.ROUTES)
    assert routes[10:20] == list(probe.ROUTES)
    for _, path in requests:
        assert "generation=perf-x" in path or path.startswith("/v1/sources")
        if "limit=" in path:
            assert int(path.split("limit=")[1].split("&")[0]) <= 100
    # Keys rotate between turns so the load is not one cached row.
    detail = [path for route, path in requests if route == "filing-detail"]
    assert len(set(detail)) == len(detail)


def test_page_items_counts_the_bounded_collection_per_route():
    assert probe.page_items("company-list", {"items": [1, 2, 3]}) == 3
    assert probe.page_items("filing-detail", {"sources": [1], "items": []}) == 1
    assert probe.page_items("fact-provenance", {"sources": [1, 2]}) == 2


def test_dispatcher_models_production_cadence():
    assert probe.DISPATCH_INTERVAL_SECONDS == 2.0
    assert probe.DISPATCH_BATCH_LIMIT == 100


def test_worker_and_victim_cli_arguments_parse():
    args = probe.parse_args(
        ["worker", "--stream", "perf:x", "--owner", "perf-x-0", "--samples", "/tmp/s.jsonl"]
    )
    assert (args.phase, args.stream, args.owner, args.sample_seconds) == (
        "worker",
        "perf:x",
        "perf-x-0",
        15,
    )
    soak = probe.parse_args(["soak", "--rate", "5", "--seconds", "60"])
    assert probe.spec_from(soak) == probe.DatasetSpec()
    assert soak.workers == 4
