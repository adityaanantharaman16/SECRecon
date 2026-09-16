# ADR 0003: snapshot-bound reconciliation and versioned adapters

Accepted for M4, 2026-09-16.

## Comparison contract

An explicit pair must have the same CIK, supported base form and known report date. The original must precede the amendment by filing date, or by timezone-aware acceptance time on the same date. Automatic links are `unique`, `ambiguous` or `unresolved`; multiple plausible originals are never silently resolved by choosing the newest. An operator may compare one plausible explicit pair without changing automatic linkage. Manual link overrides and replayable control events are not implemented.

Each side is bound to one successfully processed Company Facts event. An omitted event ID selects the company's latest successfully processed facts snapshot, ordered by PostgreSQL fetch timestamp and event ID, including snapshots with zero facts for the requested accession. It does not silently fall back to an older nonempty snapshot. A supplied event must match the company, generation and source kind. Values from different snapshots are never mixed into an apparent amendment conflict.

Comparison keys are CIK, taxonomy, concept, unit, instant/duration, start date and end date. Fiscal labels and frame metadata remain in the assertions, but cannot replace actual dates. Distinct values within one side/key are ambiguous; duplicate metadata with the same numeric value is not a conflicting value. Exact Decimal deltas use a precision context large enough to align both exponents. No currency conversion or inferred deletion occurs.

Results retain source events, documents, discovery evidence, parser version and comparison version. Coverage is explicitly limited to supported entity-wide US-GAAP facts in the selected snapshots. `available` means both accessions have supported observations; it is not a claim of complete filing coverage.

## Persistence and replay

Migration 0005 adds immutable candidate-link versions, comparison runs and fact-change rows, plus small current-pointer tables. Content-derived IDs make concurrent identical requests idempotent. The existing per-company projection lock serializes input commits, automatic linkage and comparison refresh. Explicit comparisons acquire the same lock. Old runs remain accessible when later snapshots change the current result.

Canonical replay digests now include current candidate links and current automatic comparisons, with generated run/generation IDs excluded. Historical on-demand comparison runs are operational history, not reconstructed from raw source events. This keeps financial output deterministic across processing order without pretending to reproduce which comparisons an operator requested. Digests from M3 cannot be compared directly with the extended M4 digest. Rebuild or refresh links before taking a new baseline.

Replays pin both parser and comparison versions; resume rejects either mismatch. Existing M3 replay records are marked `legacy-no-reconciliation`, and cannot be promoted by the M4 code. Create a fresh replay after upgrading. Existing source/fact/quarantine rows are retained by the migration. Run `reconcile links --refresh` to populate comparison heads for pre-M4 live data.

Snapshot queries have indexes on generation/event/fingerprint and company/fetch-time. The first audit run exposed slow joins against accumulated test history; these are necessary access paths, not optional performance polish.

## Schema accommodation

`sec-json-v1` remains the default. `sec-json-v2` adds one explicit compatibility rule: financial `val` may also be a strict JSON-number-shaped string. This is a **synthetic schema-evolution demonstration**, not a claim that SEC changed this field. Both adapters pass the recorded real-source contracts. Booleans, objects, formatted currency strings and nonfinite values remain invalid. Numeric values are bounded to 1,000 digits and an absolute exponent of 1,000 to prevent pathological expansion; out-of-range inputs are quarantined, not rounded.

Unknown optional Company Facts top-level and observation fields are retained in raw bytes and recorded as warnings. Required numeric type failures have structured path/code diagnostics in quarantine. A failed source commits no partial facts. Successful processing under v2 does not delete the older v1 quarantine record. Replay into a separate v2 generation and the `generation-diff` report make coverage and assertion differences inspectable before promotion.

Workers prepare using the target generation's parser. The commit validates that version again under the active-generation lock. A promotion racing preparation triggers a retry rather than mixing parser policies in the new generation.

## Interface boundary

M4 administration is local CLI. The API adds read-only comparison and candidate routes; protected asynchronous web administration is still M5. The `demo/` directory is an independent, static product preview with fictional data and no backend connectivity. It does not substitute for M5's connected operations interface or telemetry.
