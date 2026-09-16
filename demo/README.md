# SECRecon product concept demo

A dependency-free, local, interactive preview of the intended operations interface. It uses fictional companies and values and has **no backend connection**. Browser actions only alter in-memory sample state. Reloading or Reset demo restores it. This is a product concept, not a claim that M5 telemetry, authentication or operations UI is complete.

From the repository root:

```powershell
py -3.13 -m http.server 8010 --bind 127.0.0.1 --directory demo
```

Open <http://127.0.0.1:8010>. No installation or cloud hosting is required. You can also open `index.html` directly. Assets are local; no fonts, analytics, CDNs or API requests are loaded.

## Five-minute walkthrough

Click **Take a walkthrough** for seven guided steps, or explore:

1. **Overview:** sample ingestion lag, processing success and exceptions. “Preserved” means raw evidence is retained, not that every fact was successfully parsed. The sample metrics describe intended M5 telemetry.
2. **Filings:** search by fictional company, ticker or accession. An accession identifies a single original or amended filing. Click any row to open its comparison.
3. **Reconciliation:** Northstar shows changed, unchanged, missing and conflicting observations. Choose Meridian for a formatting-only scenario or Atlas for an amendment without supported facts. Filter the results. Different units, concepts or reporting periods are not interchangeable. Missing is not deleted, and ambiguous values get no numeric delta.
4. **Evidence drawer:** click a financial row. Follow its accession, snapshot, reporting period and JSON locator. Checksums and excerpts are explicitly illustrative. In the actual backend these refer to preserved source bytes. Export JSON downloads clearly labeled fictional data.
5. **Processing jobs:** select each status to inspect its sample history. Redrive the dead-letter job: a NEW queued job appears and the old job remains failed. Quarantine means an unexpected source was preserved without committing partial facts.
6. **Source archive:** distinguish the capture event from its content checksum. Identical bytes can be observed at multiple times and share storage without losing provenance.
7. **Replay & recovery:** run the simulated replay. Four steps build and validate a candidate generation while leaving the active one untouched. Financial replay and restoring operational job history are separate guarantees; the latter needs database backups.

The prototype deliberately contains no real fetch, retry, promotion or destructive controls. Full backend reconciliation is implemented under M4; the finished connected interface belongs to M5.
