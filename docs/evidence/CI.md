# Hosted CI investigation

The first completed hosted run (35097188730) failed because SeaweedFS exhausted writable volume slots on the GitHub runner. Its disk-based automatic slot count was too small for the suite's isolated per-test buckets; the server reported `No writable volumes and no free volumes left`. This surfaced as S3 `PutObject` failures, rather than a financial processing assertion.

The isolated test Compose override now explicitly allows 512 small 64 MB volume slots. These are capacity limits, not preallocated disk reservations. Development storage settings are unchanged. The same pinned image and real S3 correctness tests remain in use. Check GitHub Actions for the hosted validation result of the fix.

The fix passed hosted CI at commit `8610bf7`: [successful run 35098280173](https://github.com/adityaanantharaman16/SECRecon/actions/runs/35098280173). All 54 pre-M4 tests passed using the real pinned dependencies. M4's expanded suite runs through the same workflow.

M5 adds an independent `observability` job: optional Compose validation, Collector pipeline validation and Promtool alert tests. The existing `checks` job still builds the application and runs lint, formatting, strict typing, real-service integration tests and coverage. Both jobs run on pushes and pull requests. Performance baselines and release delivery remain M6/M7 work.

## Main run 35879892780: reconciliation statement timeout (2026-09-23)

The `checks` job for main merge commit `6f949e09` (PR #6) failed with `1 failed, 166 passed`. `tests/integration/test_recorded_sources.py::test_real_pair_five_deliveries_preserve_digest_and_both_document_links` hit PostgreSQL's 30 s `statement_timeout`. The statement was the facts/provenance join in `secrecon.db.reconciliation.observations()`, reached through the projection's `refresh_company`. The merged tree is identical to `6ed966c`, which passed hosted CI twice, so the merge introduced no code change. The failure was nevertheless not runner noise.

- **Timing evidence (hosted logs).** Most integration files took about the same time in all three runs; for example, `test_orchestration.py` took 7.6 s, 8.1 s and 8.1 s. The two recorded-pair files did not. `test_reconciliation.py` took 19 s, 92 s and 112 s. `test_recorded_sources.py` took 9.7 s, 116 s and 64 s (failed). Uniform contention cannot produce a 5–12× slowdown confined to two tests. (Intervals are measured between pytest's per-file progress lines.)
- **Mechanism (captured plans).**
  - Projection calls `observations()` in the transaction that has just written the snapshot's facts and provenance. The planner's statistics therefore never describe that generation, and it estimates `rows=1` on both sides.
  - It then chooses a nested loop whose inner index scan does not use the join key, even though `facts_pkey` could have served it: `Join Filter: f.fingerprint = p.fingerprint`, with 5,031,112 rows removed per call on the recorded Robinhood pair. The work is quadratic in snapshot size.
  - Autoanalyze timing decides which inner index is used. If the tables have never been analyzed, it is `facts_search`: 3.7M buffer hits, 1.3 s per call on the 2-vCPU Hermes host. If they were analyzed while any other generation existed, it is `facts_pkey`, filtering on accession: 76M buffer hits, 18–20 s per call.
  - Each test makes six such calls, which is about 116 s and matches the slow green run. The failed run's runner was slower still, and one call exceeded 30 s.
- **Fix.**
  - `observations()` now uses two single-table indexed lookups: facts by `(generation, accession)`, then provenance by `(generation, event_id, fingerprint = ANY(...))` on the existing `provenance_snapshot` index. Neither lookup can re-scan the other table per row; the worst case is one pass over the generation's rows. The result rows, keys and ordering are unchanged.
  - The same inputs under the stale-statistics state now take 3–6 ms per lookup. `process_source` for each recorded document fell from 72–77 s to 0.3–0.4 s.
  - The other projection-path reads, `snapshot()` and `evidence()`, join small per-generation or per-accession sets to `source_events` by primary key. A diagnostic run of the whole integration suite with `auto_explain.log_min_duration=500ms` logged no statement at or above 500 ms after the fix.
  - No timeout was raised, no retry was added, and no assertion was changed.
- **Regression test.** `test_snapshot_observations_stay_linear_when_statistics_predate_the_generation` reproduces the stale-statistics state and asserts that the planner cannot see the new generation. It checks the exact result rows, then bounds the tuples read from `pg_stat_xact_user_tables` by the total table rows. It fails on the old query (3,204,200 tuples read against a 12,400 bound) and passes on the fix. Counting tuples rather than timing them keeps it independent of runner speed.
- **Hosted result.** PR #7 at `8a3c153` passed both workflows on the push run [35887476061](https://github.com/adityaanantharaman16/SECRecon/actions/runs/35887476061) and the pull-request run [35887525983](https://github.com/adityaanantharaman16/SECRecon/actions/runs/35887525983). Each run passed 168 tests at 91.67% coverage, in 81.57 s and 80.71 s of pytest time. The two previously bimodal files dropped:
  - `test_recorded_sources.py`: 12.3 s and 11.9 s (was 9.7/116/64 s);
  - `test_reconciliation.py`: 20.9 s and 20.7 s (was 19/92/112 s), now including the new regression test.

See the 2026-09-23 CI-repair entry in `docs/PROJECT_STATE.md` for commands, local logs and the PR.
