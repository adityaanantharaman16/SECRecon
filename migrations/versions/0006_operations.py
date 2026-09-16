"""Indexed queries, durable operator requests, sessions and trace context."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE INDEX companies_name_search ON companies USING gin ((lower(data->>'name')) gin_trgm_ops);
    CREATE INDEX companies_tickers_search ON companies USING gin ((lower((data->'tickers')::text)) gin_trgm_ops);
    CREATE INDEX filings_form_page ON filings(generation,form,accession);
    CREATE INDEX facts_concept_page ON facts(generation,concept,fingerprint);
    CREATE INDEX facts_period_page ON facts(generation,(data->>'end_date'),fingerprint);
    CREATE INDEX jobs_state_page ON jobs(state,id);
    CREATE INDEX events_job_page ON job_events(job_id,id);
    ALTER TABLE jobs ADD COLUMN trace_context jsonb NOT NULL DEFAULT '{}';
    ALTER TABLE jobs ADD COLUMN correlation_id text;
    ALTER TABLE job_attempts ADD COLUMN trace_id text;
    ALTER TABLE job_attempts ADD COLUMN span_id text;
    CREATE TABLE admin_requests (
      id text PRIMARY KEY, idempotency_key text UNIQUE NOT NULL,
      request_hash text NOT NULL, action text NOT NULL, body jsonb NOT NULL,
      job_id text REFERENCES jobs, result jsonb, actor text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE admin_sessions (
      token_hash text PRIMARY KEY, csrf_hash text NOT NULL,
      credential_hash text NOT NULL, expires_at timestamptz NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now());
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE admin_sessions,admin_requests;
    ALTER TABLE job_attempts DROP COLUMN span_id, DROP COLUMN trace_id;
    ALTER TABLE jobs DROP COLUMN correlation_id, DROP COLUMN trace_context;
    DROP INDEX companies_name_search,companies_tickers_search,filings_form_page,
      facts_concept_page,facts_period_page,jobs_state_page,events_job_page;
    """)
