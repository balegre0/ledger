# Local development. `tilt up` runs the infrastructure; the v2 apps (api, worker, migrate) join it from the one
# image once they exist (identity is a module of them).
docker_compose("deploy/local/compose.yaml")

dc_resource("postgres", labels=["infrastructure"])
dc_resource("nats", labels=["infrastructure"])
# The profile `zitadel` (a local identity provider) is not started here: see deploy/local/zitadel/README.md.
