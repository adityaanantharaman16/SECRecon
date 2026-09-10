# SECRecon: current project state

Last updated: 2026-09-10.

## Current position

**Phase:** M0–M3 implementation delivered; one M1 external acceptance gate remains pending.

M0, M2 and M3 local gates pass. M1 code and its synthetic/real-service tests pass; the recorded SEC fixture gate awaits an approved contact address. M4–M7 remain planned. The full isolated suite passes 48 tests with 89.47% combined statement/branch coverage of domain and job modules. Hosted GitHub CI remains pending because no remote is configured.

The owner has specified **local for now, ideally free**. Vercel is an optional future presentation host, not a required backend dependency. Working name: SECRecon.

## Milestone tracker

| Milestone | Status | Evidence required to close |
| --- | --- | --- |
| M0: foundation | Complete | [M0 evidence](evidence/M0.md); hosted CI pending |
| M1: sources and facts | In progress: real fixture gate pending | [M1 evidence](evidence/M1.md) |
| M2: durable processing | Complete | [M2 evidence](evidence/M2.md) |
| M3: ingestion and rebuilds | Complete | [M3 evidence](evidence/M3.md) |
| M4: reconciliation | Not started | Verified amendment comparison and schema evolution |
| M5: operations | Not started | Search, protected admin actions, correlated telemetry |
| M6: failure and performance evidence | Not started | Repeatable drills and measured report |
| M7: release and handoff | Not started | Local release, restore, rollback and case study |

Allowed status values: Not started, In progress, Blocked, Complete. Link evidence when changing status; do not infer completion from time spent.

## First implementation task

Complete M1's recorded SEC fixture gate once the owner approves a contact address for the identifying User-Agent. Record a real original/amendment pair, retain exact response bytes/provenance, verify accessible facts manually, and add contract assertions. Then M4 is the next implementation milestone, if requested. Do not implement M4 as part of the existing M0–M3 request.

Start by checking Git status and Docker readiness. This workstation has Python 3.12.14 in `.venv`, Python 3.13 via `py`, and Docker Desktop 4.86.0 with the Linux engine. Docker provides about 16 GB memory. The repository is initialized on `main` with milestone commits and no remote; nothing has been published. Host `uv` was initially bootstrapped into ignored `.tools`; the Docker workflow does not depend on that host tool remaining available.

## Settled design defaults

Python 3.12, FastAPI, PostgreSQL 17, SQLAlchemy 2/Alembic, Redis Streams, SeaweedFS S3 storage, Docker Compose, pytest, GitHub Actions. One package with separate API, worker, and scheduler processes. Small server-rendered operations UI. Freeze exact compatible patches and image digests at M0.

Start with five US companies, 10-K/10-Q and amendments, two years of filings; expand to 25 companies after gates pass. Reconciliation covers supported Company Facts observations, not complete financial-statement restatements. Fetch and preserve primary filing documents separately.

## Open decisions and risks

- Five default companies: Apple, Microsoft, Alphabet, Amazon and Rivian. A usable real amendment pair and no-financial-change amendment still require recorded source verification. Synthetic fixtures are clearly identified.
- An automated approval review rejected sending the configured Git email to SEC endpoints without destination-specific authorization. A contact-address question is pending. Do not send that email or substitute an invented contact.
- SeaweedFS conditional-create and restart persistence gates pass. Local S3 credentials are administrative; immutability is application-enforced, not administrator-proof WORM.
- Repository visibility and public hosting are undecided; neither blocks local implementation.
- Eight weeks is a suggested sequence, not a completion promise. Reduce breadth before weakening correctness gates.
- Read-only API endpoints and CLI administration are implemented; richer search, authentication, UI and telemetry are M5. Load baselines and release/backup automation are M6/M7.
- Backfill date ranges bound document discovery/fetching. Full captured Company Facts responses retain all supported aggregate observations. See ADR 0002.

## Current commands and evidence

- Start: `docker compose up -d --build` after `python scripts/bootstrap.py`.
- Synthetic demo: `docker compose run --build --rm test python scripts/seed_demo.py`.
- Full gate: `docker compose -p secrecon-test -f compose.yaml -f compose.test.yaml run --build --rm test`.
- Actual dependency drill: `python scripts/service_drills.py` (only the test project).
- API: `http://localhost:8000/docs`; facts and provenance are available.
- Runbook: [local operations](runbooks/LOCAL_OPERATIONS.md).
- Architecture: [processing and replay](adr/0002-processing-and-replay.md).

## Session log

### 2026-09-10: M0–M3 implementation and local validation

- Implemented the modular Python package, migrations 0001–0004, pinned production/test images, local Compose services and GitHub Actions workflow. Added ADR 0002 and the local operations runbook.
- `docker compose -p secrecon-test -f compose.yaml -f compose.test.yaml run --build --rm test`: 48 passed; Ruff and strict mypy pass; domain/job coverage 89.47%. Evidence: M0–M3 reports and ignored local log `.local/m3-final-gate.log`.
- `python scripts/service_drills.py`: actual test database outage and S3 restart recovery passed. Dependency advisory audit passed; hosted CI has not run.
- `docker compose up -d --build` and the documented synthetic seed command pass. HTTP liveness/readiness return success (schema 0004); facts return two exact decimal assertions and source provenance. The running local stack remains offline.
- Owner demonstration: follow README to inspect a fact and its source, then rebuild into a new generation. Duplicate delivery is expected; SQL ownership and atomic commits prevent it from duplicating financial state. Replay rebuilds derived data; a database backup preserves operational history.
- M0 commit: `76826cf`; M1 implementation commit: `7a0cc39`; M2 commit: `68b2441`; M3 commit: `d1c51a7`. The real SEC fixture gate remains pending contact authorization; nothing has been published.
- A fresh local clone of `d1c51a7`, with new credentials and separate `secrecon-clean` volumes, also passes all 48 tests and 89.47% coverage. Exact commands are in M0 evidence. Verification containers are stopped after the check; the main `secrecon` demo remains running offline.
- Next concrete task: record and validate the real M1 fixtures after the owner supplies an approved SEC contact address.

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
