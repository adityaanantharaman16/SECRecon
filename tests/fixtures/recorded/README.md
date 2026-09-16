# Recorded SEC sources

Captured directly from SEC on **2026-09-16**, using an explicitly approved identifying contact. The contact header is excluded from these fixtures. Each company directory contains a manifest with source URL, retrieval time, SHA-256 and uncompressed byte length, plus selected filing metadata.

The `.gz` files contain the exact HTTP response bodies exposed by HTTPX after HTTP content decoding. Gzip is lossless repository compression, not a claim to preserve TLS packets or HTTP wire framing. JSON and HTML were not reformatted, filtered or edited. Tests verify every checksum before parsing. These are snapshots as collected, not snapshots from the original filing dates.

| Company / CIK | Selected filing(s) | Purpose |
| --- | --- | --- |
| Rivian / 0001874178 | 0001874178-26-000054, 10-Q | Real discovery, financial facts and document contract |
| Amazon / 0001018724 | 0001018724-26-000026, 10-Q | Additional real schema/identity contract |
| Robinhood / 0001783879 | 0001783879-26-000023, 10-K; 0001783879-26-000029, 10-K/A | Real original/amendment pair with unchanged financial results |

Rivian and Amazon did not expose a supported amendment pair in their captured recent-submissions arrays. Robinhood was added as a fixture example; the five-company default watchlist is unchanged. Approximately 1.4 MB of compressed source data is committed.

## Independently verified pair

Robinhood's [original annual report](https://www.sec.gov/Archives/edgar/data/1783879/000178387926000023/hood-20251231.htm) was filed February 18, 2026. Its [amendment](https://www.sec.gov/Archives/edgar/data/1783879/000178387926000029/hood-20251231.htm), filed February 20, covers the same December 31, 2025 reporting period. The amendment's explanatory note identifies the original report and describes corrections to table formatting and heading placement; it states that previously reported financial results are unchanged.

Manual source inspection verified `us-gaap:Assets` at December 31, 2025 in both documents: inline XBRL context `c-6`, USD unit, displayed **38,137**, scale **6**, giving **38,137,000,000 USD**. Both accession-specific observations in the captured Company Facts JSON match. Contract tests independently inspect those HTML tags, context, unit and scale, then follow the normalized JSON locator back to the source observation. Each accession has 592 supported US-GAAP observations in this snapshot; that is snapshot-specific coverage, not a completeness claim about every financial concept.

The integration gate imports all four Robinhood sources into S3/PostgreSQL, processes them five times, compares canonical digests, and checks separate filing-document provenance for both accessions. An unchanged value remains a separate assertion per filing. Changed-value scenarios remain explicitly synthetic. General amendment matching and comparison are M4 work.

## Recording another snapshot

Run the opt-in `scripts/record_fixture.py --cik <cik> --output-dir <new-directory>` with `SEC_FIXTURE_USER_AGENT` set locally to an approved identifying contact, while the live scheduler is off. At most four sequential requests are made per invocation, separated by at least 0.6 seconds. Non-200 responses stop the recorder. It refuses a nonempty output directory so a later fetch cannot overwrite existing evidence. CI only reads these files; it never calls this recorder or contacts SEC.
