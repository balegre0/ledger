#!/bin/sh
# Writes the local stack's trusted roots for channel do-ecf-v1 (LEDGER_DO_ECF_TRUSTED_ROOTS): a development
# certification authority, self-signed, into .local/roots.pem, which git ignores and compose mounts read-only
# into the worker and the receiver. Both refuse to start without a readable file: they verify the
# counterparties' signatures (buyers' receipts, received documents) against these roots. Its key is thrown away as soon as the
# certificate is made, so this root vouches for nothing: no counterparty chains to it. Which authorities a
# deployment trusts (DGII's, INDOTEL's certification entities) is the owner's to decide (Owner item 7); this file
# is the local stack's alone, never a deployment's.
# Run once before `docker compose -f deploy/local/compose.yaml up`; a file that exists is kept; `--new` replaces
# it. The file is written whole or not at all (a temporary file, moved into place). Needs openssl and mv.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/.local"

fail() {
  printf 'roots.sh: %s\n' "$*" >&2
  exit 1
}

case "${1:-}" in
  "") ;;
  --new) rm -f "$OUT/roots.pem" ;;
  *) fail "unknown argument (the only one is --new)" ;;
esac

if [ -s "$OUT/roots.pem" ]; then
  printf 'roots.sh: kept %s/roots.pem (--new replaces it)\n' "$OUT"
  exit 0
fi
command -v openssl > /dev/null || fail "openssl is needed"
mkdir -p "$OUT"
temporary=".roots.$$"
key=".roots-key.$$"
cd "$OUT"  # relative names: a native openssl on Windows reads no POSIX path
trap 'rm -f "$temporary" "$key"' EXIT
umask 077
# Git Bash on Windows would read the subject (/C=...) as a path to convert: it is not one.
MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes -keyout "$key" -out "$temporary" -days 3650 \
  -sha256 -subj "/C=DO/O=Ledger local stack/CN=Ledger local development root" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
  > /dev/null 2>&1 || fail "openssl could not make the certificate"
[ -s "$temporary" ] || fail "could not write the certificate"
chmod 0644 "$temporary"  # a certificate, public: the worker (another user) reads it through the mount
mv -f "$temporary" roots.pem
printf 'roots.sh: wrote a development root to %s/roots.pem\n' "$OUT"
