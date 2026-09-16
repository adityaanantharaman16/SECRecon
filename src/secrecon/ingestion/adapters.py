"""SEC v1 adapter: strict required structure, tolerant optional fields."""

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from secrecon.domain.types import cik_text, decimal_text, fingerprint
from secrecon.storage.archive import Manifest

PARSER_VERSION = "sec-json-v1"
PARSER_VERSIONS = frozenset({PARSER_VERSION, "sec-json-v2"})
FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A"})
ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")


class SchemaError(ValueError):
    def __init__(self, message: str, *, path: str = "$", code: str = "invalid_structure") -> None:
        super().__init__(message)
        self.diagnostic = {
            "severity": "error",
            "path": path,
            "code": code,
            "message": message[:1000],
        }


@dataclass(frozen=True)
class ParsedSource:
    company: dict[str, Any]
    filings: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    historical_pages: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    parser_version: str = PARSER_VERSION


def accession_text(value: Any) -> str:
    if not isinstance(value, str) or not ACCESSION.fullmatch(value):
        raise SchemaError("Invalid accession")
    return value


def valid_date(value: Any, path: str, optional: bool = False) -> str | None:
    if optional and not value:
        return None
    if not isinstance(value, str):
        raise SchemaError(f"{path}: expected date string")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise SchemaError(f"{path}: invalid ISO date") from exc


def parse(manifest: Manifest, body: bytes, version: str = PARSER_VERSION) -> ParsedSource:
    if version not in PARSER_VERSIONS:
        raise ValueError("Unsupported parser version")
    if not manifest.complete or not 200 <= manifest.status < 300:
        raise SchemaError("Response is incomplete or unsuccessful")
    if manifest.kind == "document":
        return ParsedSource(company={"cik": manifest.cik}, parser_version=version)
    try:
        payload = json.loads(body, parse_float=Decimal, parse_int=Decimal)
        if not isinstance(payload, dict):
            raise SchemaError("$: expected object")
        if "cik" in payload:
            raw_cik = payload["cik"]
            if isinstance(raw_cik, bool) or not isinstance(raw_cik, (str, Decimal)):
                raise SchemaError("$.cik: invalid identity type")
            if isinstance(raw_cik, Decimal) and raw_cik != raw_cik.to_integral_value():
                raise SchemaError("$.cik: expected an integer identity")
            if cik_text(str(int(raw_cik))) != manifest.cik:
                raise SchemaError("$.cik: source identity mismatch")
        parsed = (
            parse_facts(payload, manifest, version)
            if manifest.kind == "facts"
            else parse_submissions(payload, manifest)
        )
        if manifest.kind == "facts":
            for key in sorted(payload.keys() - {"cik", "entityName", "facts"}):
                parsed.warnings.append(
                    {
                        "severity": "warning",
                        "path": "$/" + key,
                        "code": "unknown_optional_field",
                        "message": "Unrecognized optional field preserved in raw source",
                    }
                )
        return replace(parsed, parser_version=version)
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError) as exc:
        if isinstance(exc, SchemaError):
            raise
        raise SchemaError(f"Invalid {manifest.kind} structure: {exc}") from exc


def parse_submissions(payload: dict[str, Any], manifest: Manifest) -> ParsedSource:
    company = {
        "cik": manifest.cik,
        "name": payload.get("name", ""),
        "tickers": payload.get("tickers", []),
    }
    columns = payload["filings"]["recent"] if "filings" in payload else payload
    for key in ("accessionNumber", "filingDate", "form", "primaryDocument"):
        if not isinstance(columns.get(key), list):
            raise SchemaError(f"$.filings.recent.{key}: expected array")
    count = len(columns["accessionNumber"])
    if any(len(value) != count for value in columns.values() if isinstance(value, list)):
        raise SchemaError("$.filings.recent: unequal column lengths")
    filings = []
    for index in range(count):
        form = columns["form"][index]
        if form not in FORMS:
            continue
        accession = accession_text(columns["accessionNumber"][index])
        document = columns["primaryDocument"][index]
        if (
            not isinstance(document, str)
            or not document
            or any(x in document for x in ("/", "\\", ".."))
        ):
            raise SchemaError(f"$.primaryDocument[{index}]: invalid filename")
        filings.append(
            {
                "accession": accession,
                "cik": manifest.cik,
                "form": form,
                "filing_date": valid_date(columns["filingDate"][index], "filingDate"),
                "report_date": valid_date(
                    columns.get("reportDate", [""] * count)[index], "reportDate", True
                ),
                "accepted_at": columns.get("acceptanceDateTime", [""] * count)[index],
                "document_url": f"https://www.sec.gov/Archives/edgar/data/{int(manifest.cik)}/{accession.replace('-', '')}/{document}",
            }
        )
    pages = payload.get("filings", {}).get("files", [])
    for page in pages:
        if not isinstance(page, dict) or not re.fullmatch(
            r"CIK\d{10}-submissions-\d+\.json", page.get("name", "")
        ):
            raise SchemaError("$.filings.files: invalid historical page")
        valid_date(page["filingFrom"], "filingFrom")
        valid_date(page["filingTo"], "filingTo")
    return ParsedSource(company=company, filings=filings, historical_pages=pages)


def parse_facts(
    payload: dict[str, Any], manifest: Manifest, version: str = PARSER_VERSION
) -> ParsedSource:
    company = {"cik": manifest.cik, "name": payload.get("entityName", "")}
    if not isinstance(payload.get("facts"), dict):
        raise SchemaError("$.facts: expected object")
    facts = []
    warnings = []
    for concept, definition in payload["facts"].get("us-gaap", {}).items():
        if not isinstance(definition.get("units"), dict):
            raise SchemaError(f"$.facts.us-gaap.{concept}.units: expected object")
        for unit, entries in definition["units"].items():
            if not isinstance(entries, list):
                raise SchemaError(f"{concept}.{unit}: expected array")
            for index, entry in enumerate(entries):
                if entry.get("form") not in FORMS:
                    continue
                value = entry["val"]
                entry_path = f"$/facts/us-gaap/{concept.replace('~', '~0').replace('/', '~1')}/units/{unit.replace('~', '~0').replace('/', '~1')}/{index}"
                value_path = entry_path + "/val"
                for key in sorted(
                    entry.keys()
                    - {"start", "end", "val", "accn", "fy", "fp", "form", "filed", "frame"}
                ):
                    warnings.append(
                        {
                            "severity": "warning",
                            "path": entry_path + "/" + key.replace("~", "~0").replace("/", "~1"),
                            "code": "unknown_optional_field",
                            "message": "Unrecognized optional observation field preserved in raw source",
                        }
                    )
                if (
                    version == "sec-json-v2"
                    and isinstance(value, str)
                    and re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", value)
                ):
                    value = Decimal(value)
                if isinstance(value, bool) or not isinstance(value, Decimal):
                    raise SchemaError(
                        f"{concept}.{unit}[{index}].val: expected JSON number",
                        path=value_path,
                        code="invalid_numeric_type",
                    )
                if (
                    not value.is_finite()
                    or abs(int(value.as_tuple().exponent)) > 1000
                    or len(value.as_tuple().digits) > 1000
                ):
                    raise SchemaError(
                        "Financial number exceeds supported precision/exponent bounds",
                        path=value_path,
                        code="numeric_range",
                    )
                start = valid_date(entry.get("start"), "start", True)
                end = valid_date(entry["end"], "end")
                if start and end and start > end:
                    raise SchemaError("start is after end")
                fact = {
                    "cik": manifest.cik,
                    "accession": accession_text(entry["accn"]),
                    "taxonomy": "us-gaap",
                    "concept": concept,
                    "unit": unit,
                    "period_kind": "duration" if start else "instant",
                    "start_date": start,
                    "end_date": end,
                    "value": decimal_text(value),
                    "form": entry["form"],
                    "filed": valid_date(entry["filed"], "filed"),
                    "fy": str(entry.get("fy", "")),
                    "fp": entry.get("fp", ""),
                    "frame": entry.get("frame"),
                }
                fact["fingerprint"] = fingerprint(fact)
                fact["locator"] = (
                    f"/facts/us-gaap/{concept.replace('~', '~0').replace('/', '~1')}/units/{unit.replace('~', '~0').replace('/', '~1')}/{index}"
                )
                facts.append(fact)
    return ParsedSource(company=company, facts=facts, warnings=warnings)
