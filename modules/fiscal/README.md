# ledger-fiscal

Enrollments in fiscal channels, routing of commercial documents to them, and their status mirror (spec §3.2,
§7.6; plan `docs/superpowers/plans/2026-10-19-v2-m3b-fiscal.md`, Tasks 11, 20, 25, 31).

- Owns schema `fiscal`: `enrollments` (row-level security scope `tenant`; one enrollment that is not disabled
  per company, mode and channel; never deleted; `review_due` when a channel says its requirements changed) and
  `routings` (scope `tenant`; one per document and channel, never changed). Id prefix `enr`; permissions `fiscal.enrollments.read` and
  `fiscal.enrollments.manage` (owner, admin).
- Public surface: `FiscalApi` ([`api.py`](src/ledger_fiscal/api.py)) for modules; fiscal-kit's
  `CompanyDirectory` for channels (Decision 4: which company a tax id names on a channel's environment, a
  company's names, and identity's permissions for the channel's routes); event `fiscal.enrollment.changed`
  (public `enrollment.updated`); HTTP under `/v1/fiscal`, mounted by `api`.
- Enrollments (`domain/enrollments.py`): `pending` while the channel requires something (`currently_due`, or a
  `requirements.disabled_reason` of its own), `active` once it requires nothing, `disabled` for good (the client's
  `disabled: {reason, at}`, beside what the last review left). A channel must be in force today where its
  regime keeps its days, and of a regime that governs the company's jurisdiction. An environment serves one mode
  (`ChannelDef.mode_of`): a live enrollment goes to live environments only, a test one never. A live enrollment
  stays pending until the authority evidence is recorded (Task 31); an active one stays active (Decision 31).
  Reading one enrollment asks its channel again and keeps what changed; a channel that cannot answer now leaves
  it as stored.
- Routing (Task 20): fiscal implements invoicing's `IssuePolicy` and `Renderer` (`application/issuing.py`,
  `rendering.py`). `check` objects `fiscal_enrollment_pending` for a pending enrollment covering the document
  (Decision 5), passes each routing channel's objections (`Channel.check`) through, and objects `field_unknown`
  to an undeclared `fiscal.<regime>` field no channel routes. `after_issue` routes the issued document to every
  active enrollment covering it, within the request's budget: invoicing's mirror gets `pending` (version 1)
  first, then the routing is kept, the channel numbers (and signs, in time) the draft, and its status is
  mirrored at the channel's version + 1. Consumers: `fiscal.commercial_documents` (outside, the recovery path
  for every issued document), `fiscal.channel_documents` (outside, the mirror, unordered),
  `fiscal.requirements` (inside, marks enrollments for review and wakes parked routings),
  `fiscal.certificates` (outside, Task 25: a certificate installed marks the company's enrollments in each mode
  it serves for review and resumes their channels' documents parked for a certificate); job `fiscal.route`
  (a routing whose channel had no number to give, parked `range_missing`).
- Error codes are prefixed `fiscal_` (`fiscal_channel_unknown`, `fiscal_channel_not_in_force`,
  `fiscal_channel_not_for_jurisdiction`, `fiscal_environment_unknown`, `fiscal_environment_not_allowed`,
  `fiscal_enrollment_exists` with `existing_id`, `fiscal_enrollment_not_found`, `fiscal_disable_reason_invalid`):
  `channel_unknown` is the platform's.
- Assembly: `assembly.py` (`MODULE`, entry point `ledger.modules: fiscal`). Channels are read at call time
  through a `ChannelRegistry`.
