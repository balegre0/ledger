#!/bin/sh
# Makes the local Zitadel hold what a developer needs to call ledger with a real token: a project (its id is the
# audience of the tokens) with the role ledger.organization.admin, an API application for the platform, and a
# machine user granted that role (it registers companies), with a client secret.
# Run after `docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait`. It can run again: what
# exists is found by name and kept, and the machine user gets a new client secret (the previous one stops
# working). Needs curl, grep and sed. Every step that fails, or answers without what it should, stops the script
# with a message.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ZITADEL=${ZITADEL:-http://zitadel.localhost:8080}
export COMPOSE_FILE="$HERE/../compose.yaml"
OUT="$HERE/.local"
umask 077
mkdir -p "$OUT"

fail() {
  printf 'bootstrap.sh: %s\n' "$*" >&2
  exit 1
}

docker compose cp zitadel:/bootstrap/admin.pat "$OUT/admin.pat" > /dev/null ||
  fail "cannot copy Zitadel's bootstrap token: is the profile zitadel up?"
PAT=$(tr -d '\r\n' < "$OUT/admin.pat")
[ -n "$PAT" ] || fail "Zitadel's bootstrap token ($OUT/admin.pat) is empty"

# call METHOD PATH [curl arguments]: Zitadel's answer, or a failure with its answer. The bootstrap token reaches
# curl as configuration on its standard input, never on a command line other processes can read.
call() {
  method=$1 path=$2
  shift 2
  printf 'header = "Authorization: Bearer %s"\n' "$PAT" |
    curl --config - --fail-with-body --silent --show-error -X "$method" "$ZITADEL$path" \
      -H 'Content-Type: application/json' "$@"
}

# field NAME: the first string field NAME of the JSON on standard input, or nothing. Zitadel's ids, client ids
# and secrets hold no quotes or escapes.
field() {
  grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -n 1 | sed 's/^.*:[[:space:]]*"\(.*\)"$/\1/'
}

# require WHAT VALUE: stop unless VALUE holds something.
require() {
  [ -n "$2" ] || fail "Zitadel answered without $1"
}

# equals NAME FIELD: a search query for the entity whose FIELD is exactly NAME.
equals() {
  printf '{"queries":[{"%sQuery":{"%s":"%s","method":"TEXT_QUERY_METHOD_EQUALS"}}]}' "$2" "$2" "$1"
}

answer=$(call GET /management/v1/orgs/me) || fail "reading the organization failed: $answer"
organization=$(printf '%s' "$answer" | field id)
require "the organization's id" "$organization"

answer=$(call POST /management/v1/projects/_search --data "$(equals ledger name)") ||
  fail "looking for the project failed: $answer"
project=$(printf '%s' "$answer" | field id)
if [ -z "$project" ]; then
  answer=$(call POST /management/v1/projects --data '{"name":"ledger","projectRoleAssertion":true}') ||
    fail "creating the project failed: $answer"
  project=$(printf '%s' "$answer" | field id)
  require "the project's id" "$project"
fi

ROLE=ledger.organization.admin
answer=$(call POST "/management/v1/projects/$project/roles/_search" --data "$(equals "$ROLE" key)") ||
  fail "looking for the project role failed: $answer"
if [ -z "$(printf '%s' "$answer" | field key)" ]; then
  answer=$(call POST "/management/v1/projects/$project/roles" \
    --data "{\"roleKey\":\"$ROLE\",\"displayName\":\"Organization administrator\"}") ||
    fail "creating the project role failed: $answer"
fi

answer=$(call POST "/management/v1/projects/$project/apps/_search" --data "$(equals ledger-api name)") ||
  fail "looking for the API application failed: $answer"
if [ -z "$(printf '%s' "$answer" | field id)" ]; then
  answer=$(call POST "/management/v1/projects/$project/apps/api" \
    --data '{"name":"ledger-api","authMethodType":"API_AUTH_METHOD_TYPE_PRIVATE_KEY_JWT"}') ||
    fail "creating the API application failed: $answer"
  require "the API application's id" "$(printf '%s' "$answer" | field appId)"
fi

answer=$(call POST /management/v1/users/_search --data "$(equals ledger-dev userName)") ||
  fail "looking for the machine user failed: $answer"
user=$(printf '%s' "$answer" | field id)
if [ -z "$user" ]; then
  answer=$(call POST /management/v1/users/machine \
    --data '{"userName":"ledger-dev","name":"ledger developer","accessTokenType":"ACCESS_TOKEN_TYPE_JWT"}') ||
    fail "creating the machine user failed: $answer"
  user=$(printf '%s' "$answer" | field userId)
  require "the machine user's id" "$user"
fi

# The machine user's grant on the project: found by user and project, made if missing, given the role it lacks.
grants=$(printf '{"queries":[{"userIdQuery":{"userId":"%s"}},{"projectIdQuery":{"projectId":"%s"}}]}' \
  "$user" "$project")
answer=$(call POST /management/v1/users/grants/_search --data "$grants") ||
  fail "looking for the machine user's grant failed: $answer"
grant=$(printf '%s' "$answer" | field id)
if [ -z "$grant" ]; then
  answer=$(call POST "/management/v1/users/$user/grants" \
    --data "{\"projectId\":\"$project\",\"roleKeys\":[\"$ROLE\"]}") ||
    fail "granting the machine user $ROLE failed: $answer"
  require "the grant's id" "$(printf '%s' "$answer" | field userGrantId)"
elif ! printf '%s' "$answer" | grep -q "\"$ROLE\""; then # Zitadel refuses a change that changes nothing
  # An update replaces the grant's roles: keep the ones it holds (a quoted, comma-separated list) beside ROLE.
  held=$(printf '%s' "$answer" | grep -o '"roleKeys"[[:space:]]*:[[:space:]]*\[[^]]*\]' | head -n 1 |
    sed 's/^[^[]*\[\(.*\)\]$/\1/')
  answer=$(call PUT "/management/v1/users/$user/grants/$grant" \
    --data "{\"roleKeys\":[${held:+$held,}\"$ROLE\"]}") ||
    fail "granting the machine user $ROLE failed: $answer"
fi

answer=$(call PUT "/management/v1/users/$user/secret" --data '{}') ||
  fail "issuing the machine user's client secret failed"
client_id=$(printf '%s' "$answer" | field clientId)
client_secret=$(printf '%s' "$answer" | field clientSecret)
require "the client id" "$client_id"
require "the client secret" "$client_secret"

cat > "$OUT/client.env" << ENV
ZITADEL=$ZITADEL
PROJECT_ID=$project
ORGANIZATION_ID=$organization
CLIENT_ID=$client_id
CLIENT_SECRET=$client_secret
ENV
cat > "$OUT/identity.env" << ENV
LEDGER_TOKEN_AUDIENCE=$project
ENV

echo "project $project in organization $organization; the developer client is in $OUT/client.env"
echo "now restart identity on the new audience: docker compose -f deploy/local/compose.yaml up -d identity"
