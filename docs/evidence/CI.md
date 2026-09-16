# Hosted CI investigation

The first completed hosted run (35097188730) failed because SeaweedFS exhausted writable volume slots on the GitHub runner. Its disk-based automatic slot count was too small for the suite's isolated per-test buckets; the server reported `No writable volumes and no free volumes left`. This surfaced as S3 `PutObject` failures, rather than a financial processing assertion.

The isolated test Compose override now explicitly allows 512 small 64 MB volume slots. These are capacity limits, not preallocated disk reservations. Development storage settings are unchanged. The same pinned image and real S3 correctness tests remain in use. Check GitHub Actions for the hosted validation result of the fix.

The fix passed hosted CI at commit `8610bf7`: [successful run 35098280173](https://github.com/adityaanantharaman16/SECRecon/actions/runs/35098280173). All 54 pre-M4 tests passed using the real pinned dependencies. M4's expanded suite runs through the same workflow.
