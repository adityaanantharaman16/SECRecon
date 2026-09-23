# SECRecon: current project state

Last updated: 2026-09-23.

Moving machines or agents? Start with the consolidated [implementation handoff and deployment roadmap](IMPLEMENTATION_HANDOFF.md), then use this file for the latest checkpoint.

## Current position

**Phase:** M0–M5 and M6.1 complete. M6.2 performance-baseline evidence is recorded and merged on `main` through PR #6: 3 of 4 targets met, and the soak memory target is honestly missed with an evidence-backed explanation and a proposed revised rule. M6.3 is next.

M0–M5 local gates pass, including recorded-source reconciliation, protected operations, and correlated telemetry. M6.1's isolated failure gate also passes. The latest full isolated suite ran twice on the Hermes Docker host at `83f7fc1` on 2026-09-23, on branch `fix/ci-integration-timeout`. Both runs passed **168 tests** with **91.67%** combined statement/branch coverage of domain and job modules. M6.3 and M7 remain planned. The private GitHub repository is [adityaanantharaman16/SECRecon](https://github.com/adityaanantharaman16/SECRecon). CI has separate application and observability jobs; see [CI evidence](evidence/CI.md) and GitHub Actions for the latest hosted result.

M6.1 now has a fixture-driven isolated failure harness, SQL invariant/trace/timing reports and a hard Compose-project refusal safeguard. The owner ran three consecutive clean isolated drills, confirmed the refusal against `secrecon`, and passed the full 111-test Docker gate at 91.67% coverage.

M6.2 adds an isolated performance harness. Its guide-sized acceptance run measured:
- 4-worker replay of 100,000 synthetic observations in 49.6 s, with a digest identical to the 1-worker run;
- read p95 of 15.5 ms cold and 15.3 ms warm, with 0 errors;
- crash recovery in 61.0 s, three times;
- a 30-minute soak that stayed correct and bounded except for PostgreSQL page cache tracking a deliberately growing database.

See [M6 evidence](evidence/M6.md).

M5 is merged on `main` at `5be2ba8`; its [main CI run passed](https://github.com/adityaanantharaman16/SECRecon/actions/runs/35112209686), verified on 2026-09-19. M6.1 is merged on `main` through PR #4 (`24cee94`) and its acceptance-documentation PR #5 (`4158e6a`). M6.2 is merged on `main` through PR #6 (`6f949e0`). That merge's main CI run failed on a latent reconciliation query-plan defect, not on M6.2 code. The fix is on `fix/ci-integration-timeout`, unmerged; see the first session-log entry.

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
| M6: failure and performance evidence | In progress | [M6 evidence](evidence/M6.md). M6.1 complete. M6.2 evidence recorded and merged (PR #6) (replay, reads and recovery met; soak memory missed, explained, revised rule proposed). M6.3 still needs the recovery report and limitations, three consecutive full drill-matrix runs, and a soak-rule decision. |
| M7: release and handoff | Not started | Local release, restore, rollback and case study |

Allowed status values: Not started, In progress, Blocked, Complete. Link evidence when changing status; do not infer completion from time spent.

## First implementation task

M6.3 (report and milestone gate) is next, after review of M6.2 and a decision on the proposed soak memory rule in [M6 evidence](evidence/M6.md).
- If the rule is approved, gate soak memory for stateful containers on cgroup `anon + shmem`, report page cache as context, and rerun one guide-sized baseline.
- Run the full required failure matrix in `docs/PROJECT_GUIDE.md` three consecutive local times, keeping per-run records. The M6.1 drill harness currently scripts only the database-outage/object-store-restart scenario (`tests/fixtures/failure_drills/`). Map each other matrix row to a scripted drill or an existing integration test (for example `tests/integration/test_crashes.py`, `test_worker_failures.py`) before claiming it.
- Write the consolidated failure/benchmark report, bottleneck analysis and limitations.

Do not hide a missed target or weaken correctness. M6 remains **In progress** until M6.3's gate passes. The connected UI is served on port 8000; the separate `demo/` on 8010 remains fictional.

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
- Performance baseline: `python scripts/performance_baseline.py --project secrecon-perf-m6-baseline`. It runs guide-sized in about 58 minutes and has a hard refusal outside `secrecon-perf-*`. Summarize a report for commit with `python scripts/performance_summary.py <report.json> <summary.json>`.
- Connected UI: `http://localhost:8000`; operator controls require `SECRECON_ADMIN_TOKEN` from ignored `.env`. API docs: `http://localhost:8000/docs`, with local assets and bearer authorization.
- Optional diagnostics: `docker compose -f compose.yaml -f compose.observability.yaml --profile observability up -d --build`; Grafana at `http://localhost:3000`, Prometheus at `http://localhost:9090`.
- Reconciliation: `docker compose exec api secrecon reconcile links --refresh` for existing pre-M4 data, then `GET /v1/amendments` and `GET /v1/reconciliations/{id}`. New projections update comparisons automatically.
- Frontend concept: `py -3.13 -m http.server 8010 --bind 127.0.0.1 --directory demo`, then open `http://127.0.0.1:8010` and select **Take a walkthrough**. See [demo guide](../demo/README.md).
- Runbook: [local operations](runbooks/LOCAL_OPERATIONS.md).
- Architecture: [processing and replay](adr/0002-processing-and-replay.md).
- M4 contracts: [reconciliation and schema evolution](adr/0003-reconciliation-and-schema-evolution.md).
- M5 contracts: [operations and observability](adr/0004-operations-and-observability.md), [connected UI walkthrough](runbooks/OPERATIONS_UI.md).

## Session log

### 2026-09-23 (evening): post-merge main CI failure diagnosed and fixed (reconciliation query plan)

- Date / milestone / slice: 2026-09-23. This is a CI repair on `main` after the M6.2 merge, not a milestone slice. No milestone status changed.
- What happened:
  - [Main CI run 35879892780](https://github.com/adityaanantharaman16/SECRecon/actions/runs/35879892780), for merge commit `6f949e09` (PR #6), failed with `1 failed, 166 passed`.
  - The failing test was `tests/integration/test_recorded_sources.py::test_real_pair_five_deliveries_preserve_digest_and_both_document_links`. PostgreSQL cancelled the facts/provenance join in `secrecon.db.reconciliation.observations()` at the 30 s `statement_timeout`.
  - The tree is byte-identical to `6ed966c`, which passed hosted CI twice (runs 35868079562 and 35868046490).
- Root cause: a latent query-plan defect, diagnosed with evidence rather than assumed to be runner noise.
  - Hosted per-file timings for identical code were bimodal only for the two recorded-pair tests. `test_reconciliation.py` took 19/92/112 s and `test_recorded_sources.py` took 9.7/116/64 s, while `test_orchestration.py` took 7.6/8.1/8.1 s.
  - Projection calls `observations()` in the transaction that has just written the snapshot. Planner statistics therefore cannot describe that generation, and the join is estimated at `rows=1` per side.
  - The planner then picks a nested loop whose inner index scan ignores the join key (`Join Filter: f.fingerprint = p.fingerprint`). On the recorded pair this removes 5,031,112 rows per call.
  - Autoanalyze timing decides the inner index: `facts_search` costs about 1.3 s per call, while `facts_pkey` plus an accession filter costs 18–20 s per call on this host.
  - There are six calls per test. That gives about 116 s, the slow green run. The failed run's slower runner pushed one call past 30 s.
  - The same defect applies outside tests to any new generation (replay/rebuild) or newly projected Company Facts snapshot, and the cost grows quadratically with snapshot size.
  - Full write-up: [CI evidence](evidence/CI.md).
- What now works:
  - `observations()` issues two single-table indexed lookups instead of the join, so no plan can re-scan one table per row of the other. Result rows, keys and ordering are unchanged.
  - Under the reproduced stale-statistics state, each lookup takes 3–6 ms, and `process_source` for a recorded document dropped from 72–77 s to 0.3–0.4 s.
  - Local full-gate pytest time dropped from 178.72 s (the previous entry, `fae3fdc`) to 85.6 s and 87.0 s.
- Files and architecture decisions changed:
  - `src/secrecon/db/reconciliation.py`: the `observations()` body only.
  - `tests/integration/test_reconciliation.py`: one new regression test.
  - `docs/evidence/CI.md` and this file.
  - No migration, index, dependency, Compose, `statement_timeout`, retry or ADR change. No existing assertion was modified.
  - The two statements run under READ COMMITTED as separate snapshots. They are equivalent to the former single statement because callers hold the per-company projection advisory lock (module contract), and facts/provenance rows are insert-only.
- Checks run (Hermes session; 2 vCPU / 7.75 GiB; Docker 29.8.1). Logs are in `.local/ci-fix/` in this worktree and are ignored.
  - Hosted evidence: job logs for the three runs were fetched through the GitHub REST API and compared per file.
  - Plan capture:
    - A scratch diagnostic script, not committed, ran `EXPLAIN (ANALYZE, BUFFERS)` inside the real projection transaction on the recorded pair.
    - Old query: `diag-plans-controlled.log` (never analyzed 1.30 s per call; analyzed with a prior generation 19.2 s per call).
    - Fixed query: `diag-plans-controlled-fixed.log` (3–6 ms).
  - Red: the new regression test run against the old query failed with `AssertionError: (Decimal('3204200'), 12400)` (`regression-red-old-query.log`).
  - Green: `pytest tests/integration/test_reconciliation.py tests/integration/test_recorded_sources.py` gave 8 passed (`regression-green-fixed-query.log`).
  - Sweep:
    - Command: the whole integration suite against PostgreSQL with `auto_explain.log_min_duration=500ms`.
    - Result: 61 passed. The PostgreSQL log contains no `duration:` entries, so no statement reached 500 ms (`sweep-integration-fixed.log`, `sweep-postgres-fixed.log`).
  - Full gate run 1 (working tree = `83f7fc1` code), `docker compose -p secrecon-test-cifix1 -f compose.yaml -f compose.test.yaml run --build --rm test`:
    - Ruff, format and strict mypy passed.
    - **168 passed**, **91.67%** coverage, 85.62 s pytest (1m52 s wall), exit 0 (`full-gate-1.log`).
  - Full gate run 2 (committed `83f7fc1`), same command with `-p secrecon-test-cifix2`:
    - **168 passed**, **91.67%** coverage, 86.95 s pytest (1m54 s wall), exit 0 (`full-gate-2.log`).
  - Host `.venv`: `ruff check .` passed; `ruff format --check .` reported 108 files formatted; `mypy src` found no issues in 43 files.
  - Gate project deviation: the gate used fresh `secrecon-test-cifix1/2` projects instead of `-p secrecon-test`.
    - The existing `secrecon-test` volumes belong to an earlier session's credentials, so `migrate` failed with `password authentication failed`.
    - This unattended session may not run `down --volumes`. The attempt recreated that project's postgres/object-store containers on their existing volumes; no volume was removed.
    - Fresh volumes match hosted CI, which starts empty.
- What the owner should try:
  - Read [CI evidence](evidence/CI.md).
  - Compare this PR's hosted `test_reconciliation.py` and `test_recorded_sources.py` durations with the 92–116 s above.
  - Optionally revert `observations()` locally and watch the new regression test fail on tuples read.
- Concept to explain in plain language: the database plans routes from a map (statistics) drawn before today's new neighbourhood (generation) was built, so it believes each street has one house. Its plan was: for every house on street A, walk all of street B. That is harmless for one house and ruinous for 8,795. How often the map was redrawn (autoanalyze) decided whether the walk took 1 s or 20 s, which is why identical code sometimes passed. The fix asks two direct questions that stay cheap however stale the map is: which facts belong to this filing, and which of those came from this snapshot.
- Known limitations / blocked work:
  - The failing hosted run's exact plan was not captured, because CI has no `auto_explain`. The cause is inferred from its timing signature plus plans reproduced here.
  - Only the integration suite was swept for other slow plans.
  - Diagnostic Compose projects are still running on this host and should be removed with `docker compose -p <name> -f compose.yaml -f compose.test.yaml down --volumes`, which this session was not permitted to run: `secrecon-ci-diag`, `secrecon-ci-plan1`, `secrecon-ci-plan2`, `secrecon-ci-red`, `secrecon-ci-sweep`, `secrecon-test-cifix1`, `secrecon-test-cifix2`. The pre-existing `secrecon-test` services are also running; stop them without `--volumes` unless their old data is no longer wanted.
- Migration or configuration changes: none. A local ignored `.env` was generated in this worktree with `scripts/bootstrap.py`.
- Next concrete task: owner review of the PR, and hosted CI green on the PR and again on `main` after any merge. Then M6.3 as below.
- Commit or PR: `83f7fc1` (fix plus regression test) and this documentation commit on `fix/ci-integration-timeout`. The PR is listed in the task handoff. Not merged.

### 2026-09-23 (later): M6.2 acceptance run recorded, test gate repaired and passing

- Date / milestone / slice: 2026-09-23, M6, M6.2 performance baseline (evidence completion).
- What now works:
  - Guide-sized run 2 ran from committed harness `886d177` with a clean image-input tree. It is the M6.2 acceptance run; exit 3 means correct, one target missed.
    - Replay: 4 workers 49.6 s vs 1 worker 82.8 s. Digest `9be5c25b…d16ae` is identical for 1-worker, 4-worker and rebuild, and `EXCEPT ALL` shows 0 differing rows.
    - Reads at 20 req/s × 600 s: p95 15.53 ms cold / 15.31 ms warm, 0 errors, max page 100.
    - Recovery under a 60 s lease: 61.0 s × 3, each `lease_expired`→`succeeded` with tokens 1→2.
    - Soak: 21,778 jobs, all succeeded. Drain 12.09 jobs/s, backlog cleared 1.0 s after load stopped, peak worker RSS 125.2 MiB.
  - The soak memory miss now has an evidence-backed explanation. PostgreSQL cgroup `anon` stayed at 17.7→17.8 MiB and `shmem` at 140.4→140.6 MiB (medians). The growth in `docker stats` usage (251→354 MiB, early/late max) is `active_file` page cache over a database that the soak deliberately grows from 0.59 to 4.61 GB. The verdict stays **TARGET MISSED**, and a revised rule (gate `anon + shmem`, report page cache) is proposed for review, not applied.
  - The full isolated test gate is green again.
- Files and architecture decisions changed:
  - `tests/unit/test_performance_baseline.py` (commit `fae3fdc`). The image-input existence test assumed a source checkout. The Docker gate runs it inside `secrecon:test`, where `Dockerfile`, `compose.yaml` and `compose.test.yaml` are intentionally absent. The test was fixed, and `IMAGE_INPUTS` and the `Dockerfile` were deliberately left unchanged. A new test pins that provenance keeps those build-definition files.
  - Docs: `docs/evidence/M6.md`, this file, and the committed allowlisted summary `docs/evidence/performance/m6.2-guide-run-2.json`.
  - No harness, probe, `src`, migration, dependency, Compose or ADR change.
- Checks run, all in this Hermes session on the 2 vCPU / 7.75 GiB Docker 29.8.1 host:
  - `docker compose -p secrecon-test -f compose.yaml -f compose.test.yaml run --build --rm test` at `fae3fdc`: **167 passed**, **91.67%** coverage, 178.72 s pytest (227 s wall including build), exit 0. Log: `.local/performance/test-gate-2.log`, ignored.
  - The earlier unattended gate at `358f5dc` failed (`1 failed, 165 passed`). That failure is the test bug fixed above. Log: `.local/performance/test-gate.log`.
  - Host `.venv`: `ruff check .` passed; `ruff format --check .` 108 files formatted; `mypy src` no issues in 43 files; `MYPYPATH=src mypy --strict --explicit-package-bases` on the four harness scripts no issues; `pytest tests/unit` 107 passed.
  - Guide-sized run 2: `python scripts/performance_baseline.py --project secrecon-perf-m6-baseline`, 3,496.6 s, exit 3. Raw report `.local/performance/20260923T044910Z-perf-3fada9f8c50b49e4ad92f1e156b1b007.json` (ignored; SHA-256 recorded in the committed summary).
- What the owner should try:
  - Open `docs/evidence/performance/m6.2-guide-run-2.json` and compare `results.replay_single.digest` with `results.replay_four.digest`.
  - Read `verdicts[3].details.diagnostics_not_gated` to see flat PostgreSQL `anon`/`shmem` beside growing `active_file`.
  - Decide whether to accept the proposed soak memory rule.
- Concept to explain in plain language: a warehouse's floor space looked like it was "leaking" because it kept filling up. It turned out the staff (process memory) and the fixed shelving (shared buffers) never grew. The extra space was the loading dock holding recently delivered boxes (page cache), and the test kept ordering new stock on purpose. The fix is to measure staff and shelving, not the dock.
- Known limitations / blocked work:
  - Timings apply to this 2-vCPU host only.
  - The page-cache explanation is not a proof of boundedness under indefinite growth, because no container memory limit was exercised.
  - Discovery-to-normalized lag is out of scope for fixtures.
  - No PR was opened by earlier runs because `gh` is not installed; this session uses the GitHub REST API instead (see Commit or PR below).
- Migration or configuration changes: none.
- Next concrete task: review M6.2. Then do M6.3: decide the soak rule and, if approved, implement it and rerun one baseline; run the full failure matrix three consecutive times; write the consolidated report and limitations.
- Commit or PR: `fae3fdc` (test fix) plus this documentation commit on `feat/m6-performance-baseline`. The PR is listed in the task handoff. Not merged.

### 2026-09-23: M6.2 performance baseline harness and first guide-sized run (in progress)

- What now works:
  - `scripts/performance_baseline.py` (host) with `scripts/performance_probe.py` (in-stack) measures:
    - 1- vs 4-worker replay of the frozen 100,000-observation synthetic dataset, with digest plus row-level `EXCEPT ALL` equivalence and a sequential-rebuild baseline;
    - cold and warm open-loop read latency at 20 req/s for 10 minutes each;
    - SIGKILL lease-recovery timing, judged from SQL;
    - a 30-minute fixed-rate soak with pre-declared bounded-growth checks.
  - It runs only in generated `secrecon-perf-*` Compose projects and refuses `secrecon`, `secrecon-test`, `secrecon-drill-*` and everything else with exit 2.
  - `scripts/performance_summary.py` writes allowlisted, credential-checked summaries for commit.
- Files and architecture: new scripts and unit tests. `scripts/service_drills.py` was refactored to share its isolation primitives (`validate_isolated_project`, `CommandRecorder`, `IsolatedCompose`) with the new harness; drill behaviour is unchanged. No ADR, migration, dependency, runtime configuration, processing semantics, provenance or decimal handling changed.
- Checks run in this Hermes session. Host, venv Python 3.12:
  - `ruff check .`: passed.
  - `ruff format`: 38 files unchanged.
  - `mypy --strict` on the four scripts: no issues.
  - `pytest tests/unit`: 106 passed.
- Docker on this host: 2 vCPU EPYC, 7.75 GiB, Docker 29.8.1.
  - Smoke run (`--companies 5 --filings-per-company 4 --facts-per-filing 10 …`): exit 0.
  - Guide-sized run 1: exit 3 (correct, target missed). Replay 4 workers 50.7 s against 1 worker 82.0 s, digest match; read p95 cold 15.9 ms / warm 15.7 ms, 0 errors; recovery 61.0 s × 3; soak correct, but PostgreSQL container memory grew from 312 to 648 MiB while the database grew to 4.54 GB. See [M6 evidence](evidence/M6.md).
  - Run 1 was measured before the harness commit, so it is supporting evidence only.
  - Smoke runs found four harness bugs, fixed before the timed runs.
- What the owner should try: `python scripts/performance_baseline.py --project secrecon` (refused), then `python scripts/performance_baseline.py --project secrecon-perf-<name>` (about 58 minutes, or pass smaller `--companies`/`--read-seconds`/`--soak-seconds` for a smoke run).
- Concept: two cashiers counting the same till must agree to the cent (digest), and an auditor then recounts line by line (`EXCEPT ALL`). Speed only counts after both agree.
- Known limitations: the soak memory miss is not yet explained with evidence. Guide-sized run 2 (from committed `886d177`, with reported-only cgroup memory breakdown) and the full isolated test gate were started unattended after this entry; their logs are the machine-local, ignored files `.local/performance/guide-run-2.log` and `.local/performance/test-gate.log` in this worktree. Timings are specific to this 2-vCPU host. Discovery-to-normalized lag is out of scope for fixtures.
- Migration or configuration changes: none. A local ignored `.env` was generated with `scripts/bootstrap.py` in this worktree.
- Next concrete task:
  - Record run 2's verdicts and cgroup diagnostics, and the full test-gate pass count, coverage and duration.
  - Commit `docs/evidence/performance/m6.2-guide-run-2.json`.
  - Either prove the soak memory explanation and propose an evidence-backed revised rule, or keep the miss open.
  - Then request review. M6.3 follows.
- Commits: `886d177` (harness), `cdd1b8c` (summarizer) and this documentation commit on `feat/m6-performance-baseline`. No PR merge.

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
