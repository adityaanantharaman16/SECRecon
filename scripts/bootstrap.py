"""Generate local-only credentials. Never overwrite an existing environment."""

import secrets
from pathlib import Path

target = Path(".env")
if target.exists():
    current = target.read_text()
    if "SECRECON_ADMIN_TOKEN=" not in current:
        with target.open("a") as handle:
            handle.write("\nSECRECON_ADMIN_TOKEN=" + secrets.token_urlsafe(32) + "\n")
        print("Added a local operator token; existing settings preserved")
    else:
        print(".env already exists; preserved")
else:
    template = Path(".env.example").read_text()
    template = template.replace("replace-with-local-random-password", secrets.token_hex(24))
    template = template.replace("replace-with-local-admin-token", secrets.token_urlsafe(32))
    target.write_text(template)
    print("Created .env with local credentials; SEC access is disabled")
