#!/bin/sh
# Writes the local stack's trusted roots for channel do-ecf-v1 (LEDGER_DO_ECF_TRUSTED_ROOTS) into .local/roots.pem,
# which git ignores and compose mounts read-only into the worker and the receiver. Both refuse to start without a
# readable file: they verify the counterparties' signatures (buyers' receipts, received documents) against these
# roots. It holds two self-signed certification authorities:
# - a development root, its key thrown away as soon as the certificate is made: it vouches for nothing, no
#   counterparty chains to it;
# - the fake DGII's root (its emisor-receptor simulator signs its receipts under it), whose certificate and key
#   are kept in .local/fake-dgii-root.pem, which compose mounts read-only into the fake-dgii container alone
#   (LEDGER_FAKE_DGII_ROOT). It vouches for the fake's simulator and nothing else; readable by any user of this
#   machine (the container's user is another), as befits a key of the local stack's fake.
# Which authorities a deployment trusts (DGII's, INDOTEL's certification entities) is the owner's to decide (Owner
# item 7); these files are the local stack's alone, never a deployment's.
# Run once before `docker compose -f deploy/local/compose.yaml up`; files that exist are kept; `--new` replaces them.
# Each file is written whole or not at all (a temporary file, moved into place). Needs openssl, cat and mv.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/.local"

fail() {
  printf 'roots.sh: %s\n' "$*" >&2
  exit 1
}

case "${1:-}" in
  "") ;;
  --new) rm -f "$OUT/roots.pem" "$OUT/fake-dgii-root.pem" ;;
  *) fail "unknown argument (the only one is --new)" ;;
esac

if [ -s "$OUT/roots.pem" ] && [ -s "$OUT/fake-dgii-root.pem" ]; then
  printf 'roots.sh: kept %s/roots.pem and fake-dgii-root.pem (--new replaces them)\n' "$OUT"
  exit 0
fi
command -v openssl > /dev/null || fail "openssl is needed"
mkdir -p "$OUT"
development=".development.$$"
development_key=".development-key.$$"
fake=".fake-dgii.$$"
fake_key=".fake-dgii-key.$$"
roots=".roots.$$"
fake_root=".fake-dgii-root.$$"
cd "$OUT"  # relative names: a native openssl on Windows reads no POSIX path
trap 'rm -f "$development" "$development_key" "$fake" "$fake_key" "$roots" "$fake_root"' EXIT
umask 077

root() {  # root <subject> <certificate> <key>
  # Git Bash on Windows would read the subject (/C=...) as a path to convert: it is not one.
  MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes -keyout "$3" -out "$2" -days 3650 \
    -sha256 -subj "$1" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    > /dev/null 2>&1 || fail "openssl could not make the certificate"
  [ -s "$2" ] || fail "could not write the certificate"
}

root "/C=DO/O=Ledger local stack/CN=Ledger local development root" "$development" "$development_key"
root "/C=DO/O=Ledger local stack/CN=Ledger local fake DGII root" "$fake" "$fake_key"
cat "$development" "$fake" > "$roots"
cat "$fake" "$fake_key" > "$fake_root"
# Certificates are public, and the fake's key is the local stack's fake's: the containers' user (another) reads them.
chmod 0644 "$roots" "$fake_root"
mv -f "$fake_root" fake-dgii-root.pem
mv -f "$roots" roots.pem
printf 'roots.sh: wrote the development root and the fake DGII'"'"'s root to %s/roots.pem\n' "$OUT"
