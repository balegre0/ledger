# ledger-app-api

`ledger-api`: the public API (spec §3.3). Composes every installed module's routers mounted in `api`; holds no
business code. `main` calls `ledger_platform.runtime.serve("api")`: Granian, `LEDGER_HTTP_HOST:LEDGER_HTTP_PORT`
(8100 on a host, 8000 in the image), `/livez`, `/readyz`, `/metrics`. Needs the `LEDGER_TOKEN_*` settings.
