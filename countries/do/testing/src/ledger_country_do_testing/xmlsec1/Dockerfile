# xmlsec1, the reference implementation of XML Signature, for the tests that verify the DO pack's signatures
# (ledger_country_do_testing.xmlsec). Base image pinned by digest; packages come from snapshot.debian.org at the
# base image's own date (the snapshot its sources name), and xmlsec1 by version: every machine, on any day,
# installs the same bytes and verifies with the same libxml2 canonicalization and the same OpenSSL. The
# snapshot's Release files are past their validity, hence Check-Valid-Until off; signatures are still checked
# against the image's Debian keyring. Not a runtime image: tests only.
FROM debian:trixie-20260918-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
ARG SNAPSHOT=20260918T000000Z
RUN printf '%s\n' \
      'Types: deb' \
      "URIs: http://snapshot.debian.org/archive/debian/${SNAPSHOT}" \
      'Suites: trixie trixie-updates' \
      'Components: main' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp' \
      '' \
      'Types: deb' \
      "URIs: http://snapshot.debian.org/archive/debian-security/${SNAPSHOT}" \
      'Suites: trixie-security' \
      'Components: main' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp' \
      > /etc/apt/sources.list.d/debian.sources \
 && apt-get -o Acquire::Check-Valid-Until=false update \
 && apt-get install --yes --no-install-recommends xmlsec1=1.2.41-1+b1 \
 && rm -rf /var/lib/apt/lists/* \
 && xmlsec1 --version
CMD ["sleep", "infinity"]
