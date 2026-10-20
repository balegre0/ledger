# Runbook: a company's signing certificate is expiring

## What happened

The signer tells a company its active certificate is about to expire: for each threshold of
`LEDGER_SIGNER_EXPIRY_NOTICE_DAYS` (default `30,14,7,1,0` days before `not_after`) a `signer.expiry_notice` job,
planned when the certificate was installed, publishes `signer.certificate.expiring` (public
`certificate.expiring`) with the certificate's id, its `not_after` and the threshold, if the certificate is still
active then. A certificate installed with ten days left is told `14` at once. A retired or replaced certificate is
never announced.

Once it expires, the signer refuses to sign with it: a document whose moment plus the signing skew
(`LEDGER_SIGNER_SIGNING_SKEW_SECONDS`, 300 s at least) falls past `not_after` is `signer_window_not_covered`, so
signatures stop a few minutes before the certificate's own end. Every signature the company's documents need
is refused from then on, until a valid certificate is installed.

## Who to tell

- The company's owners and administrators (identity's memberships), through the contact the customer
  registered: only they may install a certificate (`signer.certificates.manage`). Say which certificate (its
  `crt_…` id), which modes it serves, and when it ends (`not_after`, UTC). Never ask for the bundle or its
  password by mail or ticket: the customer imports it themselves.
- At `7` days or fewer with no replacement installed, the account's owner at our side as well: the customer's
  invoicing stops at `0`.

## How the customer replaces it

The new certificate is imported through the api while the old one still serves; installing it takes the old
one's modes, and the old one is retired `replaced` once it serves none. No signature fails in between.

```http
POST /v1/certificates?modes=live&modes=test
Authorization: Bearer <a token of a member with signer.certificates.manage>
Ledger-Company: <company id>
Idempotency-Key: <a new key>
Ledger-Certificate-Password: <base64 of the bundle's password, as UTF-8>
Content-Type: application/x-pkcs12

<the PKCS #12 bundle, at most 2 MiB>
```

- A request in test mode installs for `test` alone; `live` (alone or with `test`) needs a request in live mode
  (`certificate_modes_not_allowed` otherwise).
- 422 `certificate_*`: the bundle is refused as a whole (wrong password, not RSA 2048+, not a signing
  certificate of its key, not valid now and for the signing skew). Nothing is installed; fix and import again
  under a new key.
- 503 `signer_unavailable`: see [signer-unavailable.md](signer-unavailable.md). A retry with the same
  `Idempotency-Key` is safe; an import that may have happened is confirmed with `GET /v1/certificates`.
- Then `GET /v1/certificates`: the new certificate active for the modes, the old one `retired` (`replaced`).

## Never

- Never handle a customer's bundle or password, and never install one for them.
- Every proxy, WAF and APM agent in front of the api redacts `Ledger-Certificate-Password` (ADR 0010).
- Production cannot run the signer yet (the only KMS is the local key file, refused outside `local` and
  `test`): until a cloud KMS exists, this runbook applies to the local stack only.
