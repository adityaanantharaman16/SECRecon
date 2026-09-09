# SECRecon: current project state

Last updated: 2026-09-09.

## Current position

**Phase:** implementation. **Next milestone:** M1, immutable sources and facts.

M0 foundation is implemented: locked tooling, API health, migration, Compose dependencies, offline unit tests, real-service integration test and CI workflow. M1–M7 remain planned.

The owner has specified **local for now, ideally free**. Vercel is an optional future presentation host, not a required backend dependency. Working name: SECRecon.

## Milestone tracker

| Milestone | Status | Evidence required to close |
| --- | --- | --- |
| M0: foundation | Complete (local gate; hosted CI pending) | [M0 evidence](evidence/M0.md) |
| M1: sources and facts | Not started | Preserved real fixture, provenance, duplicate test |
| M2: durable processing | Not started | Concurrent delivery, crash recovery, retries and DLQ |
| M3: ingestion and rebuilds | Not started | Resumable backfill and deterministic offline replay |
| M4: reconciliation | Not started | Verified amendment comparison and schema evolution |
| M5: operations | Not started | Search, protected admin actions, correlated telemetry |
| M6: failure and performance evidence | Not started | Repeatable drills and measured report |
| M7: release and handoff | Not started | Local release, restore, rollback and case study |

Allowed status values: Not started, In progress, Blocked, Complete. Link evidence when changing status; do not infer completion from time spent.

## First implementation task

Implement M0.1: Python package skeleton, locked tooling, settings validation, FastAPI health endpoint, and unit-test/lint/type-check commands. Then implement M0.2: containerized dependencies and startup checks. Follow the guide's M0 gate before M1.

Start by checking Git status, Docker daemon/Compose availability, and the chosen Python toolchain. During design, `docker.exe` was found on PATH; daemon readiness was not tested. `uv` and `python` were not found through PowerShell command discovery; installation status elsewhere is unknown. The repository had an unborn `master` branch and no commits. No branch changes, commits, or remote publication were performed during planning.

## Settled design defaults

Python 3.12, FastAPI, PostgreSQL 17, SQLAlchemy 2/Alembic, Redis Streams, SeaweedFS S3 storage, Docker Compose, pytest, GitHub Actions. One package with separate API, worker, and scheduler processes. Small server-rendered operations UI. Freeze exact compatible patches and image digests at M0.

Start with five US companies, 10-K/10-Q and amendments, two years of filings; expand to 25 companies after gates pass. Reconciliation covers supported Company Facts observations, not complete financial-statement restatements. Fetch and preserve primary filing documents separately.

## Open decisions and risks

- Choose five companies and verify one usable real amendment pair during M1. Include a no-financial-change amendment. Synthetic fixtures must be clearly identified.
- Confirm local memory/disk capacity in M0; run telemetry only when needed on constrained machines.
- Verify SeaweedFS conditional-create behavior and durability in M1. Do not claim immutability based on an S3-compatible label alone.
- Repository visibility and public hosting are undecided; neither blocks local implementation.
- Eight weeks is a suggested sequence, not a completion promise. Reduce breadth before weakening correctness gates.

## Session log

### 2026-09-09: design baseline

- Added the milestone guide, repository entry point, and agent handoff instructions.
- Verified primary documentation for SEC coverage, access constraints, Redis recovery, storage semantics, telemetry, Docker, GitHub Actions, and Vercel function duration.
- Incorporates the owner's local/free hosting preference.
- No runtime checks are claimed; there is no application yet.
- Documentation verification: local Markdown file links resolve and code fences are balanced in all four planning files. Git status shows only the new planning artifacts.
- Next: M0.1.

## Template for the next implementation session

Copy this block into the session log and fill it in:

```text
Date / milestone / slice:
What now works:
Files and architecture decisions changed:
Checks run (exact command, result, and evidence path or CI URL):
What the owner should try:
Concept to explain in plain language:
Known limitations / blocked work:
Migration or configuration changes:
Next concrete task:
Commit or PR, if created:
```
