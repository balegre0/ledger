# ledger-invoicing

Commercial documents: invoices, credit and debit notes, their lines and totals (spec §7.5; plan
`docs/superpowers/plans/2026-10-17-v2-m3a-invoicing.md`).

- Owns schema `invoicing` (`0001`: the chassis's tables and a document's four; `0002`: the fiscal mirror,
  `fiscal_statuses`; all under the tenant's row-level security) and id prefix `inv`, and declares the
  `invoicing.invoices.*` permissions.
- Depends on `identity` (the company, its establishments and what a caller may do there, through
  `IdentityApi`).
- HTTP: `/v1/invoices` (`adapters/http`): create (`Idempotency-Key`, `Ledger-Mode`), list, read, issue and void
  (`If-Match`), the company in `Ledger-Company`. Its OpenAPI snapshot is `openapi.json` (`uv run poe sync`).
  An `Idempotency-Key` is the caller's per organization, mode and route, whatever the company: the same key
  sent for another company's request is 422 `idempotency_key_reused`, so an integrator acting for several
  companies uses a key per company. A malformed body is 422 before the permission is judged (it tells nothing).
  A retried `POST` (or a twin that waited) answers its stored status and headers with the document as it is now
  (M3b Decision 23): its `fiscal[]` may have grown since. `GET /v1/invoices/{id}/representation?channel=`
  answers the printed form a `Renderer` renders (Decision 22), for a reader of the company's documents.
- Wire `fiscal[]` (§9.8): one entry per channel, by channel key: `{channel, environment, status, version,
  messages[{code, message, lang}], <regime>: {...the channel's details}}`. Country fields under
  `fiscal.<regime>` that the regime does not declare (a channel's: `modification_code`, `withholdings`, nested
  one level deeper) are kept for the `IssuePolicy` chain to judge; with no chain they are 422 `field_unknown`
  (Decision 24).
- In-process API (`api.py`, §3.4): `InvoicingApi.document(call, invoice) -> InvoiceInfo`, bound under
  `INVOICING_API` by `application/facade.py` (`@facade`: it opens its own unit, never inside the caller's). The
  call names the tenant read (a company in a mode: a request's `Call` or a `SystemCall`); another tenant's
  document is `InvoiceNotFoundError`. An `InvoiceInfo` carries the document as kept, tax ids in clear (inside the tenant's
  boundary, §7.3); its `repr` shows its id, kind, status and version, a party's its tax id masked.
  `apply_fiscal_status(SystemCall, invoice, FiscalStatusInfo, version) -> Applied` keeps a channel's status in
  the fiscal mirror when newer than the one kept for that channel (`STALE` otherwise: redeliveries are
  harmless), under a share of the document's lock, and announces it; the document's own version is untouched.
  A document a channel accepted is not voided (409 `invoice_not_voidable`).
- Extension points (ADR 0011), declared, none required, read at call time, implemented by `fiscal` (M3b Task
  20): `IssuePolicy` (`check` before the unit, objections concatenated in chain order, 422 `invalid_document`
  with `errors[]`; `after_issue` after the commit, in chain order within `ISSUE_BUDGET` = 1.5 s, a timeout or
  failure logged by type and passed over) and `Renderer` (the first to answer renders; none: 404
  `representation_unavailable`). An implementation's coroutines are `@facade`s, and a route of its own that
  shows a document requires `invoicing.invoices.read` itself.
- Events (`events.py`, through the outbox in the unit of the change): `invoicing.invoice.issued` v1 `{kind,
  direction, corrects}` and `invoicing.invoice.voided` v1 `{kind, direction}`, scoped to the document's
  organization, company and mode, subject the document, `aggregate_version` its version;
  `invoicing.invoice.fiscal_updated` v1 `{channel, status, version}` (public `invoice.fiscal_updated`),
  `aggregate_version` the status's version in its channel. A draft, a refused request and a replay announce
  nothing; payloads carry no party, tax id or amount.
- Assembly: `assembly.py` (`MODULE`, entry point `ledger.modules: invoicing`).
