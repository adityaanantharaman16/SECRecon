# SECRecon: current project state

Last updated: 2026-09-16.

## Current position

**Phase:** M0–M4 complete; M5 is next if requested.

M0–M4 local gates pass, including real recorded-source reconciliation and schema evolution. M5–M7 remain planned. The full isolated suite passes **79 tests** with **90.75%** combined statement/branch coverage of domain and job modules. The private GitHub repository is [adityaanantharaman16/SECRecon](https://github.com/adityaanantharaman16/SECRecon). The hosted CI capacity failure is fixed and verified; see [CI evidence](evidence/CI.md) and GitHub Actions for the latest commit's result.

The owner has specified **local for now, ideally free**. Vercel is an optional future presentation host, not a required backend dependency. Working name: SECRecon.

## Milestone tracker

| Milestone | Status | Evidence required to close |
| --- | --- | --- |
| M0: foundation | Complete | [M0 evidence](evidence/M0.md), [hosted CI](evidence/CI.md) |
| M1: sources and facts | Complete | [M1 evidence](evidence/M1.md) |
| M2: durable processing | Complete | [M2 evidence](evidence/M2.md) |
| M3: ingestion and rebuilds | Complete | [M3 evidence](evidence/M3.md) |
| M4: reconciliation | Complete | [M4 evidence](evidence/M4.md); 79 tests pass |
| M5: operations | Not started | Search, protected admin actions, correlated telemetry |
| M6: failure and performance evidence | Not started | Repeatable drills and measured report |
| M7: release and handoff | Not started | Local release, restore, rollback and case study |

Allowed status values: Not started, In progress, Blocked, Complete. Link evidence when changing status; do not infer completion from time spent.

## First implementation task

M5.1 is next, if requested: implement indexed query filters, deterministic cursor pagination and API error contracts with integration coverage. Read ADR 0003 before changing comparison semantics. M4 comparisons, candidate linkage and schema replay already exist; do not rebuild them. The static `demo/` is a fictional product concept, not a connected UI or completion of M5.

Start by checking Git status and Docker readiness. This workstation has Python 3.12.14 in `.venv`, Python 3.13 via `py`, and Docker Desktop 4.86.0 with the Linux engine. Docker provides about 16 GB memory. The repository uses `main` with milestone commits; `origin` is `https://github.com/adityaanantharaman16/SECRecon.git`. Branch names must exclude `codex`; use descriptive prefixes such as `feat/`, `fix/`, `test/` or `docs/`. Host `uv` was initially bootstrapped into ignored `.tools`; the Docker workflow does not depend on that host tool remaining available.

## Settled design defaults

Python 3.12, FastAPI, PostgreSQL 17, SQLAlchemy 2/Alembic, Redis Streams, SeaweedFS S3 storage, Docker Compose, pytest, GitHub Actions. One package with separate API, worker, and scheduler processes. Small server-rendered operations UI. Freeze exact compatible patches and image digests at M0.

Start with five US companies, 10-K/10-Q and amendments, two years of filings; expand to 25 companies after gates pass. Reconciliation covers supported Company Facts observations, not complete financial-statement restatements. Fetch and preserve primary filing documents separately.

## Open decisions and risks

- Five default companies: Apple, Microsoft, Alphabet, Amazon and Rivian. Recorded fixtures cover Amazon, Rivian and a Robinhood original/amendment pair. Robinhood's amendment corrects formatting without changing financial results; it does not change the default watchlist. Synthetic changed-value fixtures remain clearly identified.
- The owner explicitly approved the configured Git email as the SEC contact on 2026-09-16, resolving the earlier automatic-review blocker. It is saved in ignored `.env`; it is absent from committed fixture contents and documentation. The application remains configured offline; the bounded fixture recorder made ten authorized SEC requests.
- SeaweedFS conditional-create and restart persistence gates pass. Local S3 credentials are administrative; immutability is application-enforced, not administrator-proof WORM.
- The owner authorized private GitHub publication on 2026-09-16. Public visibility and application hosting are not requested; the required runtime remains local and free.
- Eight weeks is a suggested sequence, not a completion promise. Reduce breadth before weakening correctness gates.
- Read-only API endpoints and CLI administration are implemented; richer search, authentication, UI and telemetry are M5. Load baselines and release/backup automation are M6/M7.
- Backfill date ranges bound document discovery/fetching. Full captured Company Facts responses retain all supported aggregate observations. See ADR 0002.

## Current commands and evidence

- Start: `docker compose up -d --build` after `python scripts/bootstrap.py`.
- Synthetic demo: `docker compose run --build --rm test python scripts/seed_demo.py`.
- Full gate: `docker compose -p secrecon-test -f compose.yaml -f compose.test.yaml run --build --rm test`.
- Actual dependency drill: `python scripts/service_drills.py` (only the test project).
- API: `http://localhost:8000/docs`; facts and provenance are available.
- Reconciliation: `docker compose exec api secrecon reconcile links --refresh` for existing pre-M4 data, then `GET /v1/amendments` and `GET /v1/reconciliations/{id}`. New projections update comparisons automatically.
- Frontend concept: `py -3.13 -m http.server 8010 --bind 127.0.0.1 --directory demo`, then open `http://127.0.0.1:8010` and select **Take a walkthrough**. See [demo guide](../demo/README.md).
- Runbook: [local operations](runbooks/LOCAL_OPERATIONS.md).
- Architecture: [processing and replay](adr/0002-processing-and-replay.md).
- M4 contracts: [reconciliation and schema evolution](adr/0003-reconciliation-and-schema-evolution.md).

## Session log

### 2026-09-16: CI repair, M4 completion and product demo

- Fixed hosted CI's SeaweedFS volume-slot exhaustion using explicit test-only volume capacity. Commit `8610bf7`; [hosted repair run passed](https://github.com/adityaanantharaman16/SECRecon/actions/runs/35098280173). Tests and coverage thresholds were not weakened.
- Implemented immutable snapshot-bound comparisons, conservative amendment candidates, exact decimal deltas, source/document evidence, structured schema diagnostics, parser-versioned replay and generation reports. Original-only observations express missing coverage, never deletion. Migration 0005 includes data-preserving upgrade coverage from 0004.
- Audit fixes: indexed snapshot queries; rejected stale parser commits for retry; covered late source arrival and concurrent comparisons; fixed extreme Decimal scale precision; pinned comparison versions and explicitly marked legacy replay records. Old M3 replay digests need a fresh M4 baseline; legacy runs cannot be resumed or promoted. See ADR 0003.
- Full gate: `docker compose -p secrecon-m4-final -f compose.yaml -f compose.test.yaml run --build --rm test` — **79 passed**, Ruff/format/strict mypy passed, **90.75%** domain/job coverage, 95.36 seconds, two existing upstream deprecation warnings. Ignored log: `.local/m4-final-gate.log`. Targeted migration/rule audit passed 10 tests before the final full gate.
- Runtime upgrade: `docker compose up -d --build` succeeded; `/health/ready` reports schema **0005**. `docker compose exec api secrecon reconcile links --refresh` and `reconcile create 0001234567-25-000001 0001234567-25-000002` succeeded for existing synthetic seed data. The read API exposes the resulting immutable comparison. Live SEC access stays off.
- Added the standalone `demo/` concept: six screens, three fictional comparison scenarios, provenance drawer, JSON export, search/filtering, simulated linked redrive, replay and a seven-step tour. Browser checks covered desktop and 390-pixel layouts, search, coverage messaging, job history preservation, replay and all tour steps; no console errors observed. JavaScript syntax checks passed. No backend integration or M5 telemetry/authentication is claimed.
- Owner demonstration: compare the original and amendment, inspect a value's source snapshot, and explain why missing coverage is not deletion. Then simulate recovery in the frontend and distinguish financial replay from restoring operational history through database backups.
- Work branch: `feat/m4-reconciliation`; M4 commit `5614d92`, demo commit `72bc8b9`, [review and hosted gates in PR #1](https://github.com/adityaanantharaman16/SECRecon/pull/1). Required runtime remains local/free; no new SEC requests, dependencies or public hosting were introduced.
- Next concrete task: M5.1 query filters, pagination and API contracts, only when requested. Keep M5 connected UI work separate from this static demo.

### 2026-09-16: private GitHub publication and branch naming

- Created the owner-authorized private repository `adityaanantharaman16/SECRecon`, connected `origin`, and pushed the complete milestone history to `main`. GitHub's main commit was verified as `a98b2bf`, matching the initial local publication commit. Local configuration and credentials remain ignored.
- Renamed the existing feature branch to `feat/finish-m1-real-fixtures`. Updated AGENTS.md, the guide and ADR 0002 so future branch names exclude `codex`, as requested.
- Checks: clean initial working tree, no existing remote, GitHub authenticated-owner lookup, private repository creation response, ignored configuration/helper paths, and `git diff --check`. No runtime code changed, so the passing 54-test milestone gate was not repeated.
- The owner can find and clone the project on GitHub. Git preserves the implementation history; GitHub now provides remote storage and the configured Actions workflow. No application deployment is included.
- Hosted CI started successfully: [initial run](https://github.com/adityaanantharaman16/SECRecon/actions/runs/35097098499), observed in progress after the initial push. This records dispatch, not a passing result. Subsequent pushes trigger new checks; use the Actions page for the latest status.
- Next concrete task: inspect the latest hosted CI result, then M4.1 when requested.

### 2026-09-16: M1 recorded-source gate completed

- Captured three immutable fixture bundles with ten exact decoded HTTP bodies, source URLs, UTC fetch times, SHA-256 checksums and byte lengths. Recorded the real Robinhood 10-K/10-K/A pair and independently checked year-end assets in the original document, amendment and Company Facts. See [fixture notes](../tests/fixtures/recorded/README.md).
- Added five offline contract/recorder checks and one integration gate. Repeatedly importing the real pair preserves canonical output and both filing-document links. The recorder now refuses to overwrite a nonempty capture directory.
- Commands: set `SEC_FIXTURE_USER_AGENT` locally from the approved Git email, then `python scripts/record_fixture.py --cik 1874178`, `--cik 1018724`, and `--cik 1783879` (three, three and four successful requests respectively). Contact values were neither printed nor committed.
- `.venv/Scripts/python.exe scripts/check.py`: lint/format/types pass; 15 host tests pass, 39 integration tests intentionally skip without their services.
- `docker compose -p secrecon-test -f compose.yaml -f compose.test.yaml run --build --rm test`: **54 passed**, 89.47% coverage, two existing upstream deprecation warnings, 39.10 seconds. Ignored log: `.local/m1-real-fixture-gate.log`.
- Docker startup encountered the known stale socket issue. Preserved the verified runtime socket directories with timestamp suffix `secrecon-recovery-20260916-083105` and restarted Docker; no images, volumes or settings were reset. Isolated test services are stopped after validation.
- Owner demonstration: inspect the real fixture notes, follow a normalized Assets value to the original bytes, and explain why the same value belongs to two distinct filings. An amendment can correct formatting without changing financial results.
- No database migration or application dependency change. The approved contact is stored locally while SEC mode stays offline. Work prepared on `codex/finish-m1-real-fixtures`, committed and fast-forwarded to local `main`; no remote publication.
- Next concrete task: M4.1 when requested. Broader amendment reconciliation is not implemented by the fixture verification.

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
