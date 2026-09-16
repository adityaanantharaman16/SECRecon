from copy import deepcopy
from decimal import Decimal, localcontext

import pytest

from secrecon.domain.reconciliation import candidates, compare


def fact(value="10", **extra):
    return {
        "cik": "0001234567",
        "taxonomy": "us-gaap",
        "concept": "Assets",
        "unit": "USD",
        "period_kind": "instant",
        "start_date": None,
        "end_date": "2024-12-31",
        "value": value,
        **extra,
    }


def test_comparison_statuses_and_exact_decimal_delta():
    before = "1234567890123456789012345678901234567890.123456789"
    after = "1234567890123456789012345678901234567891.123456790"
    row = compare([fact(before)], [fact(after)])[0]
    assert row["status"] == "changed" and row["delta"] == "1.000000001"
    assert compare([fact()], [fact("10.00", frame="CY2024Q4I")])[0]["status"] == "unchanged"
    assert compare([fact()], [])[0]["status"] == "only_in_original"
    assert compare([], [fact()])[0]["status"] == "only_in_amendment"
    assert compare([fact(), fact("11")], [fact()])[0]["status"] == "ambiguous"
    assert compare([fact(), fact("11")], [])[0]["delta"] is None


@pytest.mark.parametrize(
    "difference",
    [
        {"unit": "EUR"},
        {"end_date": "2023-12-31"},
        {"concept": "Liabilities"},
        {"period_kind": "duration", "start_date": "2024-01-01"},
    ],
)
def test_units_periods_and_concepts_are_never_silently_matched(difference):
    rows = compare([fact()], [fact("11", **difference)])
    assert {row["status"] for row in rows} == {"only_in_original", "only_in_amendment"}
    assert all(row["delta"] is None for row in rows)


def test_duplicate_qualifiers_are_not_conflicting_values():
    rows = compare([fact(), fact(frame="CY2024Q4I")], [fact("12")])
    assert rows[0]["status"] == "changed"
    assert Decimal(rows[0]["delta"]) == 2
    assert len(rows[0]["original"]) == 2


def test_delta_is_exact_across_extremely_different_scales():
    before, after = "0." + "0" * 99 + "1", "1" + "0" * 100
    delta = compare([fact(before)], [fact(after)])[0]["delta"]
    with localcontext() as context:
        context.prec = 250
        assert Decimal(delta) == Decimal(after) - Decimal(before)


def test_candidate_ambiguity_missing_period_and_same_day_acceptance():
    original = {
        "cik": "0001234567",
        "form": "10-K",
        "accession": "original",
        "report_date": "2024-12-31",
        "filing_date": "2025-02-01",
        "accepted_at": "2025-02-01T10:00:00Z",
    }
    amendment = {
        **original,
        "form": "10-K/A",
        "accession": "amendment",
        "accepted_at": "2025-02-01T11:00:00Z",
    }
    assert candidates(amendment, [original])["status"] == "unique"
    assert (
        candidates(amendment, [original, {**original, "accession": "another"}])["status"]
        == "ambiguous"
    )
    assert candidates({**amendment, "report_date": None}, [original])["status"] == "unresolved"
    assert candidates({**amendment, "accepted_at": ""}, [original])["status"] == "unresolved"
    assert candidates(amendment, [{**original, "cik": "0007654321"}])["status"] == "unresolved"
    saved = deepcopy(original)
    candidates(amendment, [original])
    assert original == saved
