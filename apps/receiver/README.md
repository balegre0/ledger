# ledger-app-receiver

`ledger-receiver`: the fiscal channels' tax-authority- and emitter-facing routes (spec §3.3, §3.8), on a host
name of their own. Mounts every installed channel's `receiver_routers` and nothing else: no module's router, no
consumer, job or schedule. `main` calls `ledger_platform.runtime.serve("receiver")`: Granian,
`LEDGER_HTTP_HOST:LEDGER_HTTP_PORT` (8103 on a host, 8000 in the image), `/livez`, `/readyz`, `/metrics`.

- **No token of ours.** Its callers (DGII, other emitters) are proven by each channel's own seeds, tokens and
  signatures; the app needs no `LEDGER_TOKEN_*` setting and ignores them, and refuses to start with a receiver
  route that declares the app's caller.
- **Paths are case-insensitive** under each channel's key (`/do-ecf-v1/...`): DGII calls them in any case.
- **Limits before anything is read:** each client address is held to `LEDGER_RECEIVER_RATE_PER_SECOND`
  (default 20) with a burst of `LEDGER_RECEIVER_RATE_BURST` (40), per replica; over it, 429 with `Retry-After`.
  Behind an ingress, `LEDGER_RECEIVER_FORWARDED_HOPS` says how many proxies append to `X-Forwarded-For`
  (default 0: the header is ignored and the peer is the client). Bodies over 1 MiB are refused 413 as they
  are read, unless a route declares more (an inbound e-CF, 2 MiB). A channel adds its own per-taxpayer limit.
- **Readiness** is the database and its packages' schemas and checks; never the signer's.
- **Signing** (the ARECF it answers with) goes through the signer with the receiver's own caller token:
  `LEDGER_SIGNER_ADDRESS` and `LEDGER_SIGNER_TOKEN_RECEIVER`. Without them the signer's stand-ins answer
  `signer_unavailable`.
