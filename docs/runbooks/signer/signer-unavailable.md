# Runbook: the signer is unavailable

The signer (`ledger-signer`, spec §3.7, ADR 0010) is the one process that holds certificates and signs. It runs
apart from the api and the worker, which call it over gRPC with tokens of their own. This runbook covers the
signer down, not ready, refusing its callers, or never deployed beside an installed `signer` module.

## Before anything: production cannot run the signer yet

The only KMS is the local key file (`LEDGER_SIGNER_KMS=local-file`), which the signer refuses outside the
environments `local` and `test` (`kms_local_refused`, at start). Until a cloud KMS adapter exists, the signer
runs in the local stack and in tests only, and `remote` (a certification entity) is never offered:
`LEDGER_SIGNER_BACKENDS=remote` fails the start with `entity_not_configured`. A staging or production deployment
that installs the `signer` module therefore has no signer: every symptom below, all the time. That is expected
until then, not an incident.

## What it looks like

- `POST`, `GET` and `DELETE /v1/certificates` answer 503 `signer_unavailable` with `Retry-After: 5`. The api
  itself stays ready: no readiness check of the api or the worker calls the signer, so neither leaves rotation.
- Signing callers (the DO channel through `SignerPort`) get `SignerUnavailableError` and retry later.
- The signer's own probes (`/livez`, `/readyz` on its HTTP port: 8102 on a host, 8000 in its container) fail or
  do not answer. There is no gRPC health service: its gRPC port (50051) is never a probe's.
- The worker keeps routing events the signer consumes (`identity.company.deactivated`) into `signer.inbox`. They
  are not marked unrouted (a consumer is subscribed); they pile up until the signer runs again.

## The alerts

Alerting rules and their routing arrive in v2-M6; these are the two to write.

**Installed, but no signer.** Every app that installs an isolated module exports
`ledger_isolated_module_served{module, isolated_in}`: 1 in the app of the module's role (the signer), 0 in the
apps that only relay it (the api and the worker; migrate also sets it, but runs once and is never scraped). A
module installed with no live app serving it is, for 10 minutes:

```promql
max by (module, isolated_in) (ledger_isolated_module_served) == 0
```

**A signer that is up but not ready.** Every app exports each readiness check's last verdict as
`ledger_readiness_check{check}` (1 ready, 0 not; set on every `/readyz` probe). A check of the signer failing for
5 minutes, whatever the orchestrator did with the probes:

```promql
min by (check) (ledger_readiness_check{job="signer"}) == 0
```

## What to check

1. **Is the signer deployed and starting?** Its logs at start name the first defect found, before anything
   listens, each with a stable code, in the order the app finds them:
   - `direct_url_missing`: the signer listens for wake-ups and needs `LEDGER_DATABASE_DIRECT_URL`.
   - `isolated_module_missing`: the image has no active isolated module (the `signer` module suspended or not
     installed).
   - `grpc_caller_token_refused`: a caller's token is missing, shorter than 32 characters, not visible ASCII, or
     shared by two callers (`LEDGER_SIGNER_TOKEN_API`, `_WORKER`, `_RECEIVER`); a `_PREVIOUS` token alone is no
     token.
   - `kms_local_refused`: the environment is not `local` or `test` (see above).
   - `signer_kms_key_file_missing`, `kms_key_file_unusable`: `LEDGER_SIGNER_KMS_KEY_FILE` unset, or the file
     absent (outside `local`, which creates it) or unreadable. The key file is read when the app is made: a bad
     file fails the start, never readiness, so `signer:kms` in `/readyz` is always true once it serves.
   - `entity_not_configured`: `remote` is offered and no certification entity's adapter exists.
   - `signer_grpc_port_refused`: `LEDGER_SIGNER_GRPC_PORT=0` (any free port, the tests') outside `local`/`test`.
2. **Is it ready?** `curl -fsS http://<signer>:8000/readyz` lists `database`, `schema:platform`, `schema:signer`,
   `listen`, `workers`, `signer:grpc` (its gRPC service serving) and `signer:kms`. A schema check false means the
   migrate job has not brought it to head; `database` false, the login `ledger_signer` cannot connect.
3. **Do its callers reach it?** The api and the worker need `LEDGER_SIGNER_ADDRESS` (`signer:50051` in the local
   stack) and their own token; without them they answer `signer_unavailable` (log `signer.not_configured`). A
   caller whose token the signer does not hold gets `UNAUTHENTICATED` (`grpc_caller_unknown`, logged by the
   signer as `grpc.caller_refused`); a method its caller may not call, `PERMISSION_DENIED`
   (`grpc_method_not_allowed`). The api may import, describe and retire; the worker and the receiver may sign
   and describe.
4. **How far behind is it?** As the `ledger_signer` login (`SET ROLE signer_rw`, it holds no privilege until it
   does):

   ```sql
   SELECT consumer, count(*) AS ready, min(next_attempt_at) AS oldest
     FROM signer.inbox WHERE status = 'ready' GROUP BY consumer;
   ```

   The rows drain on their own once the signer runs; rows `dead` (`last_error_code`) are the consumer's own
   failures, not the outage's.

## Rotating a caller's token

Each caller's token rotates without an outage: set the new token as the signer's `LEDGER_SIGNER_TOKEN_<CALLER>`
and the old one as `LEDGER_SIGNER_TOKEN_<CALLER>_PREVIOUS`, restart the signer (it accepts both), give the caller
the new token and restart it, then remove `_PREVIOUS` and restart the signer again. In the local stack
`sh deploy/local/signer/tokens.sh --new` replaces all three at once: recreate the api, the worker and the signer
(`docker compose -f deploy/local/compose.yaml up -d --force-recreate api worker signer`).

## Never

- Never print a token, a key file or the `Ledger-Certificate-Password` header in a ticket or a log.
- Every proxy, WAF and APM agent in front of the api must redact `Ledger-Certificate-Password` (base64 of a
  PKCS #12 bundle's password, ADR 0010 accepted risk 7) as it redacts `Authorization`.
- Never publish the signer's gRPC port. In the local stack the signer is on the internal network `signer` with
  its callers (the api, the worker) and PostgreSQL alone, so nothing else reaches 50051; its own network
  `signer-edge` carries its published probe port and, locally, still gives it a route out. A deployment needs a
  NetworkPolicy: gRPC ingress from the api and the worker only, egress to PostgreSQL and the KMS only. Until mTLS
  (a service mesh), the token and the network are all that guard it.
