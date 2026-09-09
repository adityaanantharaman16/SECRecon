"""Opt-in, bounded SEC fixture recording. Never invoked by CI."""

import argparse
import gzip
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--cik", required=True)
args = parser.parse_args()
cik = args.cik.zfill(10)
user_agent = os.environ.get("SEC_FIXTURE_USER_AGENT", "")
if "@" not in user_agent:
    raise SystemExit("Set SEC_FIXTURE_USER_AGENT to SECRecon and a real contact address")
output = Path("tests/fixtures/recorded") / cik
output.mkdir(parents=True, exist_ok=True)
inventory = []


def record(url, name, kind, accession=None):
    time.sleep(0.6)
    response = httpx.get(url, headers={"User-Agent": user_agent}, timeout=45)
    if response.status_code != 200:
        raise SystemExit(f"SEC fixture recording stopped: HTTP {response.status_code}")
    body = response.content
    (output / (name + ".gz")).write_bytes(gzip.compress(body, mtime=0))
    inventory.append(
        {
            "file": name + ".gz",
            "kind": kind,
            "cik": cik,
            "accession": accession,
            "url": url,
            "fetched_at": datetime.now(UTC).isoformat(),
            "sha256": hashlib.sha256(body).hexdigest(),
            "byte_length": len(body),
        }
    )
    (output / "manifest.json").write_text(json.dumps(inventory, indent=2) + "\n")
    return body


submissions = json.loads(
    record(f"https://data.sec.gov/submissions/CIK{cik}.json", "submissions.json", "submissions")
)
record(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", "facts.json", "facts")
columns = submissions["filings"]["recent"]
rows = [dict(zip(columns, values, strict=True)) for values in zip(*columns.values(), strict=True)]
pair = None
for amendment in rows:
    if amendment["form"] not in {"10-K/A", "10-Q/A"}:
        continue
    originals = [
        row
        for row in rows
        if row["form"] == amendment["form"][:-2]
        and row["reportDate"] == amendment["reportDate"]
        and row["filingDate"] < amendment["filingDate"]
    ]
    if originals:
        pair = [originals[0], amendment]
        break
if pair is None:
    pair = [next(row for row in rows if row["form"] in {"10-K", "10-Q"})]
for index, filing in enumerate(pair):
    accession = filing["accessionNumber"]
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{filing['primaryDocument']}"
    record(url, f"filing-{index}.html", "document", accession)
(output / "selection.json").write_text(json.dumps(pair, indent=2) + "\n")
print(
    json.dumps(
        {
            "cik": cik,
            "filings": [{"accession": row["accessionNumber"], "form": row["form"]} for row in pair],
            "files": len(inventory),
        }
    )
)
