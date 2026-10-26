# syntax=docker/dockerfile:1
# The fake DGII of the local stack (plan Decision 28): a dev-only image of ledger-country-do-testing, which serves
# `ledger-fake-dgii` (DGII's services for emitters, its directory and its emisor-receptor simulator) on 8471. It is
# never part of the production image (deploy/docker/ledger.Dockerfile installs no dev-only distribution) and never
# runs outside the local stack: the fake trusts any signer and hands tokens to anyone. Built from the repository
# root, from the workspace's lockfile, on the production image's pinned bases (deploy/local/images.toml):
#   docker build -f deploy/docker/fake-dgii.Dockerfile -t ledger:fake-dgii .
# In its container it listens on the container's interfaces (LEDGER_FAKE_DGII_CONTAINER=1 lets it), for the stack's
# apps to reach it as http://fake-dgii:8471; compose publishes it on the host's loopback alone.

FROM ghcr.io/astral-sh/uv:0.13.0-trixie-slim@sha256:11bee95ce27982f00acb0c85aa1b5ee6203a9bb958b7def6f27ead851d454e8d AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv python install 3.15.0
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package ledger-country-do-testing

# Distroless, as the production image: no shell, no package manager; user nonroot (65532).
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
LABEL org.opencontainers.image.title="ledger-fake-dgii" \
      org.opencontainers.image.description="A fake of DGII's e-CF web services, for the local stack only" \
      org.opencontainers.image.source="https://github.com/balegre0/ledger"
COPY --from=build /python /python
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LEDGER_FAKE_DGII_CONTAINER=1
USER nonroot
EXPOSE 8471
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=6 \
    CMD ["/app/.venv/bin/ledger-probe", "http://127.0.0.1:8471/testecf/autenticacion/api/autenticacion/semilla"]
CMD ["/app/.venv/bin/ledger-fake-dgii", "--host", "0.0.0.0"]
