#!/bin/sh
# Prints an access token (a JWT) for `Authorization: Bearer ...`: the machine user ledger-dev's (no argument, or
# `machine`) or the person ledger-admin's (`person`); prints nothing and fails, with a message, when it cannot.
# The person is signed in without a browser, through Zitadel's session API (as the bootstrap service account,
# a login client): an authorization request, a session checked with the person's password, the request finalized
# with it, and the code exchanged by the web application. Secrets reach curl on its standard input, never on a
# command line.
set -eu
umask 077
HERE=$(cd "$(dirname "$0")" && pwd)

fail() {
  printf 'token.sh: %s\n' "$*" >&2
  exit 1
}

[ -r "$HERE/.local/client.env" ] || fail "no developer client yet: run bootstrap.sh first"
. "$HERE/.local/client.env"
scope="openid urn:zitadel:iam:user:resourceowner urn:zitadel:iam:org:projects:roles urn:zitadel:iam:org:project:id:$PROJECT_ID:aud"

# field NAME: the first string field NAME of the JSON on standard input, or nothing.
field() {
  grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -n 1 | sed 's/^.*:[[:space:]]*"\(.*\)"$/\1/'
}

# as_service PATH: Zitadel's answer to the bootstrap service account POSTing the JSON on standard input, or a
# failure. The token goes to curl as configuration on its standard input, so the body waits in a file of its own
# (readable by its owner only, deleted at once) and never reaches a command line.
as_service() {
  body=$(mktemp "$HERE/.local/body.XXXXXX")
  cat > "$body"
  status=0
  printf 'header = "Authorization: Bearer %s"\n' "$(tr -d '\r\n' < "$HERE/.local/admin.pat")" |
    curl --config - --fail-with-body --silent --show-error -X POST "$ZITADEL$1" \
      -H 'Content-Type: application/json' --data "@$body" || status=$?
  rm -f "$body"
  return "$status"
}

case "${1:-machine}" in
machine)
  answer=$(printf 'user = "%s:%s"\n' "$CLIENT_ID" "$CLIENT_SECRET" |
    curl --config - --fail-with-body --silent --show-error "$ZITADEL/oauth/v2/token" \
      --data-urlencode grant_type=client_credentials --data-urlencode "scope=$scope") ||
    fail "Zitadel refused the token request: $answer"
  ;;
person)
  [ -r "$HERE/.local/person.env" ] || fail "no person yet: run bootstrap.sh again"
  . "$HERE/.local/person.env"
  location=$(curl --silent --show-error --output /dev/null --write-out '%{redirect_url}' -G \
    "$ZITADEL/oauth/v2/authorize" -H "x-zitadel-login-client: $SERVICE_ID" \
    --data-urlencode "client_id=$LOGIN_CLIENT_ID" --data-urlencode "redirect_uri=$LOGIN_REDIRECT" \
    --data-urlencode response_type=code --data-urlencode "scope=$scope") ||
    fail "Zitadel refused the authorization request"
  request=$(printf '%s' "$location" | sed -n 's/.*[?&]authRequest=\([^&]*\).*/\1/p')
  [ -n "$request" ] || fail "Zitadel's answer to the authorization request named no request"
  answer=$(printf '{"checks":{"user":{"loginName":"%s"},"password":{"password":"%s"}}}' \
    "$PERSON_LOGIN" "$PERSON_PASSWORD" | as_service /v2/sessions) ||
    fail "Zitadel refused the person's session: $answer"
  session=$(printf '%s' "$answer" | field sessionId)
  session_token=$(printf '%s' "$answer" | field sessionToken)
  [ -n "$session" ] && [ -n "$session_token" ] || fail "Zitadel's session held no id or token"
  answer=$(printf '{"session":{"sessionId":"%s","sessionToken":"%s"}}' "$session" "$session_token" |
    as_service "/v2/oidc/auth_requests/$request") ||
    fail "Zitadel refused to finish the authorization request: $answer"
  code=$(printf '%s' "$answer" | field callbackUrl | sed -n 's/.*[?&]code=\([^&]*\).*/\1/p')
  [ -n "$code" ] || fail "Zitadel's callback held no code"
  answer=$(printf 'user = "%s:%s"\n' "$LOGIN_CLIENT_ID" "$LOGIN_CLIENT_SECRET" |
    curl --config - --fail-with-body --silent --show-error "$ZITADEL/oauth/v2/token" \
      --data-urlencode grant_type=authorization_code --data-urlencode "code=$code" \
      --data-urlencode "redirect_uri=$LOGIN_REDIRECT") ||
    fail "Zitadel refused the token request: $answer"
  ;;
*)
  fail "usage: token.sh [machine | person]"
  ;;
esac
token=$(printf '%s' "$answer" | grep -o '"access_token"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1 |
  sed 's/^.*:[[:space:]]*"\(.*\)"$/\1/')
[ -n "$token" ] || fail "Zitadel's answer held no access token"
printf '%s\n' "$token"
