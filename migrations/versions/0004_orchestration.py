"""Durable polling, backfill checkpoints, enrichment and replay inventories."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE watchlist (
      cik text PRIMARY KEY, enabled boolean NOT NULL DEFAULT true,
      next_poll_at timestamptz NOT NULL DEFAULT now(), last_success_at timestamptz);
    CREATE TABLE backfills (
      id text PRIMARY KEY, scope jsonb NOT NULL, state text NOT NULL DEFAULT 'running',
      created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
      max_jobs integer NOT NULL DEFAULT 10000);
    CREATE TABLE backfill_pages (
      backfill_id text REFERENCES backfills, url text, event_id text REFERENCES source_events,
      completed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(backfill_id,url));
    CREATE TABLE backfill_normalizations (
      backfill_id text REFERENCES backfills, job_id text REFERENCES jobs,
      PRIMARY KEY(backfill_id,job_id));
    CREATE TABLE enrichment (
      accession text PRIMARY KEY, cik text NOT NULL, state text NOT NULL DEFAULT 'pending',
      step integer NOT NULL DEFAULT 0, first_seen_at timestamptz NOT NULL DEFAULT now(),
      next_check_at timestamptz NOT NULL DEFAULT now()+interval '1 minute',
      source_event_id text REFERENCES source_events);
    CREATE TABLE replays (
      generation text PRIMARY KEY REFERENCES generations, parser_version text NOT NULL,
      event_ids jsonb NOT NULL, position integer NOT NULL DEFAULT 0,
      state text NOT NULL DEFAULT 'running', error text, digest text,
      created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz);
    """)


def downgrade() -> None:
    op.execute(
        "DROP TABLE replays,enrichment,backfill_normalizations,backfill_pages,backfills,watchlist"
    )
