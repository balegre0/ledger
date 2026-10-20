# ledger-fiscal-kit

The minimal contract the `fiscal` module, the channels and the signer agree on (spec §8.4), under the rule of
two: only what two of them share lives here. It sits between the countries and modules above it and the
platform below it (import-linter contract 1).

- `channels`: the `Channel` Protocol fiscal routes to (requirements, check, issue, status, resume, printed
  representation), its `ChannelDef` (built from the manifest entry; `mode_of(environment)`: an environment
  serves one mode, and `allows` admits a document only to an environment of its own mode), what it answers
  (`Requirements`, `Objection`, `StatusUpdate`, `Issued`, `Representation`), `EnrollmentState`,
  `ResumeReason`, and the `ChannelRegistry` over the platform's assembled channels, read at call time.
- `drafts`: `FiscalDraft`, a commercial document at a version as a channel's input (the issuer and the
  counterparty, as invoicing's seller and buyer; the establishment and its time zone, lines with their
  resolved taxes, the kernel's totals, references, the regime's fields), and `DocumentRef`. No I/O; it reads
  back from the platform codec's JSON (`FiscalDraft.read`). Mappings it holds (`fields`, and a
  `StatusUpdate`'s `details`) are deep copies, read-only at their top level only.
- `numbering`: the `NumberingPolicy` port and the `FiscalNumber` it allocates; allocation stays in the packs.
- `ports`: `SignerPort` (sign a document or an authentication seed; the certificate's validity), its request
  and answer types, the errors that cross it and its key `SIGNER_PORT`. The signer module's client implements
  it; channels call it. A key never crosses it. `CompanyDirectory` (`COMPANY_DIRECTORY`; implemented by
  fiscal, also the channels' `PermissionSource`), `ArchivePort` (`ARCHIVE_PORT`; bound by the app over
  `platform.blobs`; its write-once contract is in its docstring), `archive_key` (the kind is the channel's
  word: DO defines its own `ArchiveKind`) and the archive's errors.
- `events`: what every channel publishes, under names no channel owns: `DocumentUpdated`
  (`fiscal_channel.document.updated`), `ReceivedDocumentRecorded`, `RequirementsChanged`; fiscal reads the
  first and the last back from their `RawEvent` with the class's `read`.
- `status`: what an authority's answer means, country-neutral: `FiscalStatus`, `Severity`, `StatusEntry`, the
  exhaustive `StatusMap` a channel maps its authority's documented codes with, and its messages kept verbatim
  within bounds (`AuthorityMessage`, `kept`).
- `xml.hardened`: reading XML that comes from outside (no DTD, no entity, no network, UTF-8 only, size limits).
- `xml.c14n`: a document's canonical form (C14N 1.0 without comments; exclusive on request).
- `xml.schemas`: an authority's XSDs pinned to their SHA-256 with the least corrections that make them compile
  (`PinnedSchema`, `Correction`), and documents validated against them (`SchemaSet`). The pins and
  corrections are the channel's; a country pack binds them (DO: `adapters.xml.schema_set.DO_SCHEMAS`).
- `xml.dsig`: the enveloped XMLDSig profile DGII's documents use (C14N 1.0, SHA-256, RSA-SHA256), the rule of
  which certificates may sign, and signing in two phases (`prepare`, then `Prepared.complete` with a signature
  value made elsewhere) so that a remote backend sees a digest, never the document. A private key is a value a
  caller passes in: only the signer process holds one (spec rule 7).
- No schema, no settings, no entry point.
