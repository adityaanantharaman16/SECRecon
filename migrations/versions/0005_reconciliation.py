"""Immutable comparisons, candidate evidence and structured schema diagnostics."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE quarantine_records ADD COLUMN diagnostics jsonb NOT NULL DEFAULT '[]';
    ALTER TABLE replays ADD COLUMN comparison_version text NOT NULL DEFAULT 'legacy-no-reconciliation';
    CREATE INDEX provenance_snapshot ON fact_provenance(generation,event_id,fingerprint);
    CREATE INDEX sources_company_facts ON source_events((manifest->>'cik'),fetched_at DESC,event_id DESC)
      WHERE manifest->>'kind'='facts';
    CREATE TABLE source_diagnostics (
      generation text REFERENCES generations ON DELETE CASCADE,
      event_id text REFERENCES source_events, parser_version text,
      diagnostics jsonb NOT NULL, PRIMARY KEY(generation,event_id,parser_version));
    CREATE TABLE amendment_links (
      id text PRIMARY KEY, generation text REFERENCES generations ON DELETE CASCADE,
      amendment text NOT NULL, data jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE amendment_heads (
      generation text REFERENCES generations ON DELETE CASCADE, amendment text,
      link_id text REFERENCES amendment_links, PRIMARY KEY(generation,amendment));
    CREATE TABLE reconciliation_runs (
      id text PRIMARY KEY, generation text REFERENCES generations ON DELETE CASCADE,
      original text NOT NULL, amendment text NOT NULL, data jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now());
    CREATE INDEX reconciliation_lookup ON reconciliation_runs(generation,original,amendment);
    CREATE TABLE fact_changes (
      run_id text REFERENCES reconciliation_runs ON DELETE CASCADE, key_hash text,
      status text NOT NULL CHECK(status IN ('changed','unchanged','only_in_original','only_in_amendment','ambiguous')),
      data jsonb NOT NULL, PRIMARY KEY(run_id,key_hash));
    CREATE TABLE reconciliation_heads (
      generation text REFERENCES generations ON DELETE CASCADE, original text, amendment text,
      run_id text REFERENCES reconciliation_runs, PRIMARY KEY(generation,original,amendment));
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE reconciliation_heads,fact_changes,reconciliation_runs,amendment_heads,amendment_links,source_diagnostics;
    ALTER TABLE quarantine_records DROP COLUMN diagnostics;
    ALTER TABLE replays DROP COLUMN comparison_version;
    DROP INDEX provenance_snapshot,sources_company_facts;
    """)
