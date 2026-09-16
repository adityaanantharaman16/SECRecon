# Connected UI and observability (M5)

## Start and sign in

```text
python scripts/bootstrap.py
docker compose up -d --build
```

Open <http://localhost:8000>. This is the connected application. The old <http://localhost:8010> page, if its static server is running, is the independent fictional concept demo.

Read-only company/filing/fact/comparison screens need no sign-in. For jobs, quarantine and actions, choose **Operator sign-in** and copy `SECRECON_ADMIN_TOKEN` from the local ignored `.env`. Never commit or paste that token into an issue, chat or screenshot. Bootstrap adds it to older environments without changing other settings. Sign out revokes the session; changing the token and restarting services revokes prior sessions. Sessions expire after eight hours.

Existing local data may still be synthetic seed data: the UI shows whatever is actually in the database. **Connected** does not imply SEC live access. The banner/sidebar disclose the current mode. Live configuration is described in LOCAL_OPERATIONS.md.

## Explore and operate

1. **Overview:** source capture count, filing count, eligible backlog, exceptions and freshness. A missing lag observation is unavailable, not zero. Future retries are not runnable backlog.
2. **Companies → Filings:** search by company name/ticker/CIK, then filter filings by CIK, form and date range. A filing detail links its preserved sources and financial assertions.
3. **Facts:** filter by accession, concept and period end. Values stay decimal strings. Click a value's concept to see parser, event, checksum and JSON locator. These are historical assertions; a snapshot-bound comparison chooses the observations for its two accessions explicitly.
4. **Reconciliation:** inspect current candidate evidence and comparison results. Missing observations are coverage gaps; conflicts remain ambiguous. Signed-in operators can queue an explicit pair; this does not override ambiguous automatic links.
5. **Processing jobs:** filter status and open a job to see attempts, transitions, errors and trace links. Redrive creates a new linked job and preserves the failure. A click first produces a durable operator job; follow its operation page to the eventual result.
6. **Quarantine:** inspect schema paths/codes, then follow the preserved source. Correct the parser or input selection before redrive. Do not edit retained bytes.
7. **Replay & recovery:** queue an offline replay with a selected parser into a new candidate generation. Refresh its operation page and inspect the digest. There is no web promotion button. Backfills are bounded, watchlist-only and require live fetching to do useful network work; the UI disables that form offline. Cancellation stops future work while preserving completed data.

Actions return quickly with an operation ID. Keep the same form/request idempotency key when retrying an uncertain response. Editing a form starts a new request. Investigate any failed operator job rather than assuming the button completed the underlying work.

## API

Interactive documentation at <http://localhost:8000/docs> works offline and includes bearer authorization. The complete schema is `/openapi.json`.

Public resources: `/v1/companies`, `/v1/filings`, `/v1/facts`, `/v1/amendments`, `/v1/sources`; detail routes expose filing, comparison and provenance evidence. Protected resources: `/v1/admin/jobs`, `/v1/admin/quarantine`, `/v1/admin/backfills`, `/v1/admin/replays`, and `/v1/admin/operations/{id}`.

Company filters: `q` or `cik`. Filing filters: `cik`, `form`, `from_date`, `to_date`. Fact filters: `cik`, `accession`, `concept`, `period_end`. Job filters: `state`, `kind`. Other operation lists accept `state`. Unsupported filters return 422.

Lists return `items`, `next_cursor`, selected `generation`, capture freshness and coverage. Follow `next_cursor` with the same filters; omit it to restart. The default page is 50, maximum 200. A cursor pins a generation but does not freeze concurrent ingestion. Comparison changes and provenance each have their own `limit`/`cursor`. Embedded document links cap at 200 and indicate truncation. Financial values remain strings in JSON.

Mutation requests require `Authorization: Bearer <local token>`, `Idempotency-Key: <unique request key>` and JSON. Same key/action/body returns the same IDs; reusing it differently returns 409. POST returns 202 with `operation_id`, `job_id`, and `status_url`.

Example bodies (synthetic seed accessions):

```json
{"original":"0001234567-25-000001","amendment":"0001234567-25-000002","generation":"live"}
```

Submit to `/v1/admin/reconciliations`. For `/v1/admin/replays`, use `{"parser_version":"sec-json-v1"}`. Backfills accept `ciks`, `start`, `end`, `max_jobs`; redrive/cancel accept `{}`. There is no arbitrary URL fetch endpoint. Browser actions use session/CSRF credentials instead of storing the operator token in JavaScript.

## Optional observability profile

```text
docker compose -f compose.yaml -f compose.observability.yaml --profile observability up -d --build
```

- Grafana dashboard: <http://localhost:3000/d/secrecon-operations/secrecon-operations>
- Prometheus alerts: <http://localhost:9090/alerts>
- Metrics: <http://localhost:8000/metrics>
- Correlated logs: `docker compose -f compose.yaml -f compose.observability.yaml logs --tail 100 worker otel-collector`

Grafana is a loopback-only anonymous **Viewer**; admin UI and external access are not enabled. The optional services consume roughly 1–2 GB extra memory under this small workload configuration; this is a configured budget, not a measured performance guarantee. Tempo's init service changes ownership of its own named volume so the runtime can stay non-root.

To record a real local trace without contacting SEC:

```text
docker compose -f compose.yaml -f compose.observability.yaml run --build --rm -e SECRECON_OTLP_ENDPOINT=http://otel-collector:4318 -e SECRECON_TELEMETRY_SERVICE=secrecon-smoke test python scripts/observability_smoke.py
```

This appends clearly synthetic fixture events to the local archive and processes them. It prints job IDs and the trace ID, never credentials. Open the job trace link or paste the trace ID into the **SECRecon Trace** dashboard at <http://localhost:3000/d/secrecon-trace/secrecon-trace>. This viewer-compatible dashboard does not require Explore access. Expect `source.fetch`, `archive.preserve`, `job.enqueue`, `job.process`, and `projection.commit` spans. Failed worker attempts also carry error traces; older pre-M5 attempts may not.

SQL audit survives telemetry failure. Span/log queues are bounded at 256 per signal/process, exports time out, and drops/failures become metrics when export is available again. Collector logs are sampled/rotated and traces have finite retention; they are not an audit backup. The profile has no notification service: alert state is visible in Prometheus. No-traffic/offline states and a switched-off laptop cannot be interpreted as healthy continuous ingestion.

Stop just the optional services with `docker compose -f compose.yaml -f compose.observability.yaml stop grafana prometheus tempo otel-collector`. To disable application exports too, recreate API/worker/scheduler using only `compose.yaml`. Do not remove raw or database volumes.
