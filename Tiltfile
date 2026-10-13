# Local development. `tilt up` runs the infrastructure, then builds and runs each service from its production
# image. A change to a service's code, to the libraries or to the lockfile rebuilds the image (uv's cache keeps
# that to seconds) and compose restarts the service, its migrations first.
docker_compose("deploy/local/compose.yaml")

docker_build(
    "ledger-identity:local",
    ".",
    dockerfile="services/identity/Dockerfile",
    only=["pyproject.toml", "uv.lock", ".python-version", "libs", "services"],
    ignore=["**/tests", "**/__pycache__", "**/.venv"],
)

dc_resource("postgres", labels=["infrastructure"])
dc_resource("nats", labels=["infrastructure"])
dc_resource("identity-migrate", labels=["identity"])
# The profile `zitadel` (a local identity provider) is not started here: see deploy/local/zitadel/README.md.
IDENTITY_PORT = os.getenv("LEDGER_IDENTITY_PORT", "8100")
dc_resource(
    "identity",
    labels=["identity"],
    links=[link("http://localhost:%s/docs" % IDENTITY_PORT, "identity API")],
)
