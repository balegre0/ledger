#!/bin/sh
# Makes the local Zitadel hold what a developer needs to call ledger with real tokens: a project (its id is the
# audience of the tokens) with the role ledger.organization.admin, an API application for the platform, a person
# (ledger-admin) and a machine user (ledger-dev) both granted that role, the web application the person signs in
# through, and the Action that writes who calls into every access token (urn:ledger:principal_kind). A machine
# acts only as an API client its organization registered, so the person registers the machine user first
# (README.md). Zitadel's bootstrap service account is made a login client too: token.sh signs the person in
# through Zitadel's session API, with no browser.
# Run after `docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait`. It can run again: what
# exists is found by name and kept; the machine user gets a new client secret, the person a new password and the
# web application a new client secret (the previous ones stop working). Needs curl, grep, sed and od. Every step
# that fails, or answers without what it should, stops the script with a message.
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

# grant USER: USER's grant on the project, found by user and project, made if missing, given the role it lacks.
grant() {
  grants=$(printf '{"queries":[{"userIdQuery":{"userId":"%s"}},{"projectIdQuery":{"projectId":"%s"}}]}' \
    "$1" "$project")
  answer=$(call POST /management/v1/users/grants/_search --data "$grants") ||
    fail "looking for the grant of user $1 failed: $answer"
  held_grant=$(printf '%s' "$answer" | field id)
  if [ -z "$held_grant" ]; then
    answer=$(call POST "/management/v1/users/$1/grants" \
      --data "{\"projectId\":\"$project\",\"roleKeys\":[\"$ROLE\"]}") ||
      fail "granting user $1 $ROLE failed: $answer"
    require "the grant's id" "$(printf '%s' "$answer" | field userGrantId)"
  elif ! printf '%s' "$answer" | grep -q "\"$ROLE\""; then # Zitadel refuses a change that changes nothing
    # An update replaces the grant's roles: keep the ones it holds (a quoted, comma-separated list) beside ROLE.
    held=$(printf '%s' "$answer" | grep -o '"roleKeys"[[:space:]]*:[[:space:]]*\[[^]]*\]' | head -n 1 |
      sed 's/^[^[]*\[\(.*\)\]$/\1/')
    answer=$(call PUT "/management/v1/users/$1/grants/$held_grant" \
      --data "{\"roleKeys\":[${held:+$held,}\"$ROLE\"]}") ||
      fail "granting user $1 $ROLE failed: $answer"
  fi
}

grant "$user"

answer=$(call PUT "/management/v1/users/$user/secret" --data '{}') ||
  fail "issuing the machine user's client secret failed"
client_id=$(printf '%s' "$answer" | field clientId)
client_secret=$(printf '%s' "$answer" | field clientSecret)
require "the client id" "$client_id"
require "the client secret" "$client_secret"

# The person: a human user of the organization with a password of this run, granted the role too.
answer=$(call POST /management/v1/users/_search --data "$(equals ledger-admin userName)") ||
  fail "looking for the person failed: $answer"
person=$(printf '%s' "$answer" | field id)
password="Ledger-$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')!"
if [ -z "$person" ]; then
  answer=$(call POST /v2/users/human --data "{\"username\":\"ledger-admin\",
    \"organization\":{\"orgId\":\"$organization\"},\"profile\":{\"givenName\":\"Ledger\",\"familyName\":\"Admin\"},
    \"email\":{\"email\":\"ledger-admin@ledger-local.zitadel.localhost\",\"isVerified\":true},
    \"password\":{\"password\":\"$password\",\"changeRequired\":false}}") ||
    fail "creating the person failed"
  person=$(printf '%s' "$answer" | field userId)
  require "the person's id" "$person"
else
  call POST "/v2/users/$person/password" \
    --data "{\"newPassword\":{\"password\":\"$password\",\"changeRequired\":false}}" > /dev/null ||
    fail "setting the person's password failed"
fi
grant "$person"

# The web application the person signs in through: its access tokens are JWTs that hold the person's roles; its
# configuration is set again on every run (Zitadel refuses a change that changes nothing: not a failure).
LOGIN_REDIRECT=http://localhost/ledger-dev-login
login_config="\"redirectUris\":[\"$LOGIN_REDIRECT\"],\"responseTypes\":[\"OIDC_RESPONSE_TYPE_CODE\"],
  \"grantTypes\":[\"OIDC_GRANT_TYPE_AUTHORIZATION_CODE\"],\"appType\":\"OIDC_APP_TYPE_WEB\",
  \"authMethodType\":\"OIDC_AUTH_METHOD_TYPE_BASIC\",\"accessTokenType\":\"OIDC_TOKEN_TYPE_JWT\",
  \"accessTokenRoleAssertion\":true,\"devMode\":true,\"loginVersion\":{\"loginV2\":{\"baseUri\":\"$LOGIN_REDIRECT\"}}"
answer=$(call POST "/management/v1/projects/$project/apps/_search" --data "$(equals ledger-dev-login name)") ||
  fail "looking for the web application failed: $answer"
app=$(printf '%s' "$answer" | field id)
if [ -z "$app" ]; then
  answer=$(call POST "/management/v1/projects/$project/apps/oidc" \
    --data "{\"name\":\"ledger-dev-login\",$login_config}") || fail "creating the web application failed: $answer"
  app=$(printf '%s' "$answer" | field appId)
  require "the web application's id" "$app"
else
  answer=$(call PUT "/management/v1/projects/$project/apps/$app/oidc_config" --data "{$login_config}") ||
    printf '%s' "$answer" | grep -qi 'no changes' || fail "configuring the web application failed: $answer"
fi
answer=$(call POST "/management/v1/projects/$project/apps/$app/oidc_config/_generate_client_secret" --data '{}') ||
  fail "issuing the web application's client secret failed"
login_secret=$(printf '%s' "$answer" | field clientSecret)
require "the web application's client secret" "$login_secret"
answer=$(call GET "/management/v1/projects/$project/apps/$app") || fail "reading the web application failed"
login_client=$(printf '%s' "$answer" | field clientId)
require "the web application's client id" "$login_client"

# The bootstrap service account signs the person in through the session API (a login client), and stays the
# instance's owner. Zitadel refuses a change that changes nothing: that is not a failure.
answer=$(call GET /auth/v1/users/me) || fail "reading the bootstrap service account failed: $answer"
service=$(printf '%s' "$answer" | field id)
require "the bootstrap service account's id" "$service"
answer=$(call PUT "/admin/v1/members/$service" --data '{"roles":["IAM_OWNER","IAM_LOGIN_CLIENT"]}') ||
  printf '%s' "$answer" | grep -qi 'no changes' ||
  fail "making the service account a login client failed: $answer"

# The Action that says in every access token whether a person or a machine calls (urn:ledger:principal_kind):
# Zitadel puts no user type in one. A v1 Action (JavaScript) on the organization's "Complement Token" flow,
# trigger "Pre access token creation" (flow 2, trigger 5); the platform refuses a token whose claim
# contradicts it, and requires it where LEDGER_TOKEN_KIND_CLAIM_REQUIRED is true (always beyond a laptop).
KIND_SCRIPT="function ledgerPrincipalKind(ctx, api) { api.v1.claims.setClaim('urn:ledger:principal_kind', ctx.v1.getUser().human ? 'human' : 'machine'); }"
kind_action="\"name\":\"ledgerPrincipalKind\",\"script\":\"$KIND_SCRIPT\",\"timeout\":\"10s\",\"allowedToFail\":false"
answer=$(call POST /management/v1/actions/_search \
  --data '{"queries":[{"actionNameQuery":{"name":"ledgerPrincipalKind","method":"TEXT_QUERY_METHOD_EQUALS"}}]}') ||
  fail "looking for the principal kind Action failed: $answer"
action=$(printf '%s' "$answer" | field id)
if [ -z "$action" ]; then
  answer=$(call POST /management/v1/actions --data "{$kind_action}") ||
    fail "creating the principal kind Action failed: $answer"
  action=$(printf '%s' "$answer" | field id)
  require "the Action's id" "$action"
else
  answer=$(call PUT "/management/v1/actions/$action" --data "{$kind_action}") ||
    printf '%s' "$answer" | grep -qi 'no changes' || fail "updating the principal kind Action failed: $answer"
fi
answer=$(call POST /management/v1/flows/2/trigger/5 --data "{\"actionIds\":[\"$action\"]}") ||
  printf '%s' "$answer" | grep -qi 'no changes' || fail "setting the pre access token trigger failed: $answer"

cat > "$OUT/person.env" << ENV
PERSON_ID=$person
PERSON_LOGIN=ledger-admin
PERSON_PASSWORD=$password
LOGIN_CLIENT_ID=$login_client
LOGIN_CLIENT_SECRET=$login_secret
LOGIN_REDIRECT=$LOGIN_REDIRECT
SERVICE_ID=$service
ENV
cat > "$OUT/client.env" << ENV
ZITADEL=$ZITADEL
PROJECT_ID=$project
ORGANIZATION_ID=$organization
CLIENT_ID=$client_id
CLIENT_SECRET=$client_secret
ENV
cat > "$OUT/ledger.env" << ENV
LEDGER_TOKEN_AUDIENCE=$project
LEDGER_TOKEN_KIND_CLAIM_REQUIRED=true
ENV

echo "project $project in organization $organization: the machine user in $OUT/client.env, the person in $OUT/person.env"
echo "now restart the api on the new audience: docker compose -f deploy/local/compose.yaml up -d api"
