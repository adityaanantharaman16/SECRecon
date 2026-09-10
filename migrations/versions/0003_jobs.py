"""Authoritative jobs, attempts, transitions and transactional outbox."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE jobs (
      id text PRIMARY KEY, kind text NOT NULL, payload jsonb NOT NULL,
      idempotency_key text UNIQUE NOT NULL, request_hash text NOT NULL,
      state text NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued','running','retry_wait','succeeded','dead_letter','quarantined','cancelled')),
      priority integer NOT NULL DEFAULT 0, due_at timestamptz NOT NULL DEFAULT now(),
      attempts integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 5,
      owner text, token bigint NOT NULL DEFAULT 0, lease_until timestamptz,
      last_notified_at timestamptz, parent_id text REFERENCES jobs,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      error text);
    CREATE INDEX jobs_due ON jobs(state,due_at,priority);
    CREATE TABLE job_attempts (
      job_id text REFERENCES jobs, token bigint, owner text NOT NULL,
      started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz,
      outcome text NOT NULL DEFAULT 'running', error text,
      PRIMARY KEY(job_id,token));
    CREATE TABLE job_events (
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, job_id text REFERENCES jobs,
      at timestamptz NOT NULL DEFAULT now(), state text NOT NULL, details jsonb NOT NULL);
    CREATE TABLE outbox (
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, job_id text REFERENCES jobs,
      created_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz);
    CREATE UNIQUE INDEX outbox_pending ON outbox(job_id) WHERE sent_at IS NULL;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE outbox,job_events,job_attempts,jobs")
