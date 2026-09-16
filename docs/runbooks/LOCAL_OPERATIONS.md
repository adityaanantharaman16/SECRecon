# SECRecon local operations

All commands below run from the repository root. The `secrecon` Compose project holds development/demo data. `secrecon-test` is isolated and may be interrupted by failure tests. Do not use `down --volumes` on the development project unless intentionally discarding its data.

## Startup and configuration

Run `python scripts/bootstrap.py` once; it creates random local credentials in ignored `.env` and preserves an existing file. Start with `docker compose up -d --build`. `docker compose ps` shows API readiness. `docker compose logs --tail 50 worker scheduler` shows background activity.

API readiness requires the expected Alembic head; liveness only establishes that the process can answer. Migrations run once before API/workers start. All Python services use the same application image; only the test image includes development tools.

The current raw store uses an application create-only interface. A local administrator can still alter its volume or use administrative S3 credentials. This is not WORM storage. Keep backups on another disk for protection against physical disk loss; full backup automation is M7 work.

## Offline demonstration

Run `docker compose run --build --rm test python scripts/seed_demo.py`. It imports fictional discovery, facts and a document. Repeating it creates additional fetch provenance but no duplicate financial assertions. Inspect `/v1/facts`, then `/v1/facts/{id}/provenance` in the OpenAPI interface.

The API has read endpoints only at this stage. The richer search, protected web administration and operations UI belong to M5. Raw filing HTML is not executed by a browser-facing application endpoint.

## Live SEC mode

Configure an approved contact address locally in `.env`:

```text
SECRECON_SEC_MODE=live
SECRECON_SEC_USER_AGENT=SECRecon <your-approved-contact-address>
```

Do not commit the filled-in file. Seed the five-company watchlist with `docker compose run --rm api secrecon watchlist seed`, then restart changed service configuration with `docker compose up -d`.

The seeded companies are Apple, Microsoft, Alphabet, Amazon and Rivian. The identifying CIK is authoritative; tickers and company names can change. The maximum configured watchlist is 25.

Live requests share a two-request/second Redis limiter. Only one local environment should run in live mode. A 403 sets a shared pause flag; investigate the contact header and SEC access policy before resuming. To resume after fixing the cause, remove only the pause flag with `docker compose exec redis redis-cli DEL secrecon:sec:paused`. This is an operator action, never automatic evasion.

The owner authorized the configured Git email as the SEC contact on 2026-09-16. That address is saved only in ignored local configuration; the scheduler remains offline by default. The separate `scripts/record_fixture.py` recorder requires `SEC_FIXTURE_USER_AGENT` with an approved contact; run it only while the live scheduler is off so its separate bounded request stream cannot exceed the shared budget. It records source URLs, timestamps and checksums, without committing the contact header. Use `--output-dir <new-directory>` for a new snapshot; nonempty output directories are refused. See [recorded fixture notes](../../tests/fixtures/recorded/README.md) for the real source selection and manual checks.

## Ingest and backfill

```text
docker compose run --rm api secrecon ingest --cik 1874178
docker compose run --rm api secrecon backfill --cik 1874178 --from 2024-09-01 --to 2026-09-01
docker compose run --rm api secrecon backfills show <operation-id>
docker compose run --rm api secrecon backfills cancel <operation-id>
```

Omit `--cik` from a backfill to use the watchlist. Dates are inclusive filing dates; a request can span at most ten years. `--max-jobs` caps discovery/fetch work at at most 10,000 jobs; split larger scopes. Normalization is additional work for captured responses. Incremental jobs have higher priority than backfills.

Backfills follow historical submissions pages and record a checkpoint only with child jobs committed. An interrupted job can resume by redelivery. A separate overlapping request can fetch another snapshot while preserving the same financial assertion identities. Completion waits for linked normalization, and failures yield `completed_with_errors` rather than a false clean success.

Missing facts trigger persisted enrichment checks at bounded intervals. They become `available` once a snapshot includes the accession, or `unavailable` after the bounded revisit policy. Absence alone is not schema corruption.

## Inspect failures and redrive

```text
docker compose run --rm api secrecon jobs list --status dead_letter
docker compose run --rm api secrecon jobs show <job-id>
docker compose run --rm api secrecon jobs redrive <job-id>
```

Redrive produces a new linked job, retaining the failed job and its attempts. Fix schema or integrity problems before redriving quarantine. Adapter accommodation and amendment comparison are M4; do not silently edit preserved bytes to make a job succeed.

## Replay and promotion

```text
docker compose run --rm api secrecon archive-sync
docker compose run --rm api secrecon projection-digest
docker compose run --rm api secrecon replay --generation replay-001 --offline
docker compose run --rm api secrecon replay --generation replay-001 --offline --resume
```

Record the original digest before comparing the same input set. Replay never fetches the SEC. It freezes its manifest inventory and pins the parser version. Its target is a fresh generation, not a destructive reset of the active data. Checksums are verified and partial progress is checkpointed. A failed replay leaves active data unchanged.

To promote a validated result, run the replay command with `--resume --promote --expected-digest <sha256>`. Promotion rejects an incorrect digest, corrupt archive input, an unready target, or an inventory that omits sources already applied to the current active projection. If ingestion advanced meanwhile, create a fresh replay inventory. Do not resume rebuilding an active generation; use a new target.

The previous generation remains available for explicit API queries with `?generation=<name>`. Replaying restores derived facts, filing metadata and provenance; restoring job history requires PostgreSQL backups. The complete release/backup/rollback runbook is an M7 deliverable.

## Verification and evidence

Run `docker compose -p secrecon-test -f compose.yaml -f compose.test.yaml run --build --rm test` for the full offline gate. Run `python scripts/service_drills.py` after building the test image for an actual database interruption and object-store restart.

See `docs/evidence/` for completed gates. Cloud publishing, Grafana dashboards, broad load testing and M4–M7 features remain outside M0–M3. GitHub Actions is configured but has no hosted execution evidence until a remote exists.
