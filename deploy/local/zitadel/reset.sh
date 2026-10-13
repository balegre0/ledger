#!/bin/sh
# Starts the local Zitadel over and nothing else: its container, its database and the volume of its bootstrap
# token go, the files bootstrap.sh wrote too; identity's database and the others stay.
# (`docker compose ... down -v` would delete every volume of the stack.) Honours COMPOSE_PROJECT_NAME.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
export COMPOSE_FILE="$HERE/../compose.yaml"
PROJECT=${COMPOSE_PROJECT_NAME:-ledger}

docker compose --profile zitadel rm --stop --force zitadel
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c 'DROP DATABASE IF EXISTS zitadel WITH (FORCE)' -c 'CREATE DATABASE zitadel OWNER zitadel'
volumes=$(docker volume ls --quiet --filter "label=com.docker.compose.project=$PROJECT" \
  --filter label=com.docker.compose.volume=zitadel-bootstrap)
if [ -n "$volumes" ]; then
  docker volume rm $volumes > /dev/null
fi
rm -rf "$HERE/.local"

echo "Zitadel starts over at its next start: docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait"
echo "then run bootstrap.sh and restart identity (until then it expects the audience of deploy/local/identity.env)"
