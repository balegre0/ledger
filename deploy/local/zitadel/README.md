# Zitadel, locally

The local stack can run a real Zitadel (v4.19.2) so that identity is called with real tokens. Nothing automated
needs it: the tests use a local OpenID provider of their own (`ledger_testing.issuer`).

## Start, bootstrap, call

```sh
docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait --build   # the first start takes a minute
sh deploy/local/zitadel/bootstrap.sh                                                  # again whenever you like
docker compose -f deploy/local/compose.yaml up -d identity                            # identity takes the new audience
TOKEN=$(sh deploy/local/zitadel/token.sh)
curl -X POST "http://127.0.0.1:${LEDGER_IDENTITY_PORT:-8100}/v1/companies" \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(date +%s%N)" -H 'Content-Type: application/json' \
  -d '{"tax_id": "101850043", "legal_name": "Mi Empresa, S.R.L."}'
```

`tilt up` does not start Zitadel: run the first command (compose's profile `zitadel`) beside it.

`bootstrap.sh` makes sure the project `ledger` (its id is the tokens' audience), the API application `ledger-api`
and the machine user `ledger-dev`, whose access tokens are JWTs, exist: it finds them by name, creates what is
missing, and issues the machine user a new client secret each run (the previous one stops working). It writes,
under `deploy/local/zitadel/.local/` (ignored by git): `admin.pat` (Zitadel's bootstrap service account),
`client.env` (the developer client's id and secret) and `identity.env` (`LEDGER_TOKEN_AUDIENCE`, which compose
gives identity after `deploy/local/identity.env`). Both scripts stop with a message when Zitadel refuses a call or
answers without what they need, and pass the tokens and the secret to curl on its standard input, never on its
command line.

## The console

Zitadel serves its console at <http://zitadel.localhost:8080/ui/console>. Its first start makes a human
administrator of the instance, `zitadel-admin@ledger-local.zitadel.localhost` with the password `Password1!`
(Zitadel v4 makes one whatever is configured); the console asks for a new password at the first login. Every port
of the stack is bound to 127.0.0.1.

## What a token holds

Checked against Zitadel v4.19.2: header `typ` `JWT`, `alg` `RS256`; claims `iss` `http://zitadel.localhost:8080`,
`aud` the project id, `sub` the machine user's id, `client_id`, `jti`, `exp`/`iat`/`nbf`, and
`urn:zitadel:iam:user:resourceowner:id` (the organization; identity derives the ledger organization id from it),
with its `...:name` and `...:primary_domain`. Project roles, once granted, come as
`urn:zitadel:iam:org:project:<project id>:roles`.

An ID token of the same project (a login's, or one from a token exchange) is signed with the same keys, is typed
`JWT` too and holds the project id among its audiences and the same organization claim; it differs in what makes
it an ID token: `azp` (and `nonce`, `at_hash`, `amr`, `auth_time` after a login), and no `jti`. identity refuses
it (`401`, `the access token was refused: not_access_token`).

## Names

Zitadel tells its instance by the `Host` header, so it must be reached by the name it was set up with,
`zitadel.localhost:8080`: curl and browsers resolve `*.localhost` to 127.0.0.1, and containers of the stack reach
it through a network alias. A service run on the host (not in compose) resolves names with the system's resolver,
which on Windows and on most Linux distributions does not: add `127.0.0.1 zitadel.localhost` to the hosts file.

## Start over

Zitadel alone (its container, database and bootstrap volume, and `.local`; identity's data and NATS stay):

```sh
sh deploy/local/zitadel/reset.sh
docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait
sh deploy/local/zitadel/bootstrap.sh && docker compose -f deploy/local/compose.yaml up -d identity
```

The whole stack, every volume included (every service's database, NATS's streams, Zitadel):

```sh
docker compose -f deploy/local/compose.yaml --profile zitadel down -v
rm -rf deploy/local/zitadel/.local
```
