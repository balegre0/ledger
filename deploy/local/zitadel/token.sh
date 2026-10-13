#!/bin/sh
# Prints an access token (a JWT) of the local developer client, for `Authorization: Bearer ...`; prints nothing and
# fails, with a message, when it cannot. The client's secret reaches curl on its standard input, never on a
# command line.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)

fail() {
  printf 'token.sh: %s\n' "$*" >&2
  exit 1
}

[ -r "$HERE/.local/client.env" ] || fail "no developer client yet: run bootstrap.sh first"
. "$HERE/.local/client.env"

scope="openid urn:zitadel:iam:user:resourceowner urn:zitadel:iam:org:projects:roles urn:zitadel:iam:org:project:id:$PROJECT_ID:aud"
answer=$(printf 'user = "%s:%s"\n' "$CLIENT_ID" "$CLIENT_SECRET" |
  curl --config - --fail-with-body --silent --show-error "$ZITADEL/oauth/v2/token" \
    --data-urlencode grant_type=client_credentials --data-urlencode "scope=$scope") ||
  fail "Zitadel refused the token request: $answer"
token=$(printf '%s' "$answer" | grep -o '"access_token"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1 |
  sed 's/^.*:[[:space:]]*"\(.*\)"$/\1/')
[ -n "$token" ] || fail "Zitadel's answer held no access token"
printf '%s\n' "$token"
