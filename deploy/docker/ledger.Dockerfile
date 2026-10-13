# syntax=docker/dockerfile:1
# The identity service. Built from the workspace's lockfile with uv; run by Granian on uvloop, as an unprivileged
# user, from a file system it never writes to. Build from the repository root:
#   docker build -f modules/identity/Dockerfile -t ledger-identity:local .
#
# Deploying it:
# - Probes: /livez for liveness (the process answers), /readyz for readiness (PostgreSQL, the workers, the
#   chassis's tables and identity's migrations); a failing dependency takes a replica out of rotation, it does
#   not restart it.
# - Stopping: Granian first drains HTTP, then the chassis stops within its stop budget (25 s by default: gRPC
#   grace, workers, span flush); Granian kills a worker still running 30 s after it asked it to stop.
#   The platform must wait longer than that before its SIGKILL: a grace period of at least 40 s (compose
#   `stop_grace_period`, Kubernetes `terminationGracePeriodSeconds`, whose default of 30 is too short).
# - Scaling: by replicas, never by Granian workers. The gRPC server and the service's workers (the outbox relay)
#   live in the worker process, beside the app: each further worker would run a gRPC server, an outbox relay and
#   a database pool of its own inside one replica, outside what the replica's limits were sized for.

# Pinned by digest (the multi-platform index of the tag): a re-pushed tag cannot change what is built.
FROM ghcr.io/astral-sh/uv:0.13.0-trixie-slim@sha256:11bee95ce27982f00acb0c85aa1b5ee6203a9bb958b7def6f27ead851d454e8d AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/app/.venv
# The interpreter is uv's own build of CPython: self-contained, so it runs on a distroless base. What a server
# never runs is removed: the GUI (Tcl/Tk, tkinter, IDLE, turtle), pip and ensurepip, the headers, the build
# configuration and the man pages, and the shared libpython (the interpreter is linked statically and never
# loads it; extension modules on Linux do not link it either). The interpreter's directory is the one it names
# itself (its prefix), whatever the platform in its name: the build runs on amd64 and arm64 alike.
RUN uv python install 3.15.0 \
 && cd "$("$(uv python find 3.15.0)" -c 'import sys; print(sys.base_prefix)')" \
 && rm -rf include share \
           bin/idle3* bin/pip* bin/pydoc3* bin/python3*-config \
           lib/libpython3* lib/libtcl* lib/libtk* lib/itcl* lib/tcl9* lib/tk9* lib/thread3* lib/pkgconfig \
           lib/python3.15/test lib/python3.15/idlelib lib/python3.15/tkinter lib/python3.15/turtledemo \
           lib/python3.15/turtle.py lib/python3.15/ensurepip lib/python3.15/config-3.15-* \
           lib/python3.15/site-packages/pip lib/python3.15/site-packages/pip-* \
           lib/python3.15/lib-dynload/_tkinter.*
WORKDIR /src
# Dependencies first, in their own layer: it is rebuilt when the lockfile or a package's manifest changes, not
# when code does. Every member of the workspace is listed (uv checks the lockfile against all of them).
COPY pyproject.toml uv.lock .python-version ./
COPY packages/kernel/pyproject.toml packages/kernel/
COPY contracts/pyproject.toml contracts/
COPY packages/platform/pyproject.toml packages/platform/
COPY packages/testing/pyproject.toml packages/testing/
COPY modules/identity/pyproject.toml modules/identity/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace --package ledger-identity
# Then the workspace's own packages.
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
# One worker (see "Scaling" above); a worker that dies is started again; one that does not stop is killed 30 s
# after it was asked to (see "Stopping" above).
ENTRYPOINT ["/app/.venv/bin/granian", "--interface", "asgi", "--factory", "--host", "0.0.0.0", "--port", "8000", \
            "--loop", "uvloop", "--workers", "1", "--no-access-log", "--respawn-failed-workers", \
            "--workers-kill-timeout", "30", \
            "ledger_identity.main:create_app"]
