# Zitadel, locally

The local stack can run a real Zitadel (v4.19.2) so that identity is called with real tokens. Nothing automated
needs it: the tests use a local OpenID provider of their own (`ledger_testing.issuer`).

## Start, bootstrap, call

```sh
docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait --build   # the first start takes a minute
sh deploy/local/zitadel/bootstrap.sh                                                  # again whenever you like
docker compose -f deploy/local/compose.yaml up -d api                                 # the api takes the new audience
PERSON=$(sh deploy/local/zitadel/token.sh person)
curl -X POST "http://127.0.0.1:${LEDGER_API_PORT:-8100}/v1/companies"   -H "Authorization: Bearer $PERSON" -H "Idempotency-Key: $(date +%s%N)" -H 'Ledger-Mode: test'   -H 'Content-Type: application/json'   -d '{"jurisdiction": {"country": "DO"}, "functional_currency": "DOP", "legal_name": "Mi Empresa, S.R.L.",
       "tax_ids": [{"scheme": "do.rnc", "value": "101-85004-3"}]}'
```

A machine user acts only as an API client its organization registered (`403 api_client_unregistered` before),
bound to one mode. The person registers `ledger-dev` (its id is `sub` in its token, and in Zitadel's console):

```sh
curl -X POST "http://127.0.0.1:${LEDGER_API_PORT:-8100}/v1/api_clients"   -H "Authorization: Bearer $PERSON" -H "Idempotency-Key: $(date +%s%N)" -H 'Ledger-Mode: test'   -H 'Content-Type: application/json' -d '{"subject": "<ledger-dev id>", "name": "ledger developer"}'
MACHINE=$(sh deploy/local/zitadel/token.sh)   # its requests act in test mode, Ledger-Mode optional
```

It then needs a role in a company to act there, like any principal (`PUT /v1/companies/{id}/memberships/{subject}`).

The local stack does not start Zitadel: run the first command (compose's profile `zitadel`) beside it.

`bootstrap.sh` and `reset.sh` reach the containers through compose's project: `ledger` (`compose.yaml`'s
`name`) unless `COMPOSE_PROJECT_NAME` is set. If the stack runs under a project of its own
(`docker compose -p ledger-me ...`), give the scripts the same name:
`COMPOSE_PROJECT_NAME=ledger-me sh deploy/local/zitadel/bootstrap.sh`.

`bootstrap.sh` makes sure the project `ledger` (its id is the tokens' audience) with its role
`ledger.organization.admin`, the API application `ledger-api`, the person `ledger-admin`, the web application
`ledger-dev-login` it signs in through (access tokens as JWTs, with its roles) and the machine user `ledger-dev`,
whose access tokens are JWTs, exist, and that the person and the machine user are granted the role (identity
registers companies and API clients for an organization's administrators only): it finds them by name, creates
what is missing, sets the grants to the role, and issues new secrets each run: the machine user's client secret,
the person's password and the web application's client secret (the previous ones stop working). It makes
Zitadel's bootstrap service account a login client as well (`IAM_LOGIN_CLIENT`): `token.sh person` signs the
person in through Zitadel's session API, without a browser (an authorization request, a session checked with the
person's password, the request finished with it, the code exchanged by the web application). It writes, under
`deploy/local/zitadel/.local/` (ignored by git): `admin.pat` (Zitadel's bootstrap service account), `client.env`
(the developer client's id and secret), `person.env` (the person's password and the web application's secret)
and `ledger.env` (`LEDGER_TOKEN_AUDIENCE`, which compose gives the apps after `deploy/local/ledger.env`). Both
scripts stop with a message when Zitadel refuses a call or answers without what they need, and pass the tokens,
passwords and secrets to curl on its standard input or in a file only their owner reads, never on its command
line.

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
`urn:zitadel:iam:org:project:<project id>:roles` (`{"ledger.organization.admin": {"<organization id>": "<domain>"}}`
for `ledger-dev`).

`bootstrap.sh` also installs the Action `ledgerPrincipalKind` (a v1 Action, JavaScript, on the organization's
Complement Token flow, trigger Pre access token creation): every access token then carries
`urn:ledger:principal_kind`, `human` or `machine`, from the user's type, and `ledger.env` sets
`LEDGER_TOKEN_KIND_CLAIM_REQUIRED=true`, as staging and production must (`Settings` refuses them otherwise).
Checked against v4.19.2: `machine` for `ledger-dev` (client credentials and JWT profile), `human` for
`ledger-admin`. In any other environment installing the same Action is a deployment step (v2 Actions need an
HTTP target and serve as well). Without the claim the platform falls back on the token's shape:

A person's access token (`token.sh person`) differs in what tells a person from a machine: its `client_id` is the
web application's id, and the application is among its audiences (Zitadel adds it), where a machine user's
`client_id` is its user name and its audiences are only the projects asked for (client credentials and the JWT
profile grant alike). Zitadel puts no claim naming the user's type in an access token: this is what the platform
reads (`kind_of`).

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

Zitadel alone (its container, database and bootstrap volume, and `.local`; identity's data stays):

```sh
sh deploy/local/zitadel/reset.sh
docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait
sh deploy/local/zitadel/bootstrap.sh && docker compose -f deploy/local/compose.yaml up -d api
```

The whole stack, every volume included (every database, Zitadel):

```sh
docker compose -f deploy/local/compose.yaml --profile zitadel down -v
rm -rf deploy/local/zitadel/.local
```
