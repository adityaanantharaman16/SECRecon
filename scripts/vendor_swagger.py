"""Refresh explicitly versioned Swagger assets from npm; verify registry SHA-512 first."""

import argparse
import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("version", help="Exact swagger-ui-dist release version")
args = parser.parse_args()
with httpx.Client(timeout=60) as client:
    response = client.get("https://registry.npmjs.org/swagger-ui-dist/" + args.version)
    response.raise_for_status()
    metadata = response.json()
    if metadata["version"] != args.version:
        raise ValueError("An exact release version is required")
    tarball = client.get(metadata["dist"]["tarball"])
    tarball.raise_for_status()
body = tarball.content
integrity = "sha512-" + base64.b64encode(hashlib.sha512(body).digest()).decode()
if integrity != metadata["dist"]["integrity"]:
    raise ValueError("Registry integrity mismatch")
destination = Path("src/secrecon/api/assets/swagger")
destination.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
    for name in ("swagger-ui-bundle.js", "swagger-ui.css", "LICENSE"):
        member = archive.getmember("package/" + name)
        if not member.isfile():
            raise ValueError("Expected an ordinary package file")
        stream = archive.extractfile(member)
        assert stream is not None
        (destination / name).write_bytes(stream.read())
(destination / "PROVENANCE.json").write_text(
    json.dumps(
        {
            "package": "swagger-ui-dist",
            "version": args.version,
            "integrity": integrity,
            "source": metadata["dist"]["tarball"],
            "license": metadata["license"],
        },
        indent=2,
    )
    + "\n"
)
print("Vendored Swagger UI " + args.version + "; integrity verified")
