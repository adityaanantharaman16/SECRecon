# ADR 0002: SQL ownership, archive commit markers and projection generations

Accepted during M1–M3 implementation.

PostgreSQL contains authoritative job state, attempts and outbox rows. Redis has one consumer group and carries job IDs. Acknowledged deliveries are deleted because audit history is in SQL. A sweeper re-notifies unfinished jobs after queue loss. Commit correctness uses row locks, expiring leases and monotonically increasing fencing tokens; a heartbeat alone is insufficient.

Raw bytes use content-addressed S3 keys; create-only manifests mark complete archive events. SQL registration and normalization notification share a transaction. If a manifest exists without registration, inventory repair recreates that notification. An unreferenced blob is retained but is not treated as a completed event. S3 compatibility was verified against the pinned SeaweedFS image, including collision behavior and restart persistence.

Projection writes are serialized per company and generation. This is deliberately coarser than a per-accession lock for the 25-company demo limit, and avoids subtle collisions when one Company Facts snapshot spans many filings. PostgreSQL stores explicit indexed columns plus canonical JSONB assertion metadata. Decimal values are NUMERIC in SQL and strings in API output. Source field order cannot change financial identity.

Polling and backfills are durable discovery jobs. Historical-page checkpoints and child scheduling commit together. Backfill completion includes linked normalization jobs, even though normalization itself is keyed independently by source event. The `max_jobs` option caps discovery/fetch jobs; normalization is additional bounded work per captured response. Cancelled backfills stop new source work; already captured sources may still normalize.

The date window bounds filing discovery work and primary-document fetching. A captured Company Facts response contains aggregate history; v1 retains all supported US-GAAP observations from that response rather than pretending the upstream endpoint was date-filtered. Query by accession/reporting period to narrow results. This retains potential comparative facts needed for later reconciliation.

Replay uses a fixed list of manifests, a fixed parser version and an isolated generation. It checkpoints progress and takes a per-generation advisory lock. Output digests include financial data and provenance, excluding operational attempts and generated processing times. Promotion checks the expected digest and rejects a stale inventory when the active projection has advanced. A shared/exclusive promotion lock makes ongoing writes follow the new active generation safely.

Operational history is restored from database backups, not invented from raw SEC data. An integration test creates a physically empty PostgreSQL database, migrates it, restores facts/provenance from the archive and confirms there are no fabricated jobs.

Initial milestone commits are on the new local `main` branch. Subsequent collaborative work should use the guide's short-lived `codex/` branches and reviewed PRs once a remote is configured.
