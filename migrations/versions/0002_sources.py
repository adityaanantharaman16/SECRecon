"""Immutable sources and versioned financial projections."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE generations (
      name text PRIMARY KEY, parser_version text NOT NULL,
      status text NOT NULL DEFAULT 'building', digest text,
      created_at timestamptz NOT NULL DEFAULT now());
    INSERT INTO generations(name,parser_version,status) VALUES ('live','sec-json-v1','active');
    INSERT INTO system_state(key,value) VALUES ('active_generation','live');
    CREATE TABLE source_events (
      event_id text PRIMARY KEY, manifest jsonb NOT NULL,
      fetched_at timestamptz NOT NULL, sha256 text NOT NULL);
    CREATE TABLE fetch_attempts (
      id text PRIMARY KEY, url text NOT NULL, requested_at timestamptz NOT NULL,
      completed_at timestamptz, event_id text REFERENCES source_events,
      status text NOT NULL, error text);
    CREATE TABLE companies (
      generation text REFERENCES generations ON DELETE CASCADE, cik text,
      data jsonb NOT NULL, source_order text NOT NULL,
      PRIMARY KEY(generation,cik));
    CREATE TABLE filings (
      generation text REFERENCES generations ON DELETE CASCADE, accession text,
      cik text NOT NULL, form text NOT NULL, filing_date date NOT NULL,
      data jsonb NOT NULL, source_order text NOT NULL,
      PRIMARY KEY(generation,accession));
    CREATE INDEX filings_search ON filings(generation,cik,filing_date,accession);
    CREATE TABLE facts (
      generation text REFERENCES generations ON DELETE CASCADE, fingerprint text,
      cik text NOT NULL, accession text NOT NULL, concept text NOT NULL,
      value numeric NOT NULL, data jsonb NOT NULL,
      PRIMARY KEY(generation,fingerprint));
    CREATE INDEX facts_search ON facts(generation,accession,concept,fingerprint);
    CREATE TABLE fact_provenance (
      generation text, fingerprint text, event_id text REFERENCES source_events,
      locator text, parser_version text NOT NULL,
      PRIMARY KEY(generation,fingerprint,event_id,locator),
      FOREIGN KEY(generation,fingerprint) REFERENCES facts ON DELETE CASCADE);
    CREATE TABLE filing_sources (
      generation text REFERENCES generations ON DELETE CASCADE, accession text,
      event_id text REFERENCES source_events, role text,
      PRIMARY KEY(generation,accession,event_id,role));
    CREATE TABLE processing_runs (
      generation text REFERENCES generations ON DELETE CASCADE,
      event_id text REFERENCES source_events, parser_version text,
      status text NOT NULL, fact_count integer NOT NULL DEFAULT 0,
      completed_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(generation,event_id,parser_version));
    CREATE TABLE quarantine_records (
      generation text REFERENCES generations ON DELETE CASCADE,
      event_id text REFERENCES source_events, parser_version text,
      reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(generation,event_id,parser_version));
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE quarantine_records, processing_runs, filing_sources,
      fact_provenance, facts, filings, companies, fetch_attempts, source_events;
    DELETE FROM system_state WHERE key='active_generation';
    DROP TABLE generations;
    """)
