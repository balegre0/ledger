# syntax=docker/dockerfile:1
# The ledger platform: one image for every app (ledger-api, ledger-worker, ledger-receiver, ledger-signer,
# ledger-migrate; the command picks one). Built from the workspace's lockfile with uv; each app runs under Granian
# (api, worker, receiver, signer) or once (migrate), as an unprivileged user, from a file system it never writes
# to. The signer runs as a process (a container) of its own (ADR 0010): the image holds its custody code, which
# only `ledger-signer` assembles. Build from the repository root:
#   docker build -f deploy/docker/ledger.Dockerfile -t ledger:local .
#
# Deploying it:
# - Probes: /livez for liveness (the process answers), /readyz for readiness (PostgreSQL, the schemas at head,
#   the runners, the installed regimes); a failing dependency takes a replica out of rotation, it does not
#   restart it. The worker and the signer serve the same probes and their metrics on their own port; the
#   signer's gRPC port (LEDGER_SIGNER_GRPC_PORT, 50051) has no health service and is never a probe's.
# - State: /var/lib/ledger-signer, owned by the image's user (mode 0700), is where a volume holds the local-file
#   KMS's key (local stack only: the signer refuses that KMS outside the environments local and test).
# - Stopping: Granian first drains HTTP, then the app stops within its stop budget (25 s); Granian kills a worker
#   still running 30 s after it asked it to stop. The platform must wait longer than that before its SIGKILL:
#   a grace period of at least 40 s (compose `stop_grace_period`, Kubernetes `terminationGracePeriodSeconds`,
#   whose default of 30 is too short).
# - Scaling: by replicas, never by Granian workers: each process is sized for one app and its database pools.
# - Listening: on 0.0.0.0:8000 in the container (LEDGER_HTTP_HOST and LEDGER_HTTP_PORT below; a host-side run
#   binds loopback only, on 8100 by default).

# Pinned by digest (the multi-platform index of the tag, as deploy/local/images.toml pins it): a re-pushed tag
# cannot change what is built.
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
# itself (its prefix), whatever the platform in its name: the build runs on amd64 and arm64 alike, and the
# runtime stage names only /python and /app/.venv/bin.
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
COPY . .
# Every production distribution, found by directory (no list to keep): modules, countries and apps. Modules and
# regimes are found at run time through entry points, so an app does not depend on them: the image installs them.
# Dev-only distributions (ledger-testing, ledger-devtools, *-testing) are never installed. --inexact keeps what
# each earlier sync installed.
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    for d in modules/*/; do uv sync --locked --no-dev --no-editable --inexact --package "ledger-$(basename "$d")"; done; \
    for d in countries/*/; do uv sync --locked --no-dev --no-editable --inexact --package "ledger-country-$(basename "$d")"; done; \
    for d in apps/*/; do uv sync --locked --no-dev --no-editable --inexact --package "ledger-app-$(basename "$d")"; done
# The signer's state directory, owned by the image's user: a named volume mounted on it starts with that owner.
RUN mkdir -p -m 0700 /state/ledger-signer

# Distroless: glibc, CA certificates and time zones, no shell, no package manager; user nonroot (65532).
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
LABEL org.opencontainers.image.title="ledger" \
      org.opencontainers.image.description="The ledger platform: api, worker, receiver, signer and migrate entrypoints" \
      org.opencontainers.image.source="https://github.com/balegre0/ledger"
COPY --from=build /python /python
COPY --from=build /app/.venv /app/.venv
COPY --from=build --chown=65532:65532 /state/ /var/lib/
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LEDGER_HTTP_HOST=0.0.0.0 \
    LEDGER_HTTP_PORT=8000
USER nonroot
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["/app/.venv/bin/ledger-probe", "http://127.0.0.1:8000/readyz"]
CMD ["/app/.venv/bin/ledger-api"]
