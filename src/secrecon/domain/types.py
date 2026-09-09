"""Canonical domain values. Database-generated IDs never define financial identity."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("Non-finite financial value")
    if value == 0:
        return "0"
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else str(value)


def cik_text(value: str | int) -> str:
    text = str(value)
    if not text.isdigit() or not 1 <= len(text) <= 10:
        raise ValueError("CIK must have 1-10 digits")
    return text.zfill(10)
