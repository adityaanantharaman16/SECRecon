# ADR 0001: one package, separate processes, local dependencies

Accepted for M0. SECRecon uses one Python package and image with separate API, worker and scheduler entry points. PostgreSQL owns processing state; Redis Streams transports notifications; SeaweedFS stores raw inputs. This keeps deployments small while preserving independent worker failure boundaries.

Use real PostgreSQL, Redis and S3 in integration tests. Default live SEC access is off. Local credentials are generated and ignored. A remote GitHub repository is optional; CI is prepared but remote execution cannot be claimed until one is configured. No cloud account or hosting purchase is required.

