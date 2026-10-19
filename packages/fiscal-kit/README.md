# ledger-fiscal-kit

The minimal contract the `fiscal` module, the channels and the signer agree on (spec §8.4), under the rule of
two: only what two of them share lives here. It sits between the countries and modules above it and the
platform below it (import-linter contract 1).

- `ports`: `SignerPort` (sign a document or an authentication seed; the certificate's validity), its request
  and answer types, the errors that cross it and its key `SIGNER_PORT`. The signer module's client implements
  it; channels call it. A key never crosses it.
- `xml.hardened`: reading XML that comes from outside (no DTD, no entity, no network, UTF-8 only, size limits).
- `xml.dsig`: the enveloped XMLDSig profile DGII's documents use (C14N 1.0, SHA-256, RSA-SHA256), the rule of
  which certificates may sign, and signing in two phases (`prepare`, then `Prepared.complete` with a signature
  value made elsewhere) so that a remote backend sees a digest, never the document. A private key is a value a
  caller passes in: only the signer process holds one (spec rule 7).
- No schema, no settings, no entry point.
