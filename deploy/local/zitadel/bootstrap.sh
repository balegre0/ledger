#!/bin/sh
# Creates, in the local Zitadel, what a developer needs to call ledger with a real token: a project (its id is the
# audience of the tokens), an API application for the platform, and a machine user with a client secret.
# Run once, after `docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait`. A second run fails
# (the names are taken); `docker compose ... --profile zitadel down -v` starts over. Needs curl and sed only.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ZITADEL=${ZITADEL:-http://zitadel.localhost:8080}
export COMPOSE_FILE="$HERE/../compose.yaml"
OUT="$HERE/.local"
umask 077
mkdir -p "$OUT"

docker compose cp zitadel:/bootstrap/admin.pat "$OUT/admin.pat"
PAT=$(tr -d '\r\n' < "$OUT/admin.pat")

# A JSON request with the bootstrap service account; prints Zitadel's compact JSON answer.
call() {
  curl --fail-with-body --silent --show-error -X "$1" "$ZITADEL$2" \
    -H "Authorization: Bearer $PAT" -H 'Content-Type: application/json' --data "$3"
}

# The value of a string field of a compact JSON answer: Zitadel's ids, client ids and secrets hold no quotes.
field() {
  sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p"
}

project=$(call POST /management/v1/projects '{"name":"ledger","projectRoleAssertion":true}' | field id)
call POST "/management/v1/projects/$project/apps/api" \
  '{"name":"ledger-api","authMethodType":"API_AUTH_METHOD_TYPE_PRIVATE_KEY_JWT"}' > /dev/null
user=$(call POST /management/v1/users/machine \
  '{"userName":"ledger-dev","name":"ledger developer","accessTokenType":"ACCESS_TOKEN_TYPE_JWT"}' | field userId)
secret=$(call PUT "/management/v1/users/$user/secret" '{}')
organization=$(curl --fail-with-body --silent --show-error "$ZITADEL/management/v1/orgs/me" \
  -H "Authorization: Bearer $PAT" | field id)

cat > "$OUT/client.env" <<ENV
ZITADEL=$ZITADEL
PROJECT_ID=$project
ORGANIZATION_ID=$organization
CLIENT_ID=$(printf '%s' "$secret" | field clientId)
CLIENT_SECRET=$(printf '%s' "$secret" | field clientSecret)
ENV
cat > "$OUT/identity.env" <<ENV
LEDGER_TOKEN_AUDIENCE=$project
ENV

echo "project $project in organization $organization; the developer client is in $OUT/client.env"
echo "now restart identity on the new audience: docker compose -f deploy/local/compose.yaml up -d identity"
