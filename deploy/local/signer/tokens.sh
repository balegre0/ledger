#!/bin/sh
# Writes the local stack's signer tokens (spec §3.7, Decision 12): one random token per caller of the signer,
# each 48 hexadecimal characters (the signer refuses one shorter than 32, or one two callers share), into files
# git ignores and compose reads:
#   .local/signer.env    LEDGER_SIGNER_TOKEN_API, _WORKER and _RECEIVER: the signer checks every caller's
#   .local/api.env       LEDGER_SIGNER_TOKEN_API: the api's own, and nothing else
#   .local/worker.env    LEDGER_SIGNER_TOKEN_WORKER: the worker's own, and nothing else
#   .local/receiver.env  LEDGER_SIGNER_TOKEN_RECEIVER: the receiver's own, and nothing else
# Run once before `docker compose -f deploy/local/compose.yaml up`: without the signer's file compose refuses the
# stack (the api, the worker and the receiver start without theirs, answering signer_unavailable). Tokens that
# exist are kept, once all four files do; otherwise, or with `--new`, all three are replaced (then recreate the
# api, the worker, the receiver and the signer). Each file is written whole or not at all (a temporary file,
# moved into place once it holds its tokens), so an interrupted run leaves no empty or half-written file. A token
# is never printed, never passed on a command line, and never leaves these files. Needs od, mv and /dev/urandom.
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
  --new) rm -f "$OUT/signer.env" "$OUT/api.env" "$OUT/worker.env" "$OUT/receiver.env" ;;
  *) fail "unknown argument (the only one is --new)" ;;
esac

if [ -s "$OUT/signer.env" ] && [ -s "$OUT/api.env" ] && [ -s "$OUT/worker.env" ] && [ -s "$OUT/receiver.env" ]; then
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

# place NAME: standard input into .local/NAME, through a temporary file moved over it once it is not empty.
place() {
  temporary="$OUT/.$1.$$"
  cat > "$temporary"
  [ -s "$temporary" ] || { rm -f "$temporary"; fail "could not write $1"; }
  mv -f "$temporary" "$OUT/$1"
}

printf 'LEDGER_SIGNER_TOKEN_API=%s\n' "$api" | place api.env
printf 'LEDGER_SIGNER_TOKEN_WORKER=%s\n' "$worker" | place worker.env
printf 'LEDGER_SIGNER_TOKEN_RECEIVER=%s\n' "$receiver" | place receiver.env
printf 'LEDGER_SIGNER_TOKEN_API=%s\nLEDGER_SIGNER_TOKEN_WORKER=%s\nLEDGER_SIGNER_TOKEN_RECEIVER=%s\n' \
  "$api" "$worker" "$receiver" | place signer.env # last: the four exist once all are written
printf 'tokens.sh: wrote the tokens of the api, the worker and the receiver to %s\n' "$OUT"
