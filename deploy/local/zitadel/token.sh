#!/bin/sh
# Prints an access token (a JWT) of the local developer client, for `Authorization: Bearer ...`.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/.local/client.env"
scope="openid urn:zitadel:iam:user:resourceowner urn:zitadel:iam:org:projects:roles urn:zitadel:iam:org:project:id:$PROJECT_ID:aud"
curl --fail-with-body --silent --show-error "$ZITADEL/oauth/v2/token" \
  --user "$CLIENT_ID:$CLIENT_SECRET" \
  --data-urlencode grant_type=client_credentials --data-urlencode "scope=$scope" \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p'
