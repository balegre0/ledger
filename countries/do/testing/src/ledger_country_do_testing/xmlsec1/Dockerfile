# xmlsec1, the reference implementation of XML Signature, for the tests that verify the DO pack's signatures
# (ledger_country_do_testing.xmlsec). Base image pinned by digest and package by version, so every machine
# verifies with the same libxml2 canonicalization and the same OpenSSL. Not a runtime image: tests only.
FROM debian:trixie-20260918-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
RUN apt-get update \
 && apt-get install --yes --no-install-recommends xmlsec1=1.2.41-1+b1 \
 && rm -rf /var/lib/apt/lists/* \
 && xmlsec1 --version
CMD ["sleep", "infinity"]
