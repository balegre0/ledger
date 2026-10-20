#!/bin/sh
# Writes the local stack's signer tokens (spec §3.7, Decision 12): one random token per caller of the signer,
# each 48 hexadecimal characters (the signer refuses one shorter than 32, or one two callers share), into files
# git ignores and compose reads:
#   .local/signer.env  LEDGER_SIGNER_TOKEN_API, _WORKER and _RECEIVER: the signer checks every caller's
#   .local/api.env     LEDGER_SIGNER_TOKEN_API: the api's own, and nothing else
#   .local/worker.env  LEDGER_SIGNER_TOKEN_WORKER: the worker's own, and nothing else
# Run once before `docker compose -f deploy/local/compose.yaml up`: compose refuses to start without them. Tokens
# that exist are kept; `--new` replaces all three (then recreate the api, the worker and the signer). A token is
# never printed, never passed on a command line, and never leaves these files. Needs od and /dev/urandom.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/.local"
umask 077

fail() {
  printf 'tokens.sh: %s\n' "$*" >&2
  exit 1
}

case "${1:-}" in
  "") ;;
  --new) rm -f "$OUT/signer.env" "$OUT/api.env" "$OUT/worker.env" ;;
  *) fail "unknown argument (the only one is --new)" ;;
esac

if [ -f "$OUT/signer.env" ] && [ -f "$OUT/api.env" ] && [ -f "$OUT/worker.env" ]; then
  printf 'tokens.sh: kept the tokens in %s (--new replaces them)\n' "$OUT"
  exit 0
fi
mkdir -p "$OUT"

# token: 24 random bytes as 48 hexadecimal characters.
token() {
  value=$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')
  [ "${#value}" -eq 48 ] || fail "could not read 24 random bytes from /dev/urandom"
  printf '%s' "$value"
}

api=$(token)
worker=$(token)
receiver=$(token)
[ "$api" != "$worker" ] && [ "$api" != "$receiver" ] && [ "$worker" != "$receiver" ] ||
  fail "two tokens came out equal: run it again"

printf 'LEDGER_SIGNER_TOKEN_API=%s\nLEDGER_SIGNER_TOKEN_WORKER=%s\nLEDGER_SIGNER_TOKEN_RECEIVER=%s\n' \
  "$api" "$worker" "$receiver" > "$OUT/signer.env"
printf 'LEDGER_SIGNER_TOKEN_API=%s\n' "$api" > "$OUT/api.env"
printf 'LEDGER_SIGNER_TOKEN_WORKER=%s\n' "$worker" > "$OUT/worker.env"
printf 'tokens.sh: wrote the tokens of the api, the worker and the receiver to %s\n' "$OUT"
