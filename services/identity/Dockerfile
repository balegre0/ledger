# syntax=docker/dockerfile:1
# The identity service. Built from the workspace's lockfile with uv; run by Granian on uvloop, as an unprivileged
# user, from a file system it never writes to. Build from the repository root:
#   docker build -f services/identity/Dockerfile -t ledger-identity:local .

FROM ghcr.io/astral-sh/uv:0.13.0-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/app/.venv
# The interpreter is uv's own build of CPython: self-contained, so it runs on a distroless base.
RUN uv python install 3.15.0
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package ledger-identity

# Distroless: glibc, CA certificates and time zones, no shell, no package manager; user nonroot (65532).
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
LABEL org.opencontainers.image.title="ledger-identity" \
      org.opencontainers.image.description="Organizations, companies, branches, emission points and memberships" \
      org.opencontainers.image.source="https://github.com/balegre0/ledger"
COPY --from=build /python /python
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER nonroot
EXPOSE 8000 50051
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["/app/.venv/bin/ledger-probe", "http://127.0.0.1:8000/readyz"]
ENTRYPOINT ["/app/.venv/bin/granian", "--interface", "asgi", "--factory", "--host", "0.0.0.0", "--port", "8000", \
            "--loop", "uvloop", "--workers", "1", "--no-access-log", "--respawn-failed-workers", \
            "ledger_identity.main:create_app"]
