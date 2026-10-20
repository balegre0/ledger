# ledger-fiscal

Enrollments in fiscal channels, routing of commercial documents to them, and their status mirror (spec §3.2,
§7.6; plan `docs/superpowers/plans/2026-10-19-v2-m3b-fiscal.md`, Tasks 11, 20, 25, 31).

- Owns schema `fiscal`: `enrollments` (row-level security scope `tenant`; one enrollment that is not disabled
  per company, mode and channel; never deleted). Id prefix `enr`; permissions `fiscal.enrollments.read` and
  `fiscal.enrollments.manage` (owner, admin).
- Public surface: `FiscalApi` ([`api.py`](src/ledger_fiscal/api.py)) for modules; fiscal-kit's
  `CompanyDirectory` for channels (Decision 4: which company a tax id names on a channel's environment, a
  company's names, and identity's permissions for the channel's routes); event `fiscal.enrollment.changed`
  (public `enrollment.updated`); HTTP under `/v1/fiscal`, mounted by `api`.
- Enrollments (`domain/enrollments.py`): `pending` while the channel requires something (`currently_due`, or a
  `disabled_reason` of its own), `active` once it requires nothing, `disabled` for good. An environment serves
  one mode (`ChannelDef.mode_of`): a live enrollment goes to live environments only, a test one never.
  A live enrollment stays pending until the authority evidence is recorded (Task 31); an active one stays
  active (Decision 31). Reading one enrollment asks its channel again and keeps what changed.
- Error codes are prefixed `fiscal_` (`fiscal_channel_unknown`, `fiscal_environment_unknown`,
  `fiscal_environment_not_allowed`, `fiscal_enrollment_exists` with `existing_id`,
  `fiscal_enrollment_not_found`, `fiscal_disable_reason_invalid`): `channel_unknown` is the platform's.
- Assembly: `assembly.py` (`MODULE`, entry point `ledger.modules: fiscal`). Channels are read at call time
  through a `ChannelRegistry`.
