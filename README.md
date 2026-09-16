# SECRecon

SEC filing ingestion, financial fact provenance, and amendment reconciliation, with demonstrable failure recovery.

**SECRecon** is the project name. The repository folder remains SECReconcile. M0–M5 implement source preservation, durable workers, replay, snapshot-bound reconciliation, a connected operations UI and local observability; see the state file for exact acceptance results. [Recorded SEC fixtures](tests/fixtures/recorded/README.md) include a verified real original/amendment pair alongside the explicitly synthetic failure examples.

Start here:

- [Project guide](docs/PROJECT_GUIDE.md): scope, architecture, milestones, testing gates, Docker, Git, and delivery plan.
- [Current project state](docs/PROJECT_STATE.md): completed work, next task, evidence, and session handoff.
- [Agent instructions](AGENTS.md): how implementation agents should use and maintain this context.

## Start locally

Want to understand the intended product first? The [interactive frontend concept](demo/README.md) uses fictional data and has a guided walkthrough. Run `py -3.13 -m http.server 8010 --bind 127.0.0.1 --directory demo`, then open http://127.0.0.1:8010. It needs no backend or Docker.

Requires Docker Desktop with its Linux engine. Python is only needed to generate the local environment file; Windows has `py`, or use your existing Python installation.

```powershell
py -3.13 scripts/bootstrap.py
docker compose up -d --build
```

Open the [connected workspace](http://localhost:8000), [API documentation](http://localhost:8000/docs) or [readiness](http://localhost:8000/health/ready). Services bind to localhost; database and storage ports stay internal unless you include `compose.dev.yaml`. Operator screens/actions require the generated `SECRECON_ADMIN_TOKEN` from your ignored `.env`; read-only financial screens do not.

The [connected UI walkthrough](docs/runbooks/OPERATIONS_UI.md) explains each screen, authentication, API requests and the optional Grafana/Prometheus/Tempo profile. Start that profile with `docker compose -f compose.yaml -f compose.observability.yaml --profile observability up -d --build`. The static demo remains a separate concept; the connected UI preserves its navy/blue/off-white palette.

Load the explicitly **synthetic** offline demonstration:

```powershell
docker compose run --build --rm test python scripts/seed_demo.py
```

Then inspect [sample facts](http://localhost:8000/v1/facts?accession=0001234567-25-000001) and use a returned fact ID with `/v1/facts/{id}/provenance`. This demo makes no SEC requests. The large sample decimal is deliberately fictional.

## Verify the system

```powershell
docker compose -p secrecon-test -f compose.yaml -f compose.test.yaml run --build --rm test
py -3.13 scripts/service_drills.py
```

The first command runs lint, formatting, strict types, real-service tests and an 80% domain/worker coverage gate. It kills test worker processes and creates/deletes a specifically named temporary test database. The second command stops/restarts only the `secrecon-test` dependencies. Development data is separate.

For host development, install Python 3.12 and uv, then run `uv sync --frozen` and `uv run python scripts/check.py`. The default host check is offline; container checks exercise all dependencies. Use `docker compose -f compose.yaml -f compose.dev.yaml up -d --build` for API reload and loopback database/storage ports.

## Operator workflow

Use the container CLI without installing Python packages on your computer:

```powershell
docker compose run --rm api secrecon watchlist seed
docker compose run --rm api secrecon jobs list
docker compose run --rm api secrecon projection-digest
docker compose run --rm api secrecon replay --generation replay-demo --offline
docker compose run --rm api secrecon reconcile links --refresh
docker compose run --rm api secrecon reconcile create 0001234567-25-000001 0001234567-25-000002
```

Live polling starts only when `.env` explicitly sets `SECRECON_SEC_MODE=live` and an approved identifying `SECRECON_SEC_USER_AGENT` with a contact address. SEC access is off by default. Polling continues while the computer and Docker are running and catches up after restart.

Read the [operations runbook](docs/runbooks/LOCAL_OPERATIONS.md) for backfills, replay promotion, retries and limitations. The runtime requires no paid hosting or cloud account. The private repository is [adityaanantharaman16/SECRecon](https://github.com/adityaanantharaman16/SECRecon); [GitHub Actions](https://github.com/adityaanantharaman16/SECRecon/actions) runs the configured CI gate on pushes and pull requests.
