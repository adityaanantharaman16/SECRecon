"""Pure, exact-decimal comparisons; absence never establishes deletion."""

import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any

from secrecon.domain.types import canonical, decimal_text

COMPARISON_VERSION = "accession-pair-v1"
LINK_VERSION = "same-period-v1"
KEY_FIELDS = ("cik", "taxonomy", "concept", "unit", "period_kind", "start_date", "end_date")


def comparable_key(fact: dict[str, Any]) -> dict[str, Any]:
    return {field: fact.get(field) for field in KEY_FIELDS}


def compare(
    original: list[dict[str, Any]], amendment: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    sides: list[dict[str, list[dict[str, Any]]]] = [defaultdict(list), defaultdict(list)]
    for rows, indexed in zip((original, amendment), sides, strict=True):
        for row in rows:
            indexed[canonical(comparable_key(row))].append(row)
    result = []
    for key in sorted(sides[0].keys() | sides[1].keys()):
        left, right = [sorted(side.get(key, []), key=canonical) for side in sides]
        values = [{Decimal(row["value"]) for row in rows} for rows in (left, right)]
        delta = None
        if any(len(side) > 1 for side in values):
            status = "ambiguous"
        elif not left:
            status = "only_in_amendment"
        elif not right:
            status = "only_in_original"
        else:
            before, after = next(iter(values[0])), next(iter(values[1]))
            status = "unchanged" if before == after else "changed"
            # The default Decimal context (28 digits) can silently round large deltas.
            with localcontext() as context:
                # Align both exponents, including a very large integer minus a tiny fraction.
                context.prec = (
                    max(v.adjusted() for v in (before, after))
                    - min(int(v.as_tuple().exponent) for v in (before, after))
                    + 3
                )
                delta = decimal_text(after - before)
        result.append(
            {
                "key": json.loads(key),
                "status": status,
                "delta": delta,
                "original": left,
                "amendment": right,
            }
        )
    return result


def earlier(original: dict[str, Any], amendment: dict[str, Any]) -> bool:
    if original["filing_date"] != amendment["filing_date"]:
        return bool(original["filing_date"] < amendment["filing_date"])
    try:
        before = datetime.fromisoformat(original.get("accepted_at", "").replace("Z", "+00:00"))
        after = datetime.fromisoformat(amendment.get("accepted_at", "").replace("Z", "+00:00"))
        return before.tzinfo is not None and after.tzinfo is not None and before < after
    except (ValueError, TypeError, AttributeError):
        return False


def candidates(amendment: dict[str, Any], filings: list[dict[str, Any]]) -> dict[str, Any]:
    if amendment["form"] not in {"10-K/A", "10-Q/A"}:
        raise ValueError("Target filing must be a supported amendment")
    matched = sorted(
        (
            filing
            for filing in filings
            if filing["cik"] == amendment["cik"]
            and filing["form"] == amendment["form"][:-2]
            and amendment.get("report_date")
            and filing.get("report_date") == amendment["report_date"]
            and earlier(filing, amendment)
        ),
        key=lambda row: row["accession"],
    )
    return {
        "rule_version": LINK_VERSION,
        "amendment": amendment,
        "status": "unique" if len(matched) == 1 else "ambiguous" if matched else "unresolved",
        "candidates": matched,
        "basis": "same CIK, base form, report date and earlier acceptance",
    }
