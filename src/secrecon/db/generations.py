"""Read-only comparison of financial assertions and source coverage across generations."""

from typing import Any

from sqlalchemy import Connection, text

from secrecon.domain.types import canonical


def generation_report(connection: Connection, original: str, target: str) -> dict[str, Any]:
    versions = {
        str(row[0]): str(row[1])
        for row in connection.execute(
            text("SELECT name,parser_version FROM generations WHERE name=ANY(:names)"),
            {"names": [original, target]},
        ).all()
    }
    if original not in versions or target not in versions:
        raise ValueError("Both generations must exist")
    rows = []
    inventories = []
    for generation in (original, target):
        rows.append(
            {
                canonical(row): row
                for row in connection.execute(
                    text("SELECT data FROM facts WHERE generation=:g"), {"g": generation}
                ).scalars()
            }
        )
        inventories.append(
            set(
                connection.execute(
                    text(
                        "SELECT event_id FROM processing_runs WHERE generation=:g AND status='succeeded'"
                    ),
                    {"g": generation},
                ).scalars()
            )
        )
    return {
        "original": original,
        "target": target,
        "parser_versions": versions,
        "same_successful_source_inventory": inventories[0] == inventories[1],
        "sources_only_in_original": sorted(inventories[0] - inventories[1]),
        "sources_only_in_target": sorted(inventories[1] - inventories[0]),
        "unchanged_assertions": len(rows[0].keys() & rows[1].keys()),
        "only_in_original": [rows[0][k] for k in sorted(rows[0].keys() - rows[1].keys())],
        "only_in_target": [rows[1][k] for k in sorted(rows[1].keys() - rows[0].keys())],
        "note": "Differences may reflect source coverage or adapter changes; not inferred legal amendments",
    }
