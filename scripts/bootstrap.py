"""Generate local-only credentials. Never overwrite an existing environment."""

import secrets
from pathlib import Path

target = Path(".env")
if target.exists():
    print(".env already exists; preserved")
else:
    template = Path(".env.example").read_text()
    template = template.replace("replace-with-local-random-password", secrets.token_hex(24))
    target.write_text(template)
    print("Created .env with local credentials; SEC access is disabled")
