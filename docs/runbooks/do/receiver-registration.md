# Runbook: the receiver, and registering a company's URLs with DGII

Other emitters, and DGII itself during certification, send a company's e-CF and commercial approvals to the
company's **receiver** (spec §3.8; the `receiver` app, `ledger-receiver`, port 8103 on a host run). DGII lists
each electronic receiver's URLs in its directory, per taxpayer and environment; an issuer looks them up there
and posts to them.

## The receiver's URLs

One base per company and environment (Decision 15, plan Owner item 2):

```
https://<receiver host>/do-ecf-v1/<environment>/<RNC or cédula>
```

`<receiver host>` is `LEDGER_DO_ECF_RECEIVER_PUBLIC_URL` (an `https` origin, no path), which the api, the
worker and the receiver read. Until it is set every do-ecf-v1 enrollment requires it (`receiver_url`) and none
activates. `<environment>` is `testecf`, `certecf` or `ecf`; paths are case-insensitive, as DGII's are. Under
the base the receiver serves DGII's paths:

| DGII's field | Path under the base |
|---|---|
| URL Recepción | `/fe/recepcion/api/ecf` |
| URL Aprobación Comercial | `/fe/aprobacioncomercial/api/ecf` |
| URL Autenticación (optional, ours is required) | `/fe/autenticacion/api/semilla`, `/fe/autenticacion/api/validacioncertificado` |

Whether DGII's directory holds bases or full endpoints is spike finding F5 (TesteCF settles it:
[testecf.md](testecf.md)): register what F5 says, the base in each field or the full path of each.

## Registering them (the company does, per environment)

1. **Certification (CerteCF):** in the certification portal's application form, then at Pasos 7 and 8, the
   CerteCF base ([certecf.md](certecf.md)); at Paso 12 the production (`ecf`) URLs.
2. **After certification:** in the OFV, "Mantenimiento Directorio FE": URL Recepción e-CF, URL Aprobación
   Comercial, URL Autenticación.
3. Check: DGII's `consultadirectorio` for the company's RNC lists the URLs (TesteCF and production; CerteCF
   publishes no directory).

A company receives only in an environment it is enrolled in, in the mode that environment serves; anything
else, an unknown RNC included, is a bare 404 (rate-limited per environment and RNC: 10 a second, 50 at once).

## In front of the receiver

The receiver is the internet-facing app. Behind a TLS-terminating proxy (its own ingress host, spec §13):

- set `LEDGER_RECEIVER_FORWARDED_HOPS` to the number of proxies in front of it, so each client is rate-limited
  by its own address (an IPv6 client by its /64);
- the proxy appends `X-Forwarded-For`: the receiver then answers 404 to `/livez`, `/readyz` and `/metrics`
  (probes are for the cluster, not the internet); it serves no OpenAPI document and no `/v1` route;
- bodies: a reception up to 2 MiB, a validation or an approval 64 KiB; the proxy's limit must allow 2 MiB.

It needs `LEDGER_DO_ECF_TRUSTED_ROOTS` (read at start; restart it after the file changes), the signer (to sign
each receipt as the receiving company), the database and the archive (`/archive` locally: every document is
kept raw before it is read, every receipt after it is signed).

## What it logs

Each e-CF reception logs one info line, `do_ecf.reception`, with the receiving company's id (`company_id`),
the `environment` and the `outcome`, and nothing of the document, its parties or its sender:

| `outcome` | Answer |
|---|---|
| `received` | receipt *received* (ARECF `Estado` 0), the document recorded and announced |
| `again` | the same bytes again: the receipt they were given |
| `specification`, `signature`, `duplicate`, `not_this_receiver` | receipt *not received*, DGII's reasons 1, 2, 3 and 4 |
| `no_receipt` | 400: nothing a receipt can name |
| `not_the_sender` | 401: a document of another issuer than the token's sender |

A reception that fails (the archive or the signer unavailable) logs no `do_ecf.reception`; its error answers
503. A burst of `signature` for one company usually means its senders' authority is missing from the trusted
roots; of `not_this_receiver`, that an issuer has the wrong RNC registered for the buyer.

## Its housekeeping job

`do_ecf.purge_receiver_credentials` runs every hour (in the worker) and deletes the receiver's expired seeds
and tokens. If it dies (`ledger_jobs_finished_total{kind="do_ecf.purge_receiver_credentials",outcome="dead"}`
rising), nothing breaks at once: expired seeds and tokens are refused anyway, the tables only grow. Look at
`last_error_code` in `do_ecf.jobs`; each hour's slot is a job of its own, so the next one runs once the cause
is gone and purges what the dead one left.

## Memory: DGII's schemas per inspection thread

Each document received is read and checked against DGII's XSDs off the event loop (`asyncio.to_thread`, the
default thread pool: `min(32, CPUs + 4)` threads). Compiled schemas are kept **per thread**
(`ledger_fiscal_kit.xml.schemas.SchemaSet`, a `threading.local`), so every thread that inspects a format
compiles that format's schema once: the receiver holds up to threads × formats compiled schemas (the ten e-CF
types, the ACECF, the seed), with lxml's own memory for each. Size the receiver's memory limit after a warm-up
that sends every format through every thread (watch the process's resident memory, `process_resident_memory_bytes`
on `/metrics`, until it levels), not from a cold start; more CPUs mean more threads, so the same image needs
more memory on a larger node. The api and the worker do the same for what they sign and receive back.
