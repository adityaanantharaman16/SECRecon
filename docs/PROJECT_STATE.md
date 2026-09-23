# SECRecon: current project state

Last updated: 2026-09-22.

Moving machines or agents? Start with the consolidated [implementation handoff and deployment roadmap](IMPLEMENTATION_HANDOFF.md), then use this file for the latest checkpoint.

## Current position

**Phase:** M0–M5 and M6.1 complete; M6.2 performance baselines are next.

M0–M5 local gates pass, including recorded-source reconciliation, protected operations, and correlated telemetry. M6.1's isolated failure gate also passes. The latest full isolated suite passes **111 tests** with **91.67%** combined statement/branch coverage of domain and job modules. M6.2–M7 remain planned. The private GitHub repository is [adityaanantharaman16/SECRecon](https://github.com/adityaanantharaman16/SECRecon). CI has separate application and observability jobs; see [CI evidence](evidence/CI.md) and GitHub Actions for the latest hosted result.

M6.1 now has a fixture-driven isolated failure harness, SQL invariant/trace/timing reports and a hard Compose-project refusal safeguard. The owner ran three consecutive clean isolated drills, confirmed the refusal against `secrecon`, and passed the full 111-test Docker gate at 91.67% coverage. See [M6 evidence](evidence/M6.md).

M5 is merged on `main` at `5be2ba8`; its [main CI run passed](https://github.com/adityaanantharaman16/SECRecon/actions/runs/35112209686), verified on 2026-09-19. Subsequent documentation commits do not change that implementation baseline. The current M6.1 work remains unmerged on its feature branch.

The owner has specified **local for now, ideally free**. Vercel is an optional future presentation host, not a required backend dependency. Working name: SECRecon.

## Milestone tracker

| Milestone | Status | Evidence required to close |
| --- | --- | --- |
| M0: foundation | Complete | [M0 evidence](evidence/M0.md), [hosted CI](evidence/CI.md) |
| M1: sources and facts | Complete | [M1 evidence](evidence/M1.md) |
| M2: durable processing | Complete | [M2 evidence](evidence/M2.md) |
| M3: ingestion and rebuilds | Complete | [M3 evidence](evidence/M3.md) |
| M4: reconciliation | Complete | [M4 evidence](evidence/M4.md); 79 tests pass |
| M5: operations | Complete | [M5 evidence](evidence/M5.md); 94 tests, tracing smoke and alert gates |
| M6: failure and performance evidence | In progress | [M6 evidence](evidence/M6.md); M6.1 complete, M6.2 performance baseline and M6.3 recovery report remain |
| M7: release and handoff | Not started | Local release, restore, rollback and case study |

Allowed status values: Not started, In progress, Blocked, Complete. Link evidence when changing status; do not infer completion from time spent.

## First implementation task

M6.2 performance baselines are next. Freeze the documented synthetic dataset and machine resources, then measure replay throughput and digest equivalence, indexed API latency/error rate, crash-recovery time, queue drain, memory and bounded telemetry behavior against the proposed budgets in `docs/PROJECT_GUIDE.md`. Publish actual results and identify the bottleneck; do not hide a missed target or weaken correctness. M6.1 is complete, but M6 remains **In progress** until the performance baseline and recovery report are recorded. The connected UI is served on port 8000; the separate `demo/` on 8010 remains fictional.

Start by checking Git status and Docker readiness, then record the exact reference-machine resources used for benchmarks. The owner's Docker workstation has Python 3.12.14 in `.venv`, Python 3.13 via `py`, and Docker Desktop 4.86.0 with the Linux engine; Docker provides about 16 GB memory. The repository uses `main` with milestone commits; `origin` is `https://github.com/adityaanantharaman16/SECRecon.git`. Branch names must exclude `codex`; use descriptive prefixes such as `feat/`, `fix/`, `test/` or `docs/`. Host `uv` was initially bootstrapped into ignored `.tools`; the Docker workflow does not depend on that host tool remaining available.

## Settled design defaults

Python 3.12, FastAPI, PostgreSQL 17, SQLAlchemy 2/Alembic, Redis Streams, SeaweedFS S3 storage, Docker Compose, pytest, GitHub Actions. One package with separate API, worker, and scheduler processes. Small server-rendered operations UI. Freeze exact compatible patches and image digests at M0.

Start with five US companies, 10-K/10-Q and amendments, two years of filings; expand to 25 companies after gates pass. Reconciliation covers supported Company Facts observations, not complete financial-statement restatements. Fetch and preserve primary filing documents separately.

## Open decisions and risks

- Five default companies: Apple, Microsoft, Alphabet, Amazon and Rivian. Recorded fixtures cover Amazon, Rivian and a Robinhood original/amendment pair. Robinhood's amendment corrects formatting without changing financial results; it does not change the default watchlist. Synthetic changed-value fixtures remain clearly identified.
- The owner explicitly approved the configured Git email as the SEC contact on 2026-09-16, resolving the earlier automatic-review blocker. It is saved in ignored `.env`; it is absent from committed fixture contents and documentation. The application remains configured offline; the bounded fixture recorder made ten authorized SEC requests.
- SeaweedFS conditional-create and restart persistence gates pass. Local S3 credentials are administrative; immutability is application-enforced, not administrator-proof WORM.
- The owner authorized private GitHub publication on 2026-09-16. Public visibility and application hosting are not requested; the required runtime remains local and free.
- Eight weeks is a suggested sequence, not a completion promise. Reduce breadth before weakening correctness gates.
- Indexed search, authenticated asynchronous administration, connected UI and local telemetry are implemented. Load baselines and release/backup automation remain M6/M7. The local UI is not an Internet-ready multi-user service; Grafana is a loopback-only Viewer and telemetry has finite retention.
- Backfill date ranges bound document discovery/fetching. Full captured Company Facts responses retain all supported aggregate observations. See ADR 0002.

## Current commands and evidence

- Start: `docker compose up -d --build` after `python scripts/bootstrap.py`.
- Synthetic demo: `docker compose run --build --rm test python scripts/seed_demo.py`.
- Full gate: `docker compose -p secrecon-test -f compose.yaml -f compose.test.yaml run --build --rm test`.
- Actual dependency drill: `python scripts/service_drills.py --project secrecon-drill-m6-gate --runs 3` (isolated test-only project; hard refusal outside `secrecon-drill-*`).
- Connected UI: `http://localhost:8000`; operator controls require `SECRECON_ADMIN_TOKEN` from ignored `.env`. API docs: `http://localhost:8000/docs`, with local assets and bearer authorization.
- Optional diagnostics: `docker compose -f compose.yaml -f compose.observability.yaml --profile observability up -d --build`; Grafana at `http://localhost:3000`, Prometheus at `http://localhost:9090`.
- Reconciliation: `docker compose exec api secrecon reconcile links --refresh` for existing pre-M4 data, then `GET /v1/amendments` and `GET /v1/reconciliations/{id}`. New projections update comparisons automatically.
- Frontend concept: `py -3.13 -m http.server 8010 --bind 127.0.0.1 --directory demo`, then open `http://127.0.0.1:8010` and select **Take a walkthrough**. See [demo guide](../demo/README.md).
- Runbook: [local operations](runbooks/LOCAL_OPERATIONS.md).
- Architecture: [processing and replay](adr/0002-processing-and-replay.md).
- M4 contracts: [reconciliation and schema evolution](adr/0003-reconciliation-and-schema-evolution.md).
- M5 contracts: [operations and observability](adr/0004-operations-and-observability.md), [connected UI walkthrough](runbooks/OPERATIONS_UI.md).

## Session log

### 2026-09-22: M6.1 isolated failure harness completed

- Completed the Docker-backed acceptance that was unavailable in the implementation agent environment. On branch `feat/m6-failure-harness-2` at `2182d77`, `python scripts/service_drills.py --project secrecon-drill-m6-gate --runs 3` passed three consecutive isolated database-outage/object-store-restart runs. The three timestamped JSON report paths are recorded in [M6 evidence](evidence/M6.md). Those ignored reports remained on the owner's Docker host and were not supplied to this workspace, so the evidence document does not claim a field-level excerpt it cannot verify.
- Reconfirmed the safety-critical refusal: `python scripts/service_drills.py --project secrecon` exited 2 with `SAFETY REFUSAL: refusing unsafe Compose project 'secrecon'; use secrecon-drill-<short-unique-name>`. No unsafe Compose operation ran.
- Full gate: `docker compose -p secrecon-test -f compose.yaml -f compose.test.yaml run --build --rm test` — **111 passed**, **91.67%** combined statement/branch coverage of domain and job modules, **47.69 seconds**, no failures. The earlier host gate also passed Ruff, formatting and strict mypy.
- No architecture decision, migration, dependency, runtime configuration, raw-byte/provenance handling, decimal semantics or fact history changed. M6.1 is **Complete**; M6 remains **In progress** because performance and recovery evidence are separate slices.
- Owner demonstration: show the hard refusal against the development project, then open any of the three drill reports and connect the SQL ownership transitions to the correlated attempt trace and measured recovery timing. The concept is a crash-test track with a locked gate: every destructive exercise gets a disposable environment, while PostgreSQL and the report preserve the authoritative record of what happened.
- Work branch: `feat/m6-failure-harness-2`; implementation commit `2182d77`; initial acceptance-documentation commit `c6c28a3`. No merge to `main`.
- Next concrete task: M6.2 performance baselines using the frozen synthetic dataset and recorded machine resources; publish throughput, digest, latency, error-rate, recovery, queue-drain and memory results with an identified bottleneck. M6.3 recovery report follows.

### 2026-09-22: M6.1 isolated failure harness implementation (Docker verification pending)

- Replaced the one-off dependency drill with a fixture-driven harness that hard-refuses non-`secrecon-drill-*` base names before Compose invocation, derives a fresh child project for every run, refuses any pre-existing labeled child resources before claiming cleanup ownership, always pins the test Compose overlay, supports a three-consecutive-run gate, records software/machine metadata and bounded command timings, writes per-run JSON reports for successful/failed/timed-out commands, and destroys only its established isolated volumes after each run.
- Extended the existing outage probe rather than changing processing semantics. The scenario uses the transactional outbox, pending Redis delivery, fenced SQL lease expiry/reclaim, normal `Worker` recovery, immutable archive bytes and persisted OpenTelemetry context. Before/after reports cover jobs, owners/tokens, attempts/outcomes, events, pending outbox/delivery state, source checksum and correlated trace IDs. Fixture: `tests/fixtures/failure_drills/database_outage.json`.
- Added 17 harness unit tests, including explicit development/release/test project rejection, pre-existing-project refusal, command-timeout recording, full attempt trace-context checks and CLI refusal without Docker. `/opt/data/projects/SECRecon/.venv/bin/python scripts/check.py` passed Ruff, format, strict mypy and **51 host tests**; **60 integration tests skipped** because `SECRECON_INTEGRATION` was not enabled. Total collected: 111; duration 5.17 seconds; two existing upstream warnings remain.
- Docker-backed acceptance is blocked in this Hermes execution environment: `docker info` cannot connect to `/var/run/docker.sock`, and no daemon/socket is available. Therefore no real outage run, three-run report, full integration pass count, coverage or duration is claimed. Pending commands and boundaries are recorded in [M6 evidence](evidence/M6.md).
- No architecture decision changed and no new dependency, migration, cloud service, SEC request or runtime configuration was added. M6 remains **In progress**, not complete.
- Owner demonstration after Docker verification: show refusal against `secrecon`, run three isolated outage/recovery drills, and compare the before/after SQL and trace evidence. The concept is a safety interlock plus a black-box flight recorder: destructive drills can touch only a disposable stack, while SQL remains the authoritative account of ownership and recovery.
- Work branch: `feat/m6-failure-harness-2` (the preferred branch name was already attached to the closed duplicate task's worktree). No PR or merge.
- Next concrete task: provide a working Docker daemon, run `python scripts/service_drills.py --project secrecon-drill-m6-gate --runs 3`, run the full isolated test/coverage gate, update M6 evidence with exact outputs, review the diff, then request review. M6.2 performance baselines follow only after M6.1 closes.

### 2026-09-19: cross-machine and cross-agent handoff

- Verified a clean working tree at M5 commit `5be2ba8`, inspected the actual package, commands, configuration, migrations, tests, ADRs and milestone gates, and checked GitHub main/CI through the existing authenticated API helper. Main matches the M5 merge and run `35112209686` succeeded.
- Added [IMPLEMENTATION_HANDOFF.md](IMPLEMENTATION_HANDOFF.md): consolidated design/correctness contracts, code/evidence map, fresh-machine bootstrap, optional existing-data transfer plan, implemented versus planned boundaries, complete M6/M7 slices and gates, optional public deployment prerequisites, and a ready-to-paste receiving-agent prompt.
- Updated README/guide entry points and corrected the state record to include the completed M5 merge/hosted result. Branch: `docs/cross-machine-handoff`. Checked documentation links and `git diff --check`; application tests were not rerun for this documentation-only change. The 94-test result remains M5 historical evidence; the receiving machine must run its own baseline.
- Limitations: Git does not transfer ignored configuration, volumes, archives, backups or traces. A fresh offline workspace can use committed fixtures. Exact operational-state migration needs a verified PostgreSQL/raw backup and isolated restore; that automation remains M7 work. No data transfer, live ingestion, deployment or new infrastructure was performed here.
- Owner demonstration: clone on a new machine, understand what is already implemented, and give the next agent one consolidated context document. The key distinction is reproducible source/configuration versus durable runtime data and historical evidence.
- Next concrete task: verify setup on the receiving machine, then M6.1 isolated fault-harness safeguards/reporting and existing crash probes. Do not skip M6's required restore drill or three-consecutive-run gate.

### 2026-09-16: M5 connected operations and observability

- Implemented allowlisted/indexed search, generation-pinned keyset pagination, bounded provenance/comparison traversal and sanitized API errors. Migration 0006 retains existing data and adds query indexes, operator requests/sessions and job trace context; populated upgrade tests cover 0004 and 0005.
- Added bearer administration and expiring, hashed browser sessions with CSRF/origin checks. Operator requests commit audit/job/outbox atomically and return durable operation IDs. Worker effects use fenced ownership; replay writes reject expired workers and create fresh candidate generations without web promotion.
- Connected the navy/blue/off-white UI to real local state: company/filing/fact searches, comparison/provenance, source inventory, job attempts, quarantine and recovery. The original demo remains a design concept; the palette is preserved, not a frozen screen specification. Vendored integrity-verified Swagger assets keep documentation offline.
- Added OpenTelemetry spans, JSON logs, bounded export queues, trace context on attempts, SQL-derived metrics and a pinned optional Collector/Prometheus/Grafana/Tempo profile. A separate observability CI job validates configuration and alert firing. Failed telemetry cannot replace or block durable SQL audit.
- Full gate: `docker compose -p secrecon-m5-final -f compose.yaml -f compose.test.yaml run --build --rm test` — **94 passed**, **91.18%** domain/job coverage, 202.49 seconds; Ruff, format and strict mypy passed. Two existing upstream deprecation warnings remain. Final focused API/telemetry audit is recorded in `.local/m5-audit-gate.log`; source search includes aggregate filing evidence as well as document manifests.
- Collector validation and Promtool alert tests passed. Locked Python dependencies exported with `uv export --frozen --no-emit-project` and checked with `pip-audit --no-deps --disable-pip`: no known vulnerabilities found. JavaScript syntax passed. Browser checks verified desktop/mobile views, comparison → provenance → source, sign-in boundary and local Swagger without console errors.
- Took `.local/backups/secrecon-pre-m5.dump`, upgraded the development database to 0006, and started the local observability profile. The mock-fetch smoke exported trace `a93487f3e70dab694092f17f4d6a61a0` through Collector to Tempo; Grafana's datasource returned fetch, archive, enqueue, worker and projection spans. Prometheus returned the live SQL runnable metric. The smoke appends explicitly synthetic events; no new SEC requests occurred.
- Audit corrected Collector retry bounds, Tempo volume ownership, error status on caught worker failures, provenance generation selection, aggregate source lookup, and viewer-compatible trace navigation. A dedicated trace dashboard avoids granting Grafana edit/Explore permissions. Operational logs/traces remain best-effort diagnostics with bounded queues/retention; SQL and raw archives retain authoritative history. Alert notifications and sustained throughput claims are out of scope.
- Work branch: `feat/m5-operations`, milestone commit `85fa628`, trace-navigation fix `566eb4e`; [PR #2](https://github.com/adityaanantharaman16/SECRecon/pull/2) merged after both hosted jobs passed. Squash commit on main: `5be2ba8`; its [main CI run also passed](https://github.com/adityaanantharaman16/SECRecon/actions/runs/35112209686). Local acceptance evidence is [M5](evidence/M5.md).
- Owner demonstration: find a comparison, follow its value to immutable evidence, inspect a failed attempt and trace, then submit an idempotent recovery job. The engineering concepts are provenance, durable intent, fenced effects and observability that cannot compromise processing.
- Next concrete task: M6.1 isolated failure harness, only when requested; then measured performance baselines and a recovery report.

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
