FROM ghcr.io/astral-sh/uv:0.12.12@sha256:73d2665b478d8fa2de1cf105c6841f8e9cb6b09e568fc7700440c09f8fcd7ac4 AS uv
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS build
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
COPY src src
RUN uv sync --frozen

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime
RUN useradd --create-home --uid 10001 app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=build --chown=app:app /app /app
COPY --chown=app:app migrations migrations
COPY --chown=app:app alembic.ini .
RUN chown app:app /app
USER app
CMD ["secrecon", "serve"]

FROM runtime AS test
COPY --chown=app:app tests tests
COPY --chown=app:app scripts scripts
COPY --chown=app:app pyproject.toml .
CMD ["python", "scripts/check.py"]
