# ADR 0004: connected operations, durable actions and bounded telemetry

Accepted for M5, 2026-09-16.

## Interface and query contracts

Keep the demo's navy/blue/off-white design direction. The connected product uses FastAPI, Jinja2 server rendering, local CSS and a small JavaScript action controller. The separate `demo/` is still fictional and never connects to the backend. Avoid a second frontend toolchain for this operations-focused project.

Allowlisted SQL implements company name/ticker/CIK search, filing filters, exact financial filters and operator lists. Lists use ascending unique keys, default 50/max 200 rows, and opaque cursors containing the resource, filter fingerprint and selected generation. A cursor pins its generation across an active-generation change and rejects mismatched filters. It is a position, not a credential or a frozen database snapshot: concurrent inserts before the position require a fresh traversal. Facts are historical assertions, not a promise to select the latest observation. Snapshot selection for comparisons is still ADR 0003's contract.

Comparison changes and provenance observations have separate bounded cursors. Filing/source document summaries cap embedded links at 200 and expose truncation. Full canonical replay digests continue to operate over all records internally. Migration 0006 adds trigram company-search indexes, fact/form/page indexes, operator request/session tables and job trace context. Existing facts, jobs and attempts remain intact; older attempts may have no trace.

The API returns sanitized error envelopes with request IDs. List queries set a five-second statement timeout; the database connection default remains 30 seconds for processing. Source HTML is not executed or embedded. Templates autoescape source-derived strings and use a restrictive CSP. Swagger UI 5.33.0 is vendored with npm SHA-512 integrity and license provenance so `/docs` works without a CDN; its initialization script uses a per-response nonce.

## Authentication and action ownership

`scripts/bootstrap.py` creates a random operator token or adds one to an existing local environment without overwriting settings. Blank tokens disable sign-in and bearer administration. Browser sign-in creates an opaque, hashed, eight-hour session in PostgreSQL. Its cookie is HttpOnly/SameSite=Strict; a separate server-issued CSRF token is required for browser mutations, with origin checks. Rotating the configured token invalidates existing sessions. HTTPS deployments must enable Secure cookies; the required runtime is loopback HTTP. Host allowlisting protects the loopback application against arbitrary Host headers.

All administration, including jobs/quarantine reads, requires a session or bearer token. Financial reads and aggregate health remain public on loopback. Do not expose this configuration publicly. Grafana provides a loopback-only anonymous Viewer experience, no anonymous editing, and a generated nondefault administrator password. It is a separate local diagnostic trust boundary, not an Internet deployment.

POST actions require `Idempotency-Key`. An advisory lock serializes a key; its canonical action/body hash must match prior use. Request audit, job and outbox commit together. Responses return 202 with operation/job IDs and a status URL. The browser preserves a key after an uncertain network result and changes it only when the form changes.

Worker handlers reuse existing transaction-scoped services for comparisons, backfill creation/cancellation and redrive. These effects commit under the job's fenced lease. Backfills accept enabled watchlist companies only. Replay gets a server-assigned fresh generation, never promotes through the web, and checkpoints under its existing session lock. Every replay mutation validates the owning job lease before and after its transaction; expired owners cannot advance it. A busy replay is retryable. Operational requests/results are PostgreSQL backup data, not raw-source replay inputs.

## Telemetry and failure isolation

OpenTelemetry trace context is persisted with jobs, carried in Redis messages, and resumed by workers. Attempts retain trace/span IDs. Fetch, archive and projection spans identify their source events; JSON logs carry job/trace context without raw payloads or credentials. A failed worker attempt sets an error span and retains the SQL failure history even if export fails.

Each process has one 256-item nonblocking queue/thread for spans and one for logs, with one-second OTLP HTTP exporter timeouts. Full queues drop telemetry rather than delaying processing. Export failures and drops are observable counters. Low-cardinality SDK metric observations use a bounded set of component/signal labels and periodic background export. No job IDs, accessions or event IDs are metric labels.

The optional Compose profile supplies pinned Collector, Prometheus, Grafana and Tempo images. SQL-derived metrics are scraped by the Collector; SDK metrics/logs/traces arrive over OTLP. Prometheus retains seven days/512 MB, Tempo uses local trace storage (default 14-day retention), and Grafana provisions operations and trace dashboards plus the Tempo datasource. Job links use a trace-ID dashboard variable because the anonymous Viewer cannot use Explore. Collector memory, batches, retry time and sending queues are bounded. Logs go to the Collector's sampled debug exporter with Docker rotation; a searchable Loki backend is deliberately not added. SQL is the durable audit.

Metric semantics: runnable counts exclude deliberate future retries; Redis pending deliveries are separate and become unknown on dependency failure. Ingestion lag is maximum capture-to-normalization latency among successful processing in 24 hours, not SEC acceptance lag. No recent observations display as unavailable. Success/failure graphs measure attempts, while job states measure durable jobs. Poll freshness alerts apply only when SEC live scheduling is enabled. No laptop process can alert while the machine is off; catch-up and recorded history remain the recovery mechanism.

## Validation and remaining scope

M5 tests cover concurrent idempotency, authentication/CSRF/origin checks, cursor traversal and generation pinning, source autoescaping, asynchronous actions, stale replay owners, controlled queue metrics, correlated failure traces, and successful SQL processing during a blocked exporter. Promtool fixtures exercise a lag alert and quiet offline/future-retry conditions. The local smoke script uses an explicit in-process SEC stub, exports a complete trace, and never requests SEC.

M6 owns sustained load measurements and the broad failure matrix. M7 owns release artifact promotion, tested full restores and rollback. This milestone does not claim multi-user authorization, Internet-ready deployment, alert notifications, a log-search backend or measured production throughput.

Primary references: [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/), [Collector configuration](https://opentelemetry.io/docs/collector/configuration/), [Prometheus alert rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/), and [Tempo's official local example](https://github.com/grafana/tempo/tree/v3.0.3/example/docker-compose/single-binary).

Trace panel configuration follows [Grafana traces visualization](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/traces/).
