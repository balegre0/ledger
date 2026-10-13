# Milestone 3: Vault Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The vault service (`services/vault`, package `ledger_vault`): custody of every company's signing certificate (PKCS#12 uploaded once, validated, its private key sealed by envelope encryption under a KMS), XML signatures exactly as DGII verifies them (`SignXml`, `SignSeed`) over a gRPC `ledger.vault.v1.VaultService`, an append-only signing audit, certificate events (uploaded, rotated, retired, expiring 30/7/1 days), and identity's public `PUT`/`GET /v1/companies/{company_id}/certificate` that reaches it. Private keys never leave vault (§2 rule 7).

**Architecture:** Platform first: calls between services (a gRPC channel as a service resource, the caller's name in the user agent, the trace carried in metadata with client and server spans, the deadline of an incoming call bounding the outgoing ones, retries of idempotent methods) and one migration runner for every service (identity moves onto it). Contracts: `ledger.vault.v1.VaultService`, `ledger.events.vault.v1` and the refusal codes (`ledger_contracts.vault`). The test toolkit gains a PKI of the test's own, xmlsec1 in a container (the independent verifier) and LocalStack's KMS. The service is hexagonal (§6): `domain` (stdlib + kernel: holder conventions of the Dominican authorities, admission rules, custody and rotation, expiry thresholds, key material) ← `application` (`VaultCommands`, `VaultQueries` over ports; no transaction is held across a KMS call or a signature) ← `adapters` (PKCS#12 reader and trust store, envelope custody over a `KeyManagementService` port with AWS KMS and local adapters, the DGII XMLDSig signer on a bounded thread pool, PostgreSQL repositories, the outbox) and `api` (gRPC servicer, the consumer of `identity.company.deactivated`, the expiry worker), wired by `main` into one chassis `Service`.

**Tech Stack:** Python 3.15, cryptography 50.0.2, lxml 6.1.3 (+ types-lxml 2026.2.16), boto3/botocore 1.43.111 (+ types-boto3-lite[kms] 1.43.111), signxml 5.1.0 (tests only), grpcio 1.84, SQLAlchemy 2.1 / psycopg 3.3 / Alembic 1.20, PostgreSQL 18.6, NATS 2.12.15, LocalStack 4.14.0, xmlsec1 1.2.41 (Debian trixie, tests only), testcontainers 4.15.

**Spec:** `docs/superpowers/specs/2026-10-11-ledger-platform-design.md` (milestone 3 of §16: "certificate upload, envelope encryption, signing over gRPC, audit"; §2 rule 7, §3 vault row, §4.2, §5.1, §6, §7, §9, §11, §14).

## Global Constraints

- Python `>=3.15`; every package's `pyproject.toml` says `requires-python = ">=3.15"`.
- Code, comments, docs and messages in English.
- Zero `# type: ignore`, `# pyright: ignore`, `# noqa`, `# pragma: no cover`, and no tool directive comment: the `suppressions` gate step fails on any. Generated protobuf code is excluded by configuration.
- `uv run poe check` passes after every task: ruff format + ruff (`select = ["ALL"]`), basedpyright (`all`, warnings fail), mypy `--strict` per package, import-linter, deptry per package, typos, `buf lint`, pytest with **100 % branch coverage**, warnings as errors.
- Raise errors with their message built inside the error class; a message never echoes a bundle, a password, a document, a subject or a tax id (§9 "leaks through telemetry"). Exception constants that hold library text are named so that ruff's S105 does not read them as passwords (`_NOT_OPENED`, not `_WRONG_PASSWORD`).
- Type-only imports use `lazy from ... import ...`. lxml objects are never named in an annotation (`etree._Element` is private to basedpyright `all`): code that handles them keeps them as inferred locals (Task 8 shows the shape). `@contextmanager` functions are annotated `Generator[T]`, not `Iterator[T]`: the typeshed basedpyright 1.40 ships deprecates the `Iterator` overload (verified; re-check Milestone 2's `ledger_testing` and identity modules when Task 2 touches them and fix any `Iterator` there the same way).
- The one `cast` of the milestone is in `ledger_vault.adapters.kms.aws` (boto3 builds clients at runtime; `types-boto3-lite` types `Session.client` as `Any`, and the full `types-boto3` overloads leave every service whose stubs are not installed `Unknown`, which basedpyright `all` refuses: verified). A second, in a test helper (`verify_with_signxml`), types signxml's partly unannotated `verify` through a protocol; both are explained where they stand.
- Plaintext key material lives in `KeyMaterial` (`ledger_vault.domain.keys`) and is wiped when the operation that needed it ends; Python's limits are stated there, not hidden.
- Docker must be running: tests use PostgreSQL `postgres:18.6`, NATS `nats:2.12.15`, LocalStack `localstack/localstack:4.14.0@sha256:3ebc37595918b8accb852f8048fef2aff047d465167edd655528065b07bc364a`, and an xmlsec1 image built from `libs/testing/src/ledger_testing/xmlsec1/Dockerfile` (`debian:trixie-20260918-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a`, `xmlsec1=1.2.41-1+b1`).
- Dependency floors introduced here, verified on 2026-10-12 to install on CPython 3.15 (pure Python, `abi3` or `cp315` wheels): `cryptography>=50.0.2`, `lxml>=6.1.3`, `boto3>=1.43.111`, `botocore>=1.43.111`; dev: `types-lxml>=2026.2.16`, `types-boto3-lite[kms]>=1.43.111`, `signxml>=5.1.0`. `uv.lock` pins the exact versions.
- Work on branch `milestone-3-vault`, created from `main` once Milestone 2 (identity) has been merged; one commit per task; never push or rewrite `main`; commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Milestone 2 is being executed while this plan is written; Milestone 1's final-review fix wave is landing too.** This plan builds on the surface the Milestone 2 plan declares (`docs/superpowers/plans/2026-10-12-milestone-2-identity.md`): `Database.transaction(scope)`, `UnitOfWork.scope`, `CHASSIS_METADATA`/`CHASSIS_POLICIES`, `ledger_platform.grpc_client` (`call_unary`, `RemoteError` and kinds, `ERROR_CODE_KEY`), `ledger_platform.routes`, `Idempotency`, `ApiContext`, `ledger_identity.adapters.postgres.migrate`, `ledger_testing.{containers,logs,waiting,issuer,api}`, `Permission.CERTIFICATE_MANAGE`; and on the fix wave's `Service(..., checks={name: check}, resources=(factory, ...))`, `Events(workers=...)`, `ServiceLimits` (`pool_size`, gRPC grace), the built-in chassis-table readiness check and the gRPC RED metrics of `ErrorInterceptor`. Every task that uses one of these says **Re-check before starting** with the names to confirm; where the landed name or signature differs, the task uses the landed one and says so in its report (a renamed parameter is an edit of this plan's code, never a reason to change the chassis or identity back).

## Review Focus

1. **Signatures DGII accepts, proven by a verifier that shares no code with the signer**: every document shape (namespaces on the root, a default namespace, nested defaults, comments, processing instructions, character references, CR and tabs in attributes, astral characters) signed by vault verifies in xmlsec1 1.2.41 with the chain checked, and in signxml; the `Signature` element is byte-for-byte the golden one; a tampered document fails both. Tests in Task 8.
2. **Hostile input**: oversized bundles and documents refused unread; a DOCTYPE (billion laughs, external entities) refused without being read; a document already signed refused; garbage, a wrong password, a bundle without key, without certificate or with another certificate's key, an EC key, a 1024-bit key, a key usage without signature, expired, not yet valid, an untrusted chain, a subject naming nobody or another taxpayer: each a precise `INVALID` code that never echoes the input. Tests in Tasks 4, 6, 8 and 11.
3. **Key custody**: the private key is stored only as AES-256-GCM ciphertext under a per-certificate data key wrapped by the KMS with an encryption context naming organization, company and certificate; a sealed key moved to another certificate, another company or altered does not open (`sealed_key_tampered` / `kms_refused`); a KMS that is down or throttling answers `UNAVAILABLE`; the local KMS is refused in production; plaintext is wiped after each operation; no key, password or data key in a log, a span or an error. Tests in Tasks 4, 6, 7 and 9.
4. **Tenancy and concurrency of custody**: a certificate of company A never signs for company B (row-level security, repositories filtering by tenant, composite foreign keys); two uploads or rotations at once serialize on the company (an advisory lock), exactly one wins, a retried one is a no-op; the audit is append-only (an update or delete fails in PostgreSQL itself). Tests in Tasks 9, 10 and 11.
5. **Identity's route**: `certificate.manage` required, `Idempotency-Key` required and replayed byte for byte, `If-Match` required to replace (428 without, 412 when stale), vault's refusals answered with their own codes (422), vault down is 503, an inactive company is 409, and nothing of the bundle or password is logged or stored by identity. Tests in Task 13.

## Decisions

The owner's eight decisions for this milestone are binding; these are the ones this plan adds or the facts it found, each with its reason.

1. **What "the subject RNC must match the company" can mean in the Dominican Republic (research).** DGII requires a *Certificado Digital de Personas Físicas para Procedimiento Tributario*, which "permite identificar a la persona que actuará en representación del contribuyente" and "debe estar a nombre del contribuyente o su representante autorizado" (DGII, *Preguntas frecuentes e-CF generales*, 2.3.1 and 16.1.4; issuers Viafirma/Avansi, Digifirma (CCPSD) and Novofirma (Novosit), 13.1.2). Viafirma's published profile for it (*CP-VIAFIRMA-DO-PI-TAX-SW* v2.0, 2023, §3.1.2/§7.1.2) puts the holder's identity in `serialNumber` (2.5.4.5) as `IDCDO-<cédula>` or `PAS<country>-<passport>` (ETSI EN 319 412-1); its electronic seal (*CP-VIAFIRMA-ELECTRONIC-SEAL* v2.0) puts the organization in `organizationIdentifier` (2.5.4.97) as `VATDO-<RNC>`; Avansi's pre-2021 profiles (*PC-AVS-PR* 1.2) put the bare cédula in `serialNumber`. Digifirma's and Novofirma's profiles are not published. So for a company with an RNC the certificate is normally a *person's*, and its cédula is not the RNC. vault therefore reads the holder with ordered strategies (`EtsiOrganizationIdentifier`, `EtsiPersonIdentifier`, `BareSerialNumber`; a new convention is one more reader) and binds it: the taxpayer's own identifier → `taxpayer`; a natural person's other identifier → `representative` (DGII, which holds the authorizations, rejects an e-CF signed by an unauthorized person); an organization certificate of another RNC → `certificate_holder_mismatch`; a subject naming nobody → `certificate_holder_unknown`. `LEDGER_ACCEPT_REPRESENTATIVES=false` restores the literal reading of the owner's Decision 3 (taxpayer only) without a code change. **The owner should rule on the default (true).**
2. **The permission of identity's route is `certificate.manage`**, not `company.manage`: Milestone 2's kernel defines `Permission.CERTIFICATE_MANAGE` for exactly this ("upload and replace the company's signing certificate (milestone 3)"), and today the same roles hold both (owner, admin). Reading the certificate needs `company.read`.
3. **Upload and rotation are two RPCs, both idempotent by the certificate.** `UploadCertificate` installs a company's first certificate (refused with `certificate_already_active` when it has one); `RotateCertificate` names the generation it replaces (`certificate_changed` otherwise, a compare-and-set) and retires the old one, which is kept with its sealed key. Installing the certificate that is already active is a no-op (`created=false`), so a retry after a lost answer is harmless. Each certificate a company installs takes the next *generation*, which identity answers as the `ETag` of the company's certificate: `PUT` without `If-Match` uploads (428 when one exists), with `If-Match` rotates (412 when stale).
4. **Changes of a company's custody serialize on a transaction-scoped advisory lock** (`pg_advisory_xact_lock(hashtextextended(company_id::text, 0))`), taken first by upload, rotation and retirement; a partial unique index (one `active` per company) and `UNIQUE (company_id, generation)` remain as a backstop. A row lock cannot serialize the first upload (there is no row yet).
5. **No transaction spans a network call or a signature.** Opening a bundle, the KMS call that seals it, and signing happen outside any transaction; the unit that installs a certificate, and the one that writes a signature's audit, are short. A signature is handed back only after its audit row committed: no signature leaves without its record.
6. **vault trusts the caller for the company scope and the tax id** (the accepted risk of Milestone 2's Decision 14, kept consistent): a request names organization, company and, for an upload, the tax id identity holds and the subject that asked; vault's gRPC port is never published outside the services' network. The calling service names itself in the gRPC user agent (`identity/0.1.0 grpc-python-asyncio/...`, set by the platform's channel); the audit keeps that name as `caller`, *unauthenticated until the mesh*.
7. **Holders are personal data.** The holder's name and identifier are stored (the company's own members see them, masked to the last three characters on the wire as `holder_id_hint`) and never logged or put in an error; the audit stores no document, only its SHA-256 and an optional reference (an e-NCF).
8. **The expiry announcements read a table outside row-level security on purpose** (`expiry_watch`: certificate id, tenant, generation, `not_after`, smallest announcement made; no secret, no personal data), as the outbox is: a worker runs without a scope, and a policy exception for it would expose certificates to any unscoped transaction. Each announcement is claimed and published in the company's own transaction. The worker runs a pass at start and every `LEDGER_EXPIRY_CHECK_SECONDS` (default 3600, not "daily": each threshold is announced once whenever it is crossed, so a finer pass only makes it timelier); it also exports `ledger_vault_active_certificates_expiring{within_days}` for the §11 alert.
9. **Signing is CPU-bound and runs on a bounded thread pool** (`SigningPool`, `LEDGER_SIGNING_THREADS`, default 4): at most N jobs at once, the rest waiting in asyncio where a call's deadline cancels the wait. Measured on the probe machine (CPython 3.15, a 12.5 KB e-CF with 60 lines): parse + C14N + RSA-2048 + serialization p50 0.59 ms, p95 0.81 ms; RSA key loading without the 30 ms consistency check (`unsafe_skip_rsa_key_validation`: checked at upload, and the GCM tag proves the bytes are those) 0.004 ms. The end-to-end target of Task 14 is **p95 `SignXml` < 50 ms** over gRPC with PostgreSQL and LocalStack's KMS on a dev machine.
10. **The data-key cache exists and is off** (`LEDGER_DATA_KEY_CACHE_SECONDS=0`; at most 300, at most 1000 keys). Rule 7 is that keys never leave vault; a data key kept in vault's memory does not leave it, what it costs is a longer window in which a memory dump would yield it. It is keyed by the KMS key, the wrapped key *and the context*, wiped on expiry, eviction and stop, and a hit still has to pass the GCM tag bound to the certificate. Private keys are never cached. Turn it on only when KMS latency or quota measurably threatens the signing SLO.
11. **AWS KMS through boto3, called on a thread.** Raw HTTP with our own SigV4 would avoid a dependency and the one `cast`, but it would make vault implement AWS's credential chain (web identity on Kubernetes, container credentials, instance profiles) and signing code no LocalStack test could prove (LocalStack does not verify signatures). aiobotocore pulls aiohttp and pins botocore tightly. boto3's client is thread-safe; a call is milliseconds of I/O on the default executor.
12. **LocalStack is pinned to 4.14.0** (2026-02-26), the last release that starts without an account: every release since 2026.3 exits with "License activation failed" without `LOCALSTACK_AUTH_TOKEN` (verified with 2026.08.3). Its KMS does all vault asks (keys, aliases, data keys, encryption contexts, disabled keys; verified). If a future need forces a newer one, the alternative is moto's server, not a CI secret.
13. **The signer is our own, on lxml and cryptography; signxml is only a second verifier.** DGII's shape (the `Signature` in the default namespace, one `enveloped-signature` transform) is exact only when we write it; and lxml's C14N of an element inside a document writes a spurious `xmlns=""` on grandchildren under a default namespace (lxml 6.1.3 with libxml2 2.11.9 on Windows and 2.14.6 on Linux; signxml issue 193), which xmlsec1 rejects: the signer canonicalizes `SignedInfo` as a standalone element declaring exactly what is in scope where it lands (verified against xmlsec1 on seven document shapes). signxml verifies DGII-shaped signatures only with `excise_empty_xmlns_declarations` on, and resolves `URI=""` to the root element (so a document with a processing instruction outside the root fails there but passes xmlsec1, the reference): it is used on documents without nodes outside the root. xmlsec1 is the authority.
14. **One migration runner for every service** (`ledger_platform.migrations`): identity's `migrate.py` (Milestone 2) and vault's would otherwise be the same hundred lines. Identity moves onto it in Task 3, keeping its console script and its tests' meaning.
15. **Retirement on company deactivation.** vault consumes `identity.company.deactivated` (§3, as Milestone 2 amended it) with the durable consumer `vault-company-deactivations`, filter `identity.company.deactivated` (no wildcard, so the typed event dispatcher Milestone 1 deferred "before the first wildcard durable consumer" is still not needed): the active certificate is retired (`company_deactivated`), its watch dropped, `vault.certificate.retired` announced. A deactivated company signs nothing.
16. **Deferred, recorded in the spec and the README (Task 16):** tracing of SQL statements (to the performance pass after Milestone 2, which profiles the database paths); revocation checks (OCSP/CRL) of uploaded certificates and of active ones, to the certification milestone with DGII's own requirement; the KMS key's rotation (AWS rotates the key material yearly by itself; re-wrapping data keys under a new key id is a later operation); authentication of gRPC callers (the mesh, as in Milestone 2).

## File map

```
ledger/
├── pyproject.toml  uv.lock  Tiltfile  README.md  .github/workflows/ci.yml
├── deploy/local/{compose.yaml, vault.env, identity.env, postgres/init.sql, localstack/init-kms.sh}
├── docs/superpowers/specs/2026-10-11-ledger-platform-design.md
├── libs/contracts/proto/ledger/vault/v1/vault.proto
├── libs/contracts/proto/ledger/events/vault/v1/events.proto
├── libs/contracts/src/ledger/vault/v1/vault_pb2{,_grpc}.py{,i}                  (generated)
├── libs/contracts/src/ledger/events/vault/v1/events_pb2{,_grpc}.py{,i}          (generated)
├── libs/contracts/src/ledger_contracts/vault.py
├── libs/contracts/tests/test_vault_contracts.py
├── libs/testing/src/ledger_testing/{pki, xmlsec, containers}.py, xmlsec1/Dockerfile
├── libs/testing/tests/{test_pki, test_xmlsec, test_localstack}.py
├── libs/platform/src/ledger_platform/{grpc_client, grpc_server, tracing, service}.py   (modified)
├── libs/platform/src/ledger_platform/migrations.py                                       (new)
├── libs/platform/tests/{test_calls_between_services, test_migrations}.py                  (new)
├── services/identity/src/ledger_identity/adapters/postgres/{migrate.py, migrations/env.py} (modified)
├── services/identity/src/ledger_identity/{settings, main, openapi}.py                     (modified)
├── services/identity/src/ledger_identity/application/certificates.py                      (new)
├── services/identity/src/ledger_identity/adapters/vault.py                                (new)
├── services/identity/src/ledger_identity/api/{certificates, models, rest}.py              (new / modified)
├── services/identity/openapi.json                                                         (regenerated)
├── services/identity/tests/{conftest, test_certificates_api}.py                           (modified / new)
└── services/vault/
    ├── pyproject.toml  Dockerfile
    ├── benchmarks/sign_latency.py
    ├── src/ledger_vault/{__init__, settings, main}.py, py.typed
    ├── src/ledger_vault/domain/{__init__, ids, errors, holder, keys, certificate, custody, signing, events, expiry}.py
    ├── src/ledger_vault/application/{__init__, ports, commands, queries}.py
    ├── src/ledger_vault/adapters/{__init__, pool, bundle, custody, xml, events}.py
    ├── src/ledger_vault/adapters/kms/{__init__, local, aws}.py
    ├── src/ledger_vault/adapters/postgres/{__init__, tables, repositories, units, migrate}.py
    ├── src/ledger_vault/adapters/postgres/migrations/{__init__, env}.py, versions/{__init__, r0001_vault}.py
    ├── src/ledger_vault/api/{__init__, rpc, deactivations, expiry}.py
    └── tests/{conftest, test_domain, test_application, test_bundle, test_custody, test_kms_aws,
               test_xml, test_migrations, test_repositories, test_rpc, test_deactivations, test_expiry,
               test_settings}.py, golden/{signer.key.pem, signer.cert.pem, ecf.xml, ecf.signed.xml}
```

---

### Task 1: Contracts: the vault service, its events and its refusal codes

**Files:**
- Create: `libs/contracts/proto/ledger/vault/v1/vault.proto`, `libs/contracts/proto/ledger/events/vault/v1/events.proto`, `libs/contracts/src/ledger_contracts/vault.py`
- Generate (committed): `libs/contracts/src/ledger/vault/v1/vault_pb2.py`, `vault_pb2.pyi`, `vault_pb2_grpc.py`, `vault_pb2_grpc.pyi`; `libs/contracts/src/ledger/events/vault/v1/events_pb2.py`, `events_pb2.pyi`, `events_pb2_grpc.py`, `events_pb2_grpc.pyi`
- Test: `libs/contracts/tests/test_vault_contracts.py`

**Interfaces:**
- Consumes: `Envelope` (`ledger_contracts.envelope`), the `google/protobuf/timestamp.proto` well-known type (buf resolves it without a dependency).
- Produces: package `ledger.vault.v1`, service `VaultService` with `UploadCertificate`, `RotateCertificate`, `GetCertificate`, `ListCertificates`, `SignXml`, `SignSeed` (each `<Rpc>Request`/`<Rpc>Response`); `Certificate{id, organization_id, company_id, generation, status, holder_name, holder_kind, holder_id_hint, holder_binding, issuer_name, serial_number, fingerprint_sha256, not_before, not_after, key_bits, uploaded_at, uploaded_by, retired_at, retirement_reason}`; enums `CertificateStatus`, `HolderKind`, `HolderBinding`, `RetirementReason`. Package `ledger.events.vault.v1`: `CertificateUploaded`, `CertificateRotated`, `CertificateRetired`, `CertificateExpiring`. `ledger_contracts.vault`: `CertificateRefusal(StrEnum)` (the eleven `INVALID_ARGUMENT` codes of an upload or rotation), `CERTIFICATE_ALREADY_ACTIVE`, `CERTIFICATE_CHANGED`, `NO_ACTIVE_CERTIFICATE`, `CERTIFICATE_NOT_FOUND` (the `FAILED_PRECONDITION`/`NOT_FOUND` codes callers act on), and the RPC paths `UPLOAD_CERTIFICATE`, `ROTATE_CERTIFICATE`, `GET_CERTIFICATE`, `LIST_CERTIFICATES`, `SIGN_XML`, `SIGN_SEED`, `IDEMPOTENT_METHODS` (the two reads).

- [ ] **Step 1: Write the failing tests**

`libs/contracts/tests/test_vault_contracts.py`:

```python
from datetime import UTC, datetime
from typing import Final

import pytest
from google.protobuf.timestamp_pb2 import Timestamp
lazy from google.protobuf.message import Message

from ledger.events.vault.v1.events_pb2 import (
    CertificateExpiring,
    CertificateRetired,
    CertificateRotated,
    CertificateUploaded,
)
from ledger.vault.v1 import vault_pb2
from ledger.vault.v1.vault_pb2 import (
    CERTIFICATE_STATUS_ACTIVE,
    HOLDER_BINDING_REPRESENTATIVE,
    HOLDER_KIND_PERSON,
    Certificate,
    SignXmlRequest,
    UploadCertificateRequest,
)
from ledger_contracts.envelope import Envelope
from ledger_contracts.vault import (
    GET_CERTIFICATE,
    IDEMPOTENT_METHODS,
    LIST_CERTIFICATES,
    SIGN_XML,
    CertificateRefusal,
)
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id

TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
EVENTS: Final[tuple[Message, ...]] = (
    CertificateUploaded(),
    CertificateRotated(),
    CertificateRetired(),
    CertificateExpiring(),
)


def moment() -> Timestamp:
    stamp = Timestamp()
    stamp.FromDatetime(datetime(2027, 10, 12, 12, 0, tzinfo=UTC))
    return stamp


def test_the_vault_service_answers_custody_and_signing() -> None:
    service = vault_pb2.DESCRIPTOR.services_by_name["VaultService"]
    assert [method.name for method in service.methods] == [
        "UploadCertificate",
        "RotateCertificate",
        "GetCertificate",
        "ListCertificates",
        "SignXml",
        "SignSeed",
    ]
    for method in service.methods:
        assert (method.input_type.name, method.output_type.name) == (f"{method.name}Request", f"{method.name}Response")


def test_the_rpc_paths_and_the_idempotent_reads() -> None:
    assert SIGN_XML == "/ledger.vault.v1.VaultService/SignXml"
    assert IDEMPOTENT_METHODS == (GET_CERTIFICATE, LIST_CERTIFICATES)


@pytest.mark.parametrize("event", EVENTS, ids=[type(event).__name__ for event in EVENTS])
def test_every_vault_event_is_a_message_of_the_versioned_events_package(event: Message) -> None:
    assert event.DESCRIPTOR.full_name == f"ledger.events.vault.v1.{type(event).__name__}"


def test_a_vault_event_travels_in_an_envelope_and_back() -> None:
    event = CertificateExpiring(
        certificate_id=str(new_id()),
        organization_id=str(TENANT.organization),
        company_id=str(TENANT.company),
        generation=3,
        not_after=moment(),
        days_left=7,
    )
    read = Envelope.from_headers(*_wire(Envelope.wrap(event, source="vault", tenant=TENANT)))
    assert read.unwrap(CertificateExpiring) == event


def _wire(envelope: Envelope) -> tuple[dict[str, str], bytes]:
    return envelope.headers(), envelope.data


def test_requests_and_answers_round_trip() -> None:
    certificate = Certificate(
        id=str(new_id()),
        organization_id=str(TENANT.organization),
        company_id=str(TENANT.company),
        generation=2,
        status=CERTIFICATE_STATUS_ACTIVE,
        holder_name="JUAN PEREZ",
        holder_kind=HOLDER_KIND_PERSON,
        holder_id_hint="********678",
        holder_binding=HOLDER_BINDING_REPRESENTATIVE,
        not_after=moment(),
    )
    messages: tuple[Message, ...] = (
        certificate,
        UploadCertificateRequest(bundle=b"\x30\x82", password="p", tax_id="101850043", requested_by="2001"),
        SignXmlRequest(document=b"<ECF/>", reference="E310000000001"),
    )
    for message in messages:
        assert type(message).FromString(message.SerializeToString()) == message
    assert not certificate.HasField("retired_at")


def test_the_refusal_codes_are_the_words_clients_match_on() -> None:
    assert {refusal.value for refusal in CertificateRefusal} == {
        "certificate_too_large",
        "certificate_unreadable",
        "certificate_password_wrong",
        "certificate_bundle_incomplete",
        "certificate_key_unsupported",
        "certificate_not_for_signing",
        "certificate_not_yet_valid",
        "certificate_expired",
        "certificate_untrusted",
        "certificate_holder_unknown",
        "certificate_holder_mismatch",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest libs/contracts -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger.events.vault'`.

- [ ] **Step 3: Write the contracts**

`libs/contracts/proto/ledger/vault/v1/vault.proto`:

```proto
syntax = "proto3";

package ledger.vault.v1;

import "google/protobuf/timestamp.proto";

// Custody of companies' signing certificates, and signatures made with them (spec §3, §9). Private keys
// never leave vault: nothing here returns one.
//
// Every request names the organization and the company it is about; vault answers inside that company only.
// Callers are other services, authenticated by the mesh (until it is deployed, by nobody: the port is never
// published outside the services' network). The caller names itself in the user agent.
//
// Errors carry their stable code in the trailing metadata ledger-error-code:
//   INVALID_ARGUMENT  a certificate refused (ledger_contracts.vault.CertificateRefusal), a document refused
//                     (document_too_large, document_malformed, document_doctype_refused,
//                     document_already_signed, seed_malformed), invalid_id, invalid_tax_id,
//                     invalid_subject, invalid_reference
//   FAILED_PRECONDITION certificate_already_active, certificate_changed, no_active_certificate,
//                     certificate_expired (the active one)
//   NOT_FOUND         certificate_not_found
//   UNAVAILABLE       kms_unavailable, and the chassis's own
service VaultService {
  // Install a company's first certificate. The same certificate again changes nothing (created=false).
  rpc UploadCertificate(UploadCertificateRequest) returns (UploadCertificateResponse);
  // Replace the company's active certificate, which is retired and kept. The same certificate again changes
  // nothing.
  rpc RotateCertificate(RotateCertificateRequest) returns (RotateCertificateResponse);
  // One of the company's certificates, active or retired. Idempotent.
  rpc GetCertificate(GetCertificateRequest) returns (GetCertificateResponse);
  // The company's certificates, newest first, at most 100. Idempotent.
  rpc ListCertificates(ListCertificatesRequest) returns (ListCertificatesResponse);
  // Sign a document with the company's active certificate, as DGII verifies it: an enveloped XMLDSig
  // signature, inclusive C14N 1.0, RSA-SHA256, SHA-256 digest, Reference URI="", the certificate in KeyInfo.
  rpc SignXml(SignXmlRequest) returns (SignXmlResponse);
  // Sign DGII's authentication seed (SemillaModel) the same way.
  rpc SignSeed(SignSeedRequest) returns (SignSeedResponse);
}

message UploadCertificateRequest {
  string organization_id = 1;
  string company_id = 2;
  // The company's tax id as identity holds it, digits only: the certificate must name it, or name a
  // natural person who may represent the company.
  string tax_id = 3;
  // The principal that asked (the identity provider's subject), kept with the certificate.
  string requested_by = 4;
  // The PKCS#12 file (.p12, .pfx): the private key, its certificate and, optionally, the chain.
  bytes bundle = 5;
  string password = 6;
}

message UploadCertificateResponse {
  Certificate certificate = 1;
  // False when the certificate was already the company's active one: nothing changed.
  bool created = 2;
}

message RotateCertificateRequest {
  string organization_id = 1;
  string company_id = 2;
  string tax_id = 3;
  string requested_by = 4;
  bytes bundle = 5;
  string password = 6;
  // The generation of the active certificate this one replaces.
  int64 replaces_generation = 7;
}

message RotateCertificateResponse {
  Certificate certificate = 1;
  // The certificate retired; empty when the certificate was already active (nothing changed).
  string retired_certificate_id = 2;
}

message GetCertificateRequest {
  string organization_id = 1;
  string company_id = 2;
  string certificate_id = 3;
}

message GetCertificateResponse {
  Certificate certificate = 1;
}

message ListCertificatesRequest {
  string organization_id = 1;
  string company_id = 2;
}

message ListCertificatesResponse {
  // Newest first; the active one, if any, has status CERTIFICATE_STATUS_ACTIVE.
  repeated Certificate certificates = 1;
}

message SignXmlRequest {
  string organization_id = 1;
  string company_id = 2;
  // UTF-8 XML without a DOCTYPE and without a signature; at most 1 MiB unless configured otherwise.
  bytes document = 3;
  // What the caller calls the document (an e-NCF such as E310000000001), kept with the audit; optional.
  string reference = 4;
}

message SignXmlResponse {
  bytes signed_document = 1;
  string certificate_id = 2;
  string signature_id = 3;
  // SHA-256 of the document as received: what the audit keeps of it.
  bytes document_sha256 = 4;
}

message SignSeedRequest {
  string organization_id = 1;
  string company_id = 2;
  // The SemillaModel DGII handed out.
  bytes seed = 3;
}

message SignSeedResponse {
  bytes signed_seed = 1;
  string certificate_id = 2;
  string signature_id = 3;
}

// A certificate in a company's custody.
message Certificate {
  string id = 1;
  string organization_id = 2;
  string company_id = 3;
  // One more with every certificate the company installs: the version of the company's certificate.
  int64 generation = 4;
  CertificateStatus status = 5;
  // The holder's common name as the certificate states it.
  string holder_name = 6;
  HolderKind holder_kind = 7;
  // The holder's identifier (cedula, RNC or passport) with all but its last three characters masked.
  string holder_id_hint = 8;
  HolderBinding holder_binding = 9;
  // The common name of the authority that issued it.
  string issuer_name = 10;
  // Lowercase hexadecimal.
  string serial_number = 11;
  // SHA-256 of the certificate (DER), lowercase hexadecimal.
  string fingerprint_sha256 = 12;
  google.protobuf.Timestamp not_before = 13;
  google.protobuf.Timestamp not_after = 14;
  int32 key_bits = 15;
  google.protobuf.Timestamp uploaded_at = 16;
  string uploaded_by = 17;
  // Unset while the certificate is active.
  google.protobuf.Timestamp retired_at = 18;
  RetirementReason retirement_reason = 19;
}

enum CertificateStatus {
  CERTIFICATE_STATUS_UNSPECIFIED = 0;
  CERTIFICATE_STATUS_ACTIVE = 1;
  CERTIFICATE_STATUS_RETIRED = 2;
}

enum HolderKind {
  HOLDER_KIND_UNSPECIFIED = 0;
  HOLDER_KIND_PERSON = 1;
  HOLDER_KIND_ORGANIZATION = 2;
}

// Why the holder may sign for the company.
enum HolderBinding {
  HOLDER_BINDING_UNSPECIFIED = 0;
  // The certificate names the company's own tax id.
  HOLDER_BINDING_TAXPAYER = 1;
  // A natural person acting for the company; DGII checks the taxpayer authorized them.
  HOLDER_BINDING_REPRESENTATIVE = 2;
}

enum RetirementReason {
  RETIREMENT_REASON_UNSPECIFIED = 0;
  RETIREMENT_REASON_ROTATED = 1;
  RETIREMENT_REASON_COMPANY_DEACTIVATED = 2;
}
```

`libs/contracts/proto/ledger/events/vault/v1/events.proto`:

```proto
syntax = "proto3";

package ledger.events.vault.v1;

import "google/protobuf/timestamp.proto";

// A company's first certificate was installed. Subject vault.certificate.uploaded.
message CertificateUploaded {
  string certificate_id = 1;
  string organization_id = 2;
  string company_id = 3;
  // The certificate's generation: the version of the company's certificate, which only grows.
  int64 generation = 4;
  google.protobuf.Timestamp not_after = 5;
}

// A certificate replaced the company's active one, retired and kept. Subject vault.certificate.rotated.
message CertificateRotated {
  string certificate_id = 1;
  string organization_id = 2;
  string company_id = 3;
  int64 generation = 4;
  google.protobuf.Timestamp not_after = 5;
  string retired_certificate_id = 6;
}

// The company's active certificate was retired without a replacement (the company was deactivated). Subject
// vault.certificate.retired.
message CertificateRetired {
  string certificate_id = 1;
  string organization_id = 2;
  string company_id = 3;
  int64 generation = 4;
}

// The company's active certificate expires within days_left days: 30, 7 or 1, each announced once. Subject
// vault.certificate.expiring.
message CertificateExpiring {
  string certificate_id = 1;
  string organization_id = 2;
  string company_id = 3;
  int64 generation = 4;
  google.protobuf.Timestamp not_after = 5;
  int32 days_left = 6;
}
```

`libs/contracts/src/ledger_contracts/vault.py`:

```python
"""What ``VaultService``'s contract holds beyond its ``.proto``: its error codes and its paths.

A refusal of a certificate is ``INVALID_ARGUMENT`` with one of ``CertificateRefusal``'s codes. A caller that
relays it to a client (identity's API) answers with the same code; the gRPC client of the platform reads any
``INVALID_ARGUMENT`` as a defect of the caller, so such a caller translates these codes itself.
"""

from enum import StrEnum
from typing import Final

_SERVICE: Final = "/ledger.vault.v1.VaultService"
UPLOAD_CERTIFICATE: Final = f"{_SERVICE}/UploadCertificate"
ROTATE_CERTIFICATE: Final = f"{_SERVICE}/RotateCertificate"
GET_CERTIFICATE: Final = f"{_SERVICE}/GetCertificate"
LIST_CERTIFICATES: Final = f"{_SERVICE}/ListCertificates"
SIGN_XML: Final = f"{_SERVICE}/SignXml"
SIGN_SEED: Final = f"{_SERVICE}/SignSeed"
IDEMPOTENT_METHODS: Final = (GET_CERTIFICATE, LIST_CERTIFICATES)
"""The methods a channel may retry (``ledger_platform.grpc_client.Channel``): they change nothing."""

CERTIFICATE_ALREADY_ACTIVE: Final = "certificate_already_active"
CERTIFICATE_CHANGED: Final = "certificate_changed"
NO_ACTIVE_CERTIFICATE: Final = "no_active_certificate"
CERTIFICATE_NOT_FOUND: Final = "certificate_not_found"


class CertificateRefusal(StrEnum):
    """Why vault refused a certificate on upload or rotation (``INVALID_ARGUMENT``)."""

    TOO_LARGE = "certificate_too_large"
    UNREADABLE = "certificate_unreadable"
    WRONG_PASSWORD = "certificate_password_wrong"
    INCOMPLETE = "certificate_bundle_incomplete"
    KEY_UNSUPPORTED = "certificate_key_unsupported"
    NOT_FOR_SIGNING = "certificate_not_for_signing"
    NOT_YET_VALID = "certificate_not_yet_valid"
    EXPIRED = "certificate_expired"
    UNTRUSTED = "certificate_untrusted"
    HOLDER_UNKNOWN = "certificate_holder_unknown"
    HOLDER_MISMATCH = "certificate_holder_mismatch"
```

- [ ] **Step 4: Generate, lint and run the tests**

Run: `uv run poe proto && uv run poe proto-lint && uv run pytest libs/contracts -q --no-cov`
Expected: the eight generated files appear; `buf lint` prints nothing; PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add libs/contracts
git commit -m "feat(contracts): vault service, vault events and certificate refusal codes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The test toolkit: a PKI of the test's own, xmlsec1, LocalStack's KMS

**Files:**
- Create: `libs/testing/src/ledger_testing/pki.py`, `libs/testing/src/ledger_testing/xmlsec.py`, `libs/testing/src/ledger_testing/xmlsec1/Dockerfile`
- Modify: `libs/testing/src/ledger_testing/containers.py`, `libs/testing/pyproject.toml`, `_typos.toml`
- Test: `libs/testing/tests/test_pki.py`, `libs/testing/tests/test_xmlsec.py`, `libs/testing/tests/test_localstack.py`

**Interfaces:**
- Consumes: `testcontainers` (`DockerContainer`, `DockerImage`, `LogMessageWaitStrategy`), `cryptography`.
- Produces: `ledger_testing.pki`: `OPENING_PHRASE` (the default bundle password), `DEFAULT_BITS`, `TAX_PROCEDURES`, `SIGNING`, `ENCIPHERING_ONLY`, `type PrivateKey = RSAPrivateKey | EllipticCurvePrivateKey | Ed25519PrivateKey`, `rsa_key(bits=2048)`, `ec_key()`, `ed25519_key()`, subjects `person(cedula="00112345678", *, name, country="DO")`, `passport_holder(number, *, name, country)`, `organization(rnc="101850043", *, name)`, `legacy_person(cedula="001-1234567-8", *, name)`, `nobody(name)`; `Issued(certificate, key)` with `pem()`; `Authority` with `root(name, *, now=None)`, `intermediate(name)`, `issue(subject, *, key=None, not_before=None, not_after=None, usage=SIGNING)`, `certificate`, `issued`; `bundle(issued, *, chain=(), password=OPENING_PHRASE, certificate=True, key=True) -> bytes`; `pem(*certificates) -> bytes`. `ledger_testing.xmlsec`: `XMLSEC_IMAGE`, `Verdict(verified, output)`, `Xmlsec.started() -> AbstractContextManager[Xmlsec]`, `Xmlsec.verify(signed, *, trusted, untrusted, at) -> Verdict`. `ledger_testing.containers`: `LOCALSTACK_IMAGE`, `LOCALSTACK_PORT = 4566`, `localstack_kms() -> DockerContainer`, `localstack_address(container) -> str`.

Re-check before starting: `libs/testing/src/ledger_testing/containers.py` as Milestone 2 left it (append, keep its constants); any `@contextmanager` there annotated `Iterator[...]` becomes `Generator[...]` (Global Constraints).

- [ ] **Step 1: Dependencies**

`libs/testing/pyproject.toml` — add to `dependencies`: `"cryptography>=50.0.2"`.

`_typos.toml` — add under `[default.extend-words]` (Spanish words of DGII's and the authorities' documents that the plan and the code quote):

```toml
semilla = "semilla"
valor = "valor"
fecha = "fecha"
firmado = "firmado"
```

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`libs/testing/tests/test_pki.py`:

```python
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from ledger_testing.pki import (
    ENCIPHERING_ONLY,
    OPENING_PHRASE,
    Authority,
    bundle,
    ec_key,
    legacy_person,
    nobody,
    organization,
    passport_holder,
    pem,
    person,
)


def serial(name: x509.Name, oid: x509.ObjectIdentifier) -> str:
    return str(name.get_attributes_for_oid(oid)[0].value)


def test_subjects_follow_the_dominican_conventions() -> None:
    assert serial(person("40212345678"), NameOID.SERIAL_NUMBER) == "IDCDO-40212345678"
    assert serial(passport_holder("X1", country="ES"), NameOID.SERIAL_NUMBER) == "PASES-X1"
    assert serial(organization("130000012"), NameOID.ORGANIZATION_IDENTIFIER) == "VATDO-130000012"
    assert serial(legacy_person(), NameOID.SERIAL_NUMBER) == "001-1234567-8"
    assert not nobody().get_attributes_for_oid(NameOID.SERIAL_NUMBER)


def test_a_hierarchy_issues_certificates_that_bundle_and_open_again() -> None:
    root = Authority.root()
    intermediate = root.intermediate()
    issued = intermediate.issue(person())
    loaded = pkcs12.load_pkcs12(bundle(issued, chain=[intermediate.certificate]), OPENING_PHRASE.encode())
    assert loaded.cert is not None
    assert loaded.cert.certificate == issued.certificate
    assert [extra.certificate for extra in loaded.additional_certs] == [intermediate.certificate]
    assert issued.certificate.issuer == intermediate.certificate.subject
    assert intermediate.certificate.issuer == root.certificate.subject
    assert pem(root.certificate).startswith(b"-----BEGIN CERTIFICATE-----")
    assert issued.pem() == pem(issued.certificate)


def test_a_test_breaks_what_it_needs() -> None:
    root = Authority.root()
    past = datetime.now(UTC) - timedelta(days=10)
    issued = root.issue(nobody(), key=ec_key(), not_after=past, usage=ENCIPHERING_ONLY)
    assert isinstance(issued.key, EllipticCurvePrivateKey)
    assert issued.certificate.not_valid_after_utc == past.replace(microsecond=0)
    keyless = pkcs12.load_pkcs12(bundle(issued, key=False), OPENING_PHRASE.encode())
    assert keyless.key is None
```

`libs/testing/tests/test_xmlsec.py`:

```python
from datetime import UTC, datetime

from ledger_testing.pki import Authority, pem
from ledger_testing.xmlsec import Xmlsec


def test_xmlsec1_refuses_a_document_without_a_valid_signature() -> None:
    root = Authority.root()
    with Xmlsec.started() as xmlsec:
        verdict = xmlsec.verify(b"<a/>", trusted=pem(root.certificate), untrusted=b"", at=datetime.now(UTC))
    assert not verdict.verified
    assert "Signature" in verdict.output or "signature" in verdict.output
```

(Signatures that verify are Task 8's: the toolkit cannot sign without vault's signer.)

`libs/testing/tests/test_localstack.py`:

```python
import httpx

from ledger_testing.containers import LOCALSTACK_IMAGE, localstack_address, localstack_kms


def test_localstack_serves_its_kms_without_an_account() -> None:
    with localstack_kms() as container:
        health = httpx.get(f"{localstack_address(container)}/_localstack/health", timeout=5).json()
    assert health["services"]["kms"] in {"available", "running"}
    assert LOCALSTACK_IMAGE.startswith("localstack/localstack:4.14.0@sha256:")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest libs/testing -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_testing.pki'`.

- [ ] **Step 4: Implement**

`libs/testing/src/ledger_testing/pki.py`:

```python
"""Certificates for tests: authorities of the test's own, and the bundles a company uploads.

A test makes its own hierarchy (``Authority.root``, then ``intermediate``), so no test depends on a real
authority and a trust store holds exactly the roots a test names. Subjects follow the conventions of the
Dominican authorities (``person``, ``organization``, ``legacy_person``, ``passport_holder``); ``issue``
makes a certificate for any subject, key, validity and key usage a test needs, broken ones included, and
``bundle`` packs a key and certificates into the PKCS#12 file a company would upload.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, final
lazy from collections.abc import Sequence

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, Encoding, pkcs12
from cryptography.x509.oid import NameOID

OPENING_PHRASE: Final = "it's the bundle's password"
"""What opens every bundle a test makes, unless it names another password."""
DEFAULT_BITS: Final = 2048
TAX_PROCEDURES: Final = "QUALIFIED CERTIFICATE FOR NATURAL PERSON - TAX PROCEDURES"
"""The DN qualifier of Viafirma's certificate for tax procedures."""
SIGNING: Final = x509.KeyUsage(
    digital_signature=True,
    content_commitment=True,
    key_encipherment=True,
    data_encipherment=False,
    key_agreement=False,
    key_cert_sign=False,
    crl_sign=False,
    encipher_only=False,
    decipher_only=False,
)
"""The key usage of a certificate for tax procedures."""
ENCIPHERING_ONLY: Final = x509.KeyUsage(
    digital_signature=False,
    content_commitment=False,
    key_encipherment=True,
    data_encipherment=True,
    key_agreement=False,
    key_cert_sign=False,
    crl_sign=False,
    encipher_only=False,
    decipher_only=False,
)
"""A key usage that allows no signature."""
_CERTIFYING: Final = x509.KeyUsage(
    digital_signature=False,
    content_commitment=False,
    key_encipherment=False,
    data_encipherment=False,
    key_agreement=False,
    key_cert_sign=True,
    crl_sign=True,
    encipher_only=False,
    decipher_only=False,
)
_AUTHORITY_YEARS: Final = timedelta(days=3650)
_LEAF_DAYS: Final = timedelta(days=365)
_BACKDATED: Final = timedelta(days=1)

type PrivateKey = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey
"""The keys a test certificate has: RSA, which vault takes; P-256 and Ed25519, which it refuses."""


def rsa_key(bits: int = DEFAULT_BITS) -> rsa.RSAPrivateKey:
    """A new RSA key (tens of milliseconds at 2048 bits: a test that needs many makes them once)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def ec_key() -> ec.EllipticCurvePrivateKey:
    """A new P-256 key: a key vault refuses."""
    return ec.generate_private_key(ec.SECP256R1())


def ed25519_key() -> ed25519.Ed25519PrivateKey:
    """A new Ed25519 key: a key vault refuses."""
    return ed25519.Ed25519PrivateKey.generate()


def _name(*attributes: tuple[x509.ObjectIdentifier, str]) -> x509.Name:
    return x509.Name([x509.NameAttribute(oid, value) for oid, value in attributes])


def person(cedula: str = "00112345678", *, name: str = "JUAN PEREZ", country: str = "DO") -> x509.Name:
    """A natural person's certificate for tax procedures: ``serialNumber`` ``IDC<country>-<cedula>``."""
    given, _, family = name.partition(" ")
    return _name(
        (NameOID.COUNTRY_NAME, "DO"),
        (NameOID.DN_QUALIFIER, TAX_PROCEDURES),
        (NameOID.COMMON_NAME, name),
        (NameOID.SERIAL_NUMBER, f"IDC{country}-{cedula}"),
        (NameOID.GIVEN_NAME, given),
        (NameOID.SURNAME, family or given),
    )


def passport_holder(number: str = "AB1234567", *, name: str = "JANE ROE", country: str = "US") -> x509.Name:
    """A natural person identified by a passport: ``serialNumber`` ``PAS<country>-<number>``."""
    return _name(
        (NameOID.COUNTRY_NAME, "DO"),
        (NameOID.COMMON_NAME, name),
        (NameOID.SERIAL_NUMBER, f"PAS{country}-{number}"),
    )


def organization(rnc: str = "101850043", *, name: str = "FERRETERIA DUARTE SRL") -> x509.Name:
    """An organization's electronic seal: ``organizationIdentifier`` ``VATDO-<rnc>``."""
    return _name(
        (NameOID.COUNTRY_NAME, "DO"),
        (NameOID.ORGANIZATION_NAME, name),
        (NameOID.COMMON_NAME, name),
        (NameOID.ORGANIZATION_IDENTIFIER, f"VATDO-{rnc}"),
    )


def legacy_person(cedula: str = "001-1234567-8", *, name: str = "JUAN PEREZ") -> x509.Name:
    """A person as certificates older than ETSI's form name one: ``serialNumber`` the cédula itself."""
    return _name((NameOID.COUNTRY_NAME, "DO"), (NameOID.COMMON_NAME, name), (NameOID.SERIAL_NUMBER, cedula))


def nobody(name: str = "SOMEONE") -> x509.Name:
    """A subject that names nobody by a Dominican identifier."""
    return _name((NameOID.COUNTRY_NAME, "DO"), (NameOID.COMMON_NAME, name))


@final
@dataclass(frozen=True, slots=True)
class Issued:
    """A certificate and its private key."""

    certificate: x509.Certificate
    key: PrivateKey

    def pem(self) -> bytes:
        """The certificate in PEM."""
        return self.certificate.public_bytes(Encoding.PEM)


@final
class Authority:
    """A certification authority of a test's own: a root, or an intermediate a root issued."""

    __slots__ = ("issued",)

    def __init__(self, issued: Issued) -> None:
        self.issued = issued

    @property
    def certificate(self) -> x509.Certificate:
        """The authority's own certificate."""
        return self.issued.certificate

    @classmethod
    def root(cls, name: str = "LEDGER TEST ROOT CA", *, now: datetime | None = None) -> Authority:
        """A self-signed root, valid from a day before ``now`` for ten years."""
        key = rsa_key()
        subject = _name((NameOID.COUNTRY_NAME, "DO"), (NameOID.COMMON_NAME, name))
        moment = datetime.now(UTC) if now is None else now
        certificate = (
            _authority_builder(subject, subject, key, key, path_length=1)
            .not_valid_before(moment - _BACKDATED)
            .not_valid_after(moment + _AUTHORITY_YEARS)
            .sign(key, hashes.SHA256())
        )
        return cls(Issued(certificate, key))

    def intermediate(self, name: str = "LEDGER TEST QUALIFIED CERTIFICATES") -> Authority:
        """An authority this one issued, which issues end-entity certificates only."""
        key = rsa_key()
        subject = _name((NameOID.COUNTRY_NAME, "DO"), (NameOID.COMMON_NAME, name))
        certificate = (
            _authority_builder(subject, self.certificate.subject, key, self.issued.key, path_length=0)
            .not_valid_before(self.certificate.not_valid_before_utc)
            .not_valid_after(self.certificate.not_valid_after_utc)
            .sign(self.issued.key, hashes.SHA256())
        )
        return Authority(Issued(certificate, key))

    def issue(
        self,
        subject: x509.Name,
        *,
        key: PrivateKey | None = None,
        not_before: datetime | None = None,
        not_after: datetime | None = None,
        usage: x509.KeyUsage | None = SIGNING,
    ) -> Issued:
        """An end-entity certificate for ``subject``, valid from a day ago for a year unless told else."""
        own = rsa_key() if key is None else key
        now = datetime.now(UTC)
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self.certificate.subject)
            .public_key(own.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _BACKDATED if not_before is None else not_before)
            .not_valid_after(now + _LEAF_DAYS if not_after is None else not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.issued.key.public_key()),
                critical=False,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(own.public_key()), critical=False)
        )
        if usage is not None:
            builder = builder.add_extension(usage, critical=True)
        return Issued(builder.sign(self.issued.key, hashes.SHA256()), own)


def _authority_builder(
    subject: x509.Name,
    issuer: x509.Name,
    key: PrivateKey,
    issuer_key: PrivateKey,
    *,
    path_length: int,
) -> x509.CertificateBuilder:
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .add_extension(x509.BasicConstraints(ca=True, path_length=path_length), critical=True)
        .add_extension(_CERTIFYING, critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False
        )
    )


def bundle(
    issued: Issued | None,
    *,
    chain: Sequence[x509.Certificate] = (),
    password: str = OPENING_PHRASE,
    certificate: bool = True,
    key: bool = True,
) -> bytes:
    """``issued`` as the PKCS#12 file a company uploads, with ``chain``; leave out its key or certificate."""
    return pkcs12.serialize_key_and_certificates(
        b"ledger test",
        issued.key if issued is not None and key else None,
        issued.certificate if issued is not None and certificate else None,
        list(chain) or None,
        BestAvailableEncryption(password.encode()),
    )


def pem(*certificates: x509.Certificate) -> bytes:
    """``certificates`` one after the other in PEM: a trust store's file."""
    return b"".join(certificate.public_bytes(Encoding.PEM) for certificate in certificates)
```

`libs/testing/src/ledger_testing/xmlsec1/Dockerfile`:

```dockerfile
# xmlsec1, the reference implementation of XML Signature, for the tests that verify the signatures the
# services make (ledger_testing.xmlsec). The base image and the package are pinned, so every machine verifies
# with the same libxml2 canonicalization and the same OpenSSL. Not a runtime image: tests only.
FROM debian:trixie-20260918-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
RUN apt-get update \
 && apt-get install --yes --no-install-recommends xmlsec1=1.2.41-1+b1 \
 && rm -rf /var/lib/apt/lists/* \
 && xmlsec1 --version
CMD ["sleep", "infinity"]
```

`libs/testing/src/ledger_testing/xmlsec.py`:

```python
"""xmlsec1, the reference implementation of XML Signature, as the independent verifier of signatures (§14).

The services sign with lxml and cryptography; a test proves a signature right with an implementation that
shares none of that code: xmlsec1 1.2.41 of Debian trixie (libxml2's canonicalization over node sets,
OpenSSL), in a container built from ``xmlsec1/Dockerfile`` (Docker keeps the image, so it is built once per
machine). The document and the certificates travel in the command that verifies them, base64-encoded: no file
of the host is shared with the container, so a test runs the same on Windows and on Linux.
"""

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final
lazy from collections.abc import Generator
lazy from datetime import datetime

from testcontainers.core.container import DockerContainer
from testcontainers.core.image import DockerImage

XMLSEC_IMAGE: Final = "ledger-xmlsec1:1.2.41"
_CONTEXT: Final = Path(__file__).with_name("xmlsec1")
_VERIFY: Final = (
    'printf %s "$1" | base64 -d > /tmp/signed.xml'
    ' && printf %s "$2" | base64 -d > /tmp/trusted.pem'
    ' && printf %s "$3" | base64 -d > /tmp/untrusted.pem'
    " && exec xmlsec1 --verify --trusted-pem /tmp/trusted.pem --untrusted-pem /tmp/untrusted.pem"
    ' --verification-time "$4" /tmp/signed.xml'
)
"""Writes the three files from the script's arguments, then verifies: the key from the signature's
``KeyInfo``, its chain through the untrusted intermediates to a trusted root, at the time given."""
_TIME: Final = "%Y-%m-%d %H:%M:%S"


@final
@dataclass(frozen=True, slots=True)
class Verdict:
    """What xmlsec1 said of a signature: whether it holds, and its own words (for a failing test's report)."""

    verified: bool
    output: str


def _encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@final
class Xmlsec:
    """A running xmlsec1 container."""

    __slots__ = ("_container",)

    def __init__(self, container: DockerContainer) -> None:
        self._container = container

    @classmethod
    @contextmanager
    def started(cls) -> Generator[Xmlsec]:
        """xmlsec1, built if this machine lacks the image, for the length of the block."""
        with (
            DockerImage(path=_CONTEXT, tag=XMLSEC_IMAGE, clean_up=False),
            DockerContainer(XMLSEC_IMAGE) as container,
        ):
            yield cls(container)

    def verify(self, signed: bytes, *, trusted: bytes, untrusted: bytes, at: datetime) -> Verdict:
        """Whether ``signed`` holds an enveloped signature by a certificate that chains to ``trusted``.

        ``trusted`` and ``untrusted`` are PEM: the roots, and the intermediates between them and the
        certificate in the signature's ``KeyInfo``; ``at`` is when the chain must be valid.
        """
        arguments = [_encoded(signed), _encoded(trusted), _encoded(untrusted), at.strftime(_TIME)]
        result = self._container.exec(["sh", "-c", _VERIFY, "verify", *arguments])
        return Verdict(result.exit_code == 0, result.output.decode("utf-8", "replace"))
```

`libs/testing/src/ledger_testing/containers.py` — append (and add `LOCALSTACK_IMAGE`, `LOCALSTACK_PORT` beside the other images):

```python
LOCALSTACK_IMAGE: Final = (
    "localstack/localstack:4.14.0@sha256:3ebc37595918b8accb852f8048fef2aff047d465167edd655528065b07bc364a"
)
"""The last LocalStack that runs without an account: from 2026.3 the image refuses to start without an auth
token (Decision 12). Its KMS is all vault's tests ask of it: keys, aliases, data keys, encryption contexts."""
LOCALSTACK_PORT: Final = 4566


def localstack_kms() -> DockerContainer:
    """LocalStack with its KMS only, ready once it says so."""
    return (
        DockerContainer(LOCALSTACK_IMAGE)
        .with_env("SERVICES", "kms")
        .with_exposed_ports(LOCALSTACK_PORT)
        .waiting_for(LogMessageWaitStrategy("Ready."))
    )


def localstack_address(container: DockerContainer) -> str:
    """The endpoint URL a client reaches ``container``'s services by."""
    return f"http://{container.get_container_host_ip()}:{container.get_exposed_port(LOCALSTACK_PORT)}"
```

The Dockerfile is package data: `uv_build` ships every file under `src/ledger_testing/` (the image is built from the source tree, through `Path(__file__)`); confirm with `uv run python -c "import ledger_testing.xmlsec as x; print(x._CONTEXT.joinpath('Dockerfile').exists())"` (prints `True`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest libs/testing -q --no-cov`
Expected: PASS (the first run builds the xmlsec1 image, about a minute on a cold cache; later runs reuse it).

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`ledger_testing` included: `Issued.pem`, `passport_holder`, `legacy_person` and both branches of `bundle` are exercised above).

- [ ] **Step 7: Commit**

```bash
git add _typos.toml uv.lock libs/testing
git commit -m "test: a PKI of the test's own, xmlsec1 as independent verifier, LocalStack's KMS

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Platform: calls between services, and one migration runner for every service

**Files:**
- Create: `libs/platform/src/ledger_platform/migrations.py`
- Modify: `libs/platform/src/ledger_platform/grpc_client.py`, `libs/platform/src/ledger_platform/grpc_server.py`, `libs/platform/src/ledger_platform/tracing.py`, `libs/platform/src/ledger_platform/service.py`, `libs/platform/pyproject.toml`, `pyproject.toml` (root: layers), `services/identity/src/ledger_identity/adapters/postgres/migrate.py`, `services/identity/src/ledger_identity/adapters/postgres/migrations/env.py`
- Test: `libs/platform/tests/test_calls_between_services.py`, `libs/platform/tests/test_migrations.py`; modify `services/identity/tests/test_migrations.py`

**Interfaces:**
- Consumes: `call_unary`, `RemoteUnavailableError`, `ERROR_CODE_KEY` (`grpc_client`, M2 Task 8); `serve_grpc`, `ErrorInterceptor` (M1, with the fix wave's RED metrics); `Tracing` (M1); `Database`, `CHASSIS_METADATA`; identity's `migrate` (M2 Task 11).
- Produces: in `ledger_platform.grpc_client`: `Channel(target: str, *, caller: str, version: str, idempotent: Sequence[str] = ())` with `opened() -> AbstractAsyncContextManager[Channel]` (the service's resource) and `current() -> grpc.aio.Channel` (`ChannelClosedError` when not open); `call_unary(method, request, *, rpc, deadline=DEFAULT_DEADLINE, tracing: Tracing | None = None)` (the deadline is also bounded by the incoming call's remaining time; the active trace travels in `traceparent`; with `tracing`, a CLIENT span per call); `remaining_deadline() -> float | None`; `answering_within(seconds: float | None) -> AbstractContextManager[None]`; `current_caller() -> str | None`; `caller_of(user_agent: str | None) -> str | None`. In `ledger_platform.grpc_server`: `serve_grpc(*, port, register, grace=10.0, tracing: Tracing | None = None)`: every asynchronous call runs inside a SERVER span continuing the caller's `traceparent` (when `tracing` is given), with the caller's name and remaining deadline in context and `rpc`/`caller` in the log context. In `ledger_platform.tracing`: `Tracing.serving(rpc, traceparent)` and `Tracing.calling(rpc)` (context managers of SERVER and CLIENT spans recording the error type only). `ledger_platform.migrations`: `Migrations(scripts: str, *, owner: str)` with `upgrade(url, revision="head")`, `downgrade(url, revision="base")`, `head() -> str`, `async require_head(database, expected)`; `run_migrations(*metadata)` (an `env.py`'s whole body); `OfflineMigrationsError`, `MigrationUrlMissingError`, `SchemaNotCurrentError` (kind `UNAVAILABLE`, code `schema_not_current`), `InvalidMigrationsOptionError`. Identity's `migrate` keeps `SCRIPTS`, `upgrade`, `downgrade`, `head`, `require_head`, `main`, now one-line delegations.

Re-check before starting: `grpc_server.py` after the fix wave (the RED-metrics interceptor and the grace in `ServiceLimits`): the server span below goes *inside* `_Answering` (it sees the domain exception's type), and is a second guard around the behaviour, so it composes with whatever the fix wave added; `service.py`'s call of `serve_grpc` gains `tracing=tracing`. Identity's `migrate.py`, `env.py` and `test_migrations.py` as Milestone 2 left them: the names below are the ones its plan declares.

- [ ] **Step 1: Dependencies and layering**

`libs/platform/pyproject.toml` — add `"alembic>=1.20.0"` to `dependencies`. Root `pyproject.toml`, contract "Platform layers", top layer:

```toml
    "service | schema | migrations",
```

(keep every module the fix wave and Milestone 2 put in the other layers). Identity's `pyproject.toml` keeps `alembic` (its revision and `env.py` import it).

- [ ] **Step 2: Write the failing tests**

`libs/platform/tests/test_calls_between_services.py`:

```python
import asyncio
import json
from typing import Final
lazy from collections.abc import AsyncGenerator

import grpc
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from ledger_platform.grpc_client import (
    Channel,
    ChannelClosedError,
    InvalidGrpcClientOptionError,
    RemoteUnavailableError,
    answering_within,
    call_unary,
    caller_of,
    current_caller,
    remaining_deadline,
)
from ledger_platform.grpc_server import serve_grpc
from ledger_platform.tracing import Tracing

RPC: Final = "/test.Remote/Call"
FLAKY: Final = "/test.Remote/Flaky"
type Context = grpc.aio.ServicerContext[bytes, bytes]


class Remote:
    def __init__(self) -> None:
        self.flaky_calls = 0

    async def call(self, request: bytes, context: Context) -> bytes:
        del context
        if request == b"slow":
            await asyncio.sleep(5)
        seen = {"caller": current_caller(), "remaining": remaining_deadline()}
        return json.dumps(seen).encode()

    async def flaky(self, request: bytes, context: Context) -> bytes:
        self.flaky_calls += 1
        if self.flaky_calls == 1:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "once")
        return request


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracing(exporter: InMemorySpanExporter) -> Tracing:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return Tracing(provider)


@pytest.fixture
async def served(tracing: Tracing) -> AsyncGenerator[tuple[Remote, int]]:
    remote = Remote()

    def register(server: grpc.aio.Server) -> None:
        handlers = {
            "Call": grpc.unary_unary_rpc_method_handler(remote.call, request_deserializer=bytes, response_serializer=bytes),
            "Flaky": grpc.unary_unary_rpc_method_handler(remote.flaky, request_deserializer=bytes, response_serializer=bytes),
        }
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Remote", handlers),))

    async with serve_grpc(port=0, register=register, tracing=tracing) as port:
        yield remote, port


def method(channel: Channel, rpc: str) -> grpc.aio.UnaryUnaryMultiCallable[bytes, bytes]:
    return channel.current().unary_unary(rpc, request_serializer=bytes, response_deserializer=bytes)


async def test_the_callee_learns_who_calls_and_how_long_it_has(served: tuple[Remote, int]) -> None:
    _, port = served
    async with Channel(f"127.0.0.1:{port}", caller="identity", version="0.1.0").opened() as channel:
        answer = json.loads(await call_unary(method(channel, RPC), b"", rpc=RPC, deadline=2.0))
    assert answer["caller"] == "identity"
    assert 0 < answer["remaining"] <= 2.0


async def test_a_call_made_while_answering_one_is_bounded_by_what_is_left(served: tuple[Remote, int]) -> None:
    _, port = served
    async with Channel(f"127.0.0.1:{port}", caller="vault", version="0.1.0").opened() as channel:
        with answering_within(0.2), pytest.raises(RemoteUnavailableError) as caught:
            await call_unary(method(channel, RPC), b"slow", rpc=RPC, deadline=10.0)
    assert caught.value.status is grpc.StatusCode.DEADLINE_EXCEEDED


async def test_a_call_with_no_time_left_is_not_made(served: tuple[Remote, int]) -> None:
    _, port = served
    async with Channel(f"127.0.0.1:{port}", caller="vault", version="0.1.0").opened() as channel:
        with answering_within(-1.0), pytest.raises(RemoteUnavailableError) as caught:
            await call_unary(method(channel, RPC), b"", rpc=RPC)
    assert caught.value.status is grpc.StatusCode.DEADLINE_EXCEEDED
    assert remaining_deadline() is None


async def test_only_the_methods_named_idempotent_are_retried(served: tuple[Remote, int]) -> None:
    remote, port = served
    async with Channel(f"127.0.0.1:{port}", caller="identity", version="0.1.0", idempotent=(FLAKY,)).opened() as channel:
        assert await call_unary(method(channel, FLAKY), b"again", rpc=FLAKY) == b"again"
    assert remote.flaky_calls == 2
    remote.flaky_calls = 0
    async with Channel(f"127.0.0.1:{port}", caller="identity", version="0.1.0").opened() as channel:
        with pytest.raises(RemoteUnavailableError):
            await call_unary(method(channel, FLAKY), b"once", rpc=FLAKY)


async def test_one_trace_crosses_the_call(
    served: tuple[Remote, int], tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    _, port = served
    async with Channel(f"127.0.0.1:{port}", caller="identity", version="0.1.0").opened() as channel:
        with tracing.tracer.start_as_current_span("request"):
            await call_unary(method(channel, RPC), b"", rpc=RPC, tracing=tracing)
    spans = {span.name: span for span in exporter.get_finished_spans()}
    client, server = spans["test.Remote/Call client"], spans["test.Remote/Call"]
    assert (client.kind, server.kind) == (SpanKind.CLIENT, SpanKind.SERVER)
    assert server.context is not None and client.context is not None
    assert server.context.trace_id == client.context.trace_id == spans["request"].context.trace_id
    assert server.parent is not None and server.parent.span_id == client.context.span_id
    assert server.attributes is not None and server.attributes["rpc.service"] == "test.Remote"


def test_a_caller_is_named_by_the_first_word_of_its_user_agent() -> None:
    assert caller_of("identity/0.1.0 grpc-python-asyncio/1.84.0") == "identity"
    assert caller_of("grpc-python-asyncio/1.84.0") is None
    assert caller_of("NOT A NAME/1") is None
    assert caller_of(None) is None


async def test_a_channel_is_used_while_open_only() -> None:
    channel = Channel("127.0.0.1:1", caller="identity", version="0.1.0")
    with pytest.raises(ChannelClosedError):
        channel.current()
    with pytest.raises(InvalidGrpcClientOptionError):
        Channel("127.0.0.1:1", caller="identity", version="0.1.0", idempotent=("not-a-path",))
    with pytest.raises(InvalidGrpcClientOptionError):
        Channel("127.0.0.1:1", caller="Not A Name", version="0.1.0")
```

`libs/platform/tests/test_migrations.py`:

```python
import pytest

from ledger_kernel.errors import ErrorKind
from ledger_platform.migrations import (
    InvalidMigrationsOptionError,
    Migrations,
    MigrationUrlMissingError,
    SchemaNotCurrentError,
)


def test_a_schema_behind_is_not_current() -> None:
    error = SchemaNotCurrentError("0002")
    assert (error.kind, error.code, str(error)) == (
        ErrorKind.UNAVAILABLE,
        "schema_not_current",
        "the database schema is not at revision 0002, the one this version needs",
    )


def test_migrations_are_named_for_their_owner() -> None:
    assert str(MigrationUrlMissingError("vault")) == "vault's migrations were started without a database URL"
    with pytest.raises(InvalidMigrationsOptionError, match="migrations option scripts"):
        Migrations("", owner="vault")
```

(The runner itself is exercised end to end by identity's and vault's `test_migrations.py`, as the services' revisions are what it runs.)

`services/identity/tests/test_migrations.py` — import `MigrationUrlMissingError`, `OfflineMigrationsError`, `SchemaNotCurrentError` from `ledger_platform.migrations` instead of `ledger_identity.adapters.postgres.migrate`, and change the two `match=` strings to `"identity's migrations run against a database"` and `"identity's migrations were started without a database URL"` if Milestone 2's wording differs. Nothing else changes.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest libs/platform/tests/test_calls_between_services.py libs/platform/tests/test_migrations.py -q --no-cov`
Expected: FAIL with `ImportError: cannot import name 'Channel' from 'ledger_platform.grpc_client'`.

- [ ] **Step 4: Implement**

`libs/platform/src/ledger_platform/tracing.py` — add `SpanKind` users after `consuming` (same rules: the error's type only; a cancellation is not a failure):

```python
    @contextmanager
    def serving(self, rpc: str, traceparent: str | None) -> Generator[Span]:
        """A server span for the gRPC call ``rpc`` (``/package.Service/Method``), continuing its caller's trace.

        Like ``consuming``: the parent comes from the caller's ``traceparent`` alone, and a failure records its
        type and nothing more.
        """
        service, _, method = rpc.removeprefix("/").partition("/")
        with self._tracer.start_as_current_span(
            f"{service}/{method}",
            context=_PROPAGATOR.extract(_carrier(traceparent)),
            kind=SpanKind.SERVER,
            attributes={"rpc.system": "grpc", "rpc.service": service, "rpc.method": method},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except Exception as error:
                span.set_status(Status(StatusCode.ERROR))
                span.add_event(_EXCEPTION, {_EXCEPTION_TYPE: error_type(error)})
                raise

    @contextmanager
    def calling(self, rpc: str) -> Generator[Span]:
        """A client span for a call to ``rpc``, child of the active span; its ``traceparent`` is the callee's parent."""
        service, _, method = rpc.removeprefix("/").partition("/")
        with self._tracer.start_as_current_span(
            f"{service}/{method} client",
            kind=SpanKind.CLIENT,
            attributes={"rpc.system": "grpc", "rpc.service": service, "rpc.method": method},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except Exception as error:
                span.set_status(Status(StatusCode.ERROR))
                span.add_event(_EXCEPTION, {_EXCEPTION_TYPE: error_type(error)})
                raise
```

`libs/platform/src/ledger_platform/grpc_client.py` — the module docstring's paragraph on deadlines becomes:

```text
A call's deadline is the caller's, at most ``MAX_DEADLINE``, and never longer than what is left of the call
being answered (``answering_within``, set by the chassis's gRPC server for every call it serves): a call that
has no time left is not made at all. The active trace travels in ``traceparent``; given the service's
``Tracing``, ``call_unary`` also opens a client span. Methods a ``Channel`` names idempotent are retried by
gRPC itself (three attempts, 0.1 s doubling to 1 s, on ``UNAVAILABLE`` only; hedging off); no other is. A
``Channel`` names its service in the user agent (``identity/0.1.0 grpc-python-asyncio/...``), which the
callee reads back with ``caller_of``: unauthenticated until the mesh, and logged and audited as such.
```

and these are added (with `import json`, `import re`, `from contextlib import asynccontextmanager, contextmanager`, `from contextvars import ContextVar`, `from time import monotonic`, `from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator`, `lazy from collections.abc import AsyncGenerator, Generator, Sequence`, `lazy from ledger_platform.tracing import Tracing`; `tracing` is in the infrastructure layer below `grpc_client`'s, so the lazy import is a type-only one and the layers hold):

```python
CALLER_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{1,30}", re.ASCII)
"""A service's name: what ``ServiceSettings.service_name`` allows."""
_RPC_PATH: Final = re.compile(r"/([A-Za-z_][A-Za-z0-9_.]*)/([A-Za-z_][A-Za-z0-9_]*)", re.ASCII)
_RETRY: Final = {
    "maxAttempts": 3,
    "initialBackoff": "0.1s",
    "maxBackoff": "1s",
    "backoffMultiplier": 2,
    "retryableStatusCodes": ["UNAVAILABLE"],
}
_PROPAGATOR: Final = TraceContextTextMapPropagator()
_deadline: ContextVar[float | None] = ContextVar("ledger_grpc_deadline", default=None)
"""When the call being answered must be answered by (``monotonic``), if it has a deadline."""
_caller: ContextVar[str | None] = ContextVar("ledger_grpc_caller", default=None)


class ChannelClosedError(LedgerError):
    """A channel was used while it was not open: a service that never opened it."""

    code: ClassVar[str] = "grpc_channel_closed"

    def __init__(self) -> None:
        super().__init__("the gRPC channel is not open")


def caller_of(user_agent: str | None) -> str | None:
    """The service a gRPC user agent names (its first word, before ``/``), if it names one."""
    if user_agent is None:
        return None
    first = user_agent.partition("/")[0]
    return first if CALLER_PATTERN.fullmatch(first) and not first.startswith("grpc-") else None


def current_caller() -> str | None:
    """The service whose call is being answered, as it named itself; ``None`` outside a call or unnamed."""
    return _caller.get()


def remaining_deadline() -> float | None:
    """Seconds left of the call being answered; ``None`` outside a call or without a deadline."""
    deadline = _deadline.get()
    return None if deadline is None else deadline - monotonic()


@contextmanager
def answering_within(seconds: float | None, caller: str | None = None) -> Generator[None]:
    """For the length of the block: the call being answered must end within ``seconds``, and ``caller`` makes it."""
    deadline = _deadline.set(None if seconds is None else monotonic() + seconds)
    named = _caller.set(caller)
    try:
        yield
    finally:
        _caller.reset(named)
        _deadline.reset(deadline)


def _service_config(idempotent: Sequence[str]) -> str:
    names = []
    for rpc in idempotent:
        match = _RPC_PATH.fullmatch(rpc)
        if match is None:
            raise InvalidGrpcClientOptionError(option="idempotent", requirement="/package.Service/Method paths", value=rpc)
        names.append({"service": match[1], "method": match[2]})
    config = {"methodConfig": [{"name": names, "retryPolicy": _RETRY}]} if names else {}
    return json.dumps(config)


@final
class Channel:
    """A channel to another service, open while the service runs: one of its resources (see the module)."""

    __slots__ = ("_channel", "_options", "_target")

    def __init__(self, target: str, *, caller: str, version: str, idempotent: Sequence[str] = ()) -> None:
        if CALLER_PATTERN.fullmatch(caller) is None:
            raise InvalidGrpcClientOptionError(option="caller", requirement="a service name", value=caller)
        self._target = target
        self._options = (
            ("grpc.primary_user_agent", f"{caller}/{version}"),
            ("grpc.enable_retries", 1),
            ("grpc.service_config", _service_config(idempotent)),
        )
        self._channel: grpc.aio.Channel | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[Channel]:
        """This channel, connected for the length of the block (insecure: the mesh does mTLS)."""
        async with grpc.aio.insecure_channel(self._target, options=self._options) as channel:
            self._channel = channel
            try:
                yield self
            finally:
                self._channel = None

    def current(self) -> grpc.aio.Channel:
        """The open channel, for a generated stub. ``ChannelClosedError`` while not open."""
        channel = self._channel
        if channel is None:
            raise ChannelClosedError
        return channel
```

`call_unary` becomes:

```python
async def call_unary[Request, Response](
    method: grpc.aio.UnaryUnaryMultiCallable[Request, Response],
    request: Request,
    *,
    rpc: str,
    deadline: float = DEFAULT_DEADLINE,
    tracing: Tracing | None = None,
) -> Response:
    """Call ``method`` (named ``rpc``, ``/package.Service/Method``) within ``deadline`` seconds.

    The deadline is cut to what is left of the call being answered; with nothing left the call is not made
    (``RemoteUnavailableError``, ``DEADLINE_EXCEEDED``). A failure is the ``RemoteError`` the module describes.
    """
    require_seconds(InvalidGrpcClientOptionError, "deadline", deadline, ceiling=MAX_DEADLINE)
    left = remaining_deadline()
    timeout = deadline if left is None else min(deadline, left)
    if timeout <= 0:
        raise RemoteUnavailableError(rpc, grpc.StatusCode.DEADLINE_EXCEEDED, _UNKNOWN_CODE)
    if tracing is None:
        return await _call(method, request, rpc, timeout)
    with tracing.calling(rpc):
        return await _call(method, request, rpc, timeout)


async def _call[Request, Response](
    method: grpc.aio.UnaryUnaryMultiCallable[Request, Response], request: Request, rpc: str, timeout: float
) -> Response:
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    try:
        return await method(request, timeout=timeout, metadata=tuple(carrier.items()))
    except grpc.aio.AioRpcError as error:
        raise remote_error(error, rpc) from error
```

`libs/platform/src/ledger_platform/grpc_server.py` — `serve_grpc(*, port, register, grace=10.0, tracing: Tracing | None = None)` passes `tracing` to `_serving`, which builds the server with `interceptors=(ErrorInterceptor(tracing),)` (or appends a second interceptor if the fix wave split them: the guard below must run inside `_Answering`). Add the scope of a call and use it in both guards:

```python
_USER_AGENT: Final = "user-agent"
_TRACEPARENT: Final = "traceparent"


@contextmanager
def _answering(context: _Context, rpc: str, tracing: Tracing | None) -> Generator[None]:
    """The scope of one call: who calls, how long it has, the trace it continues, its log context."""
    metadata = {key: value for key, value in context.invocation_metadata() or () if isinstance(value, str)}
    caller = caller_of(metadata.get(_USER_AGENT))
    with (
        answering_within(context.time_remaining(), caller),
        structlog.contextvars.bound_contextvars(rpc=rpc, caller=caller),
        nullcontext() if tracing is None else tracing.serving(rpc, metadata.get(_TRACEPARENT)),
    ):
        yield


def _guard_coroutine[Input](
    behavior: _CoroutineMethod[Input], rpc: str, tracing: Tracing | None
) -> _CoroutineMethod[Input]:
    async def guarded(request: Input, context: _Context) -> object:
        async with _Answering(context, rpc):
            with _answering(context, rpc, tracing):
                return await behavior(request, context)

    return guarded


def _guard_generator[Input](
    behavior: _GeneratorMethod[Input], rpc: str, tracing: Tracing | None
) -> _GeneratorMethod[Input]:
    async def guarded(request: Input, context: _Context) -> AsyncGenerator[object]:
        async with _Answering(context, rpc), aclosing(behavior(request, context)) as responses:
            with _answering(context, rpc, tracing):
                async for response in responses:
                    yield response

    return guarded
```

and `_guard`, `_guarded` and `ErrorInterceptor.__init__(self, tracing: Tracing | None = None)` thread `tracing` through (`ErrorInterceptor` stores it in `__slots__ = ("_tracing",)`; the fix wave's own fields stay). Imports: `from contextlib import aclosing, asynccontextmanager, contextmanager, nullcontext`, `from ledger_platform.grpc_client import ERROR_CODE_KEY, answering_within, caller_of`, `lazy from collections.abc import Generator`, `lazy from ledger_platform.tracing import Tracing`. (`time_remaining()` is `None` for a call without a deadline; `invocation_metadata()` holds `(key, value)` pairs whose binary values are `bytes`.)

`libs/platform/src/ledger_platform/service.py` — in `_start`, `serve_grpc(port=settings.grpc_port, register=partial(services, runtime), tracing=tracing)` (keep the fix wave's grace argument).

`libs/platform/src/ledger_platform/migrations.py`:

```python
"""Every service's schema brought to its version's (Alembic): from tests, from the command line, at readiness.

A service keeps its revisions in its package (``<package>.adapters.postgres:migrations``) and its ``env.py``
is one call, ``run_migrations(<the service's metadata>)``. Migrations run on a synchronous psycopg engine, one
transaction per upgrade or downgrade, and every session waits at most ten seconds for a lock: a migration that
cannot get one fails instead of queueing the service's own statements behind it. They run against a database
only, never as SQL text. ``require_head`` is the running service's side: its readiness check that the database
is at the revision its code expects.
"""

from typing import ClassVar, Final, final
lazy from collections.abc import Mapping

from alembic import command, context
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, String, column, create_engine, select, table
from sqlalchemy.pool import NullPool

from ledger_kernel.errors import LedgerError, UnavailableError
from ledger_platform.options import InvalidOptionError
from ledger_platform.schema import CHASSIS_METADATA
lazy from ledger_platform.database import Database

HEAD: Final = "head"
BASE: Final = "base"
_LOCK_TIMEOUT: Final = "-c lock_timeout=10s"
_OWNER: Final = "owner"
_VERSION: Final = select(column("version_num", String)).select_from(table("alembic_version"))


class OfflineMigrationsError(LedgerError):
    """Someone asked for the migrations as SQL text. They run against a database only."""

    code: ClassVar[str] = "offline_migrations"

    def __init__(self, owner: str) -> None:
        super().__init__(f"{owner}'s migrations run against a database, not as SQL text")


class MigrationUrlMissingError(LedgerError):
    """The migrations were started without a database to run against."""

    code: ClassVar[str] = "migration_url_missing"

    def __init__(self, owner: str) -> None:
        super().__init__(f"{owner}'s migrations were started without a database URL")


class InvalidMigrationsOptionError(InvalidOptionError):
    """A migrations option that cannot work."""

    code: ClassVar[str] = "invalid_migrations_option"
    label: ClassVar[str] = "migrations option"


class SchemaNotCurrentError(UnavailableError):
    """The database is not at the revision this version needs: migrations are pending (or ran ahead)."""

    code: ClassVar[str] = "schema_not_current"

    def __init__(self, expected: str) -> None:
        super().__init__(f"the database schema is not at revision {expected}, the one this version needs")


@final
class Migrations:
    """One service's migration scripts (a package resource), and what runs them."""

    __slots__ = ("_owner", "_scripts")

    def __init__(self, scripts: str, *, owner: str) -> None:
        if not scripts:
            raise InvalidMigrationsOptionError(
                option="scripts", requirement="a package:directory location", value=scripts
            )
        self._scripts = scripts
        self._owner = owner

    def _config(self, url: str | None = None) -> Config:
        config = Config()
        config.set_main_option("script_location", self._scripts)
        config.attributes[_OWNER] = self._owner
        if url is not None:
            config.attributes["url"] = url  # an attribute: the option parser would read a % in the password
        return config

    def upgrade(self, url: str, revision: str = HEAD) -> None:
        """Bring the database at ``url`` (``postgresql+psycopg://``) up to ``revision``."""
        command.upgrade(self._config(url), revision)

    def downgrade(self, url: str, revision: str = BASE) -> None:
        """Take the database at ``url`` down to ``revision``."""
        command.downgrade(self._config(url), revision)

    def head(self) -> str:
        """The revision this version's migrations end at."""
        (current,) = ScriptDirectory.from_config(self._config()).get_heads()
        return current

    async def require_head(self, database: Database, expected: str) -> None:
        """Return when the database is at ``expected``, else ``SchemaNotCurrentError`` (no table: also)."""
        async with database.transaction(None) as unit:
            found = (await unit.connection.execute(_VERSION)).scalars().all()
        if list(found) != [expected]:
            raise SchemaNotCurrentError(expected)


def run_migrations(*metadata: MetaData) -> None:
    """The body of a service's ``env.py``: run its revisions on the URL ``Migrations`` handed over."""
    attributes: Mapping[str, object] = context.config.attributes
    owner = str(attributes.get(_OWNER, "the service"))
    if context.is_offline_mode():
        raise OfflineMigrationsError(owner)
    url = attributes.get("url")
    if not isinstance(url, str):
        raise MigrationUrlMissingError(owner)
    engine = create_engine(url, poolclass=NullPool, connect_args={"options": _LOCK_TIMEOUT})
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=[CHASSIS_METADATA, *metadata])
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
```


`services/identity/src/ledger_identity/adapters/postgres/migrate.py` becomes:

```python
"""identity's schema brought to this version's: ``ledger_platform.migrations`` with identity's revisions."""

from typing import Final
lazy from ledger_platform.database import Database

from ledger_identity.settings import IdentitySettings
from ledger_platform.migrations import BASE, HEAD, Migrations

SCRIPTS: Final = "ledger_identity.adapters.postgres:migrations"
MIGRATIONS: Final = Migrations(SCRIPTS, owner="identity")


def upgrade(url: str, revision: str = HEAD) -> None:
    """Bring identity's database at ``url`` up to ``revision``."""
    MIGRATIONS.upgrade(url, revision)


def downgrade(url: str, revision: str = BASE) -> None:
    """Take identity's database at ``url`` down to ``revision``."""
    MIGRATIONS.downgrade(url, revision)


def head() -> str:
    """The revision identity's migrations end at."""
    return MIGRATIONS.head()


async def require_head(database: Database, expected: str) -> None:
    """identity's readiness check: the database is at ``expected``."""
    await MIGRATIONS.require_head(database, expected)


def main() -> int:
    """``ledger-identity-migrate``: bring the database of ``LEDGER_DATABASE_URL`` to this version's schema."""
    upgrade(IdentitySettings().database_url.get_secret_value())
    return 0
```

(order the imports as ruff's isort wants: the `lazy` import after the ordinary ones.)

`services/identity/src/ledger_identity/adapters/postgres/migrations/env.py` becomes:

```python
"""Alembic's entry point for identity: the platform's runner with identity's tables."""

from ledger_identity.adapters.postgres.tables import IdentityTable
from ledger_platform.migrations import run_migrations

run_migrations(IdentityTable.metadata)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest libs/platform services/identity -q --no-cov`
Expected: PASS (identity's migration tests unchanged in meaning; its readiness test still finds `migrations`).

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`caller_of`'s four outcomes, both branches of `call_unary`'s tracing, a closed channel, the bad options, the three migration errors through identity's tests).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock libs/platform services/identity
git commit -m "feat(platform): calls between services carry caller, trace and deadline; one migration runner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The vault package and its domain

**Files:**
- Create: `services/vault/pyproject.toml`, `services/vault/src/ledger_vault/__init__.py`, `py.typed`
- Create: `services/vault/src/ledger_vault/domain/__init__.py`, `ids.py`, `errors.py`, `holder.py`, `keys.py`, `certificate.py`, `custody.py`, `signing.py`, `events.py`, `expiry.py`
- Modify: `pyproject.toml` (root)
- Test: `services/vault/tests/test_domain.py`, `services/vault/tests/test_contract_codes.py`

**Interfaces:**
- Consumes: `TaxId`, `TaxIdKind` (`ledger_kernel.countries.do`), `Tenant`, `CompanyId`, `OrganizationId`, `new_id`, the kernel errors and `excerpt`, `SubjectId` (`ledger_kernel.access`, M2).
- Produces (all under `ledger_vault.domain`):
  - `ids`: `CertificateId`, `SignatureId`.
  - `errors`: `CertificateRefusedError` and its eleven subclasses (codes = `CertificateRefusal`), `BundleProblem` (`NO_KEY`, `NO_CERTIFICATE`), `CertificateAlreadyActiveError`, `CertificateChangedError(generation)`, `NoActiveCertificateError(company)`, `ActiveCertificateExpiredError(not_after)` (CONFLICT, `certificate_expired`), `CertificateNotFoundError(certificate)`, `DocumentRefusedError` and `DocumentTooLargeError(limit)`, `DocumentMalformedError`, `DocumentDoctypeError`, `DocumentSignedError`, `SeedMalformedError`; `InvalidReferenceError(text)`.
  - `holder`: `SubjectAttribute`, `HolderKind`, `Holder(kind, name, identifier, tax_id)` with `hint`, `HolderReader` and `EtsiOrganizationIdentifier`, `EtsiPersonIdentifier`, `BareSerialNumber`, `DOMINICAN_CONVENTIONS`, `read_holder(subject, readers=...)`, `HolderBinding` (`TAXPAYER`, `REPRESENTATIVE`), `bind_holder(holder, taxpayer, *, representatives)`; OID constants.
  - `keys`: `KeyMaterial(buffer)` (`copied`, `buffer`, `wiped`, `wipe`, context manager, withheld `repr`), `WipedKeyError`, `KeyContext(tenant, certificate)` with `encryption_context()`, `associated_data()`.
  - `certificate`: `MIN_RSA_BITS`, `CertificateFacts`, `CustodyPolicy(min_rsa_bits=2048, representatives=True)`, `Admission(holder, binding)`, `admit(facts, *, taxpayer, now, policy)`, `CertificateStatus`, `Retirement`, `SealedKey(provider, key_id, wrapped_key, nonce, ciphertext)`, `Certificate` with `require_usable(now)`, `retire(reason, *, now)`.
  - `custody`: `Candidate(id, tenant, facts, admission, sealed, by)`, `Installation(certificate, retired, event)` with `changed`, `upload(current, candidate, *, last_generation, now)`, `rotate(current, candidate, *, replaces, now)`.
  - `signing`: `SigningPurpose` (`DOCUMENT`, `SEED`), `MAX_REFERENCE = 64`, `parse_reference(text)`, `SignatureRecord`.
  - `events`: `CertificateUploaded`, `CertificateRotated`, `CertificateRetired`, `CertificateExpiring`, `type VaultEvent`.
  - `expiry`: `THRESHOLDS = (30, 7, 1)`, `HORIZON`, `due_threshold(not_after, now, announced)`.

Re-check before starting: the root `pyproject.toml` as Milestone 2 left it (its identity entries are the pattern followed here, and its test that every workspace package is wired into coverage, mypy, deptry and import-linter must pass with the vault entries); `ledger_kernel.access.SubjectId` exists (M2 Task 1).

- [ ] **Step 1: The package and its place in the workspace**

`services/vault/pyproject.toml`:

```toml
[project]
name = "ledger-vault"
version = "0.1.0"
description = "The vault service: custody of companies' signing certificates, and XML signatures made with them."
requires-python = ">=3.15"
dependencies = ["ledger-kernel"]

[tool.uv.sources]
ledger-kernel = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

`services/vault/src/ledger_vault/__init__.py`:

```python
"""The vault service: custody of signing certificates, signatures with them, and the audit of both."""
```

`services/vault/src/ledger_vault/py.typed`: empty file. `services/vault/src/ledger_vault/domain/__init__.py`:

```python
"""What vault is about, in the standard library and the kernel only: no crypto library, no SQL, no wire."""
```

Root `pyproject.toml`: add `"ledger-vault"` to the root project's `dependencies`, `ledger-vault = { workspace = true }` to `[tool.uv.sources]`; `mypy-vault = { cmd = "mypy services/vault" }` appended to the `mypy` sequence, `deps-vault = { cmd = "deptry services/vault/src --config services/vault/pyproject.toml" }` appended to `deps`; `"ledger_vault"` to isort's `known-first-party`, coverage's `source_pkgs`, import-linter's `root_packages`, the `source_modules` of "Production code never imports test tooling" and the `forbidden_modules` of "Libraries never import services"; and these contracts:

```toml
[[tool.importlinter.contracts]]
name = "Services never import each other"
type = "independence"
modules = ["ledger_identity", "ledger_vault"]

[[tool.importlinter.contracts]]
name = "Vault's domain is the standard library and the kernel"
type = "forbidden"
source_modules = ["ledger_vault.domain"]
forbidden_modules = [
    "ledger_contracts", "ledger_platform", "pydantic", "sqlalchemy", "fastapi", "starlette", "grpc", "nats",
    "structlog", "google", "psycopg", "alembic", "cryptography", "lxml", "boto3", "botocore",
]

[[tool.importlinter.contracts]]
name = "Vault is hexagonal"
type = "layers"
containers = ["ledger_vault"]
exhaustive = true
layers = ["domain"]
```

(Tasks 5, 6, 9 and 11 extend the layers as the others appear.)

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/vault/tests/test_domain.py`:

```python
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.access import SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_vault.domain.certificate import (
    CertificateFacts,
    CertificateStatus,
    CustodyPolicy,
    Retirement,
    SealedKey,
    admit,
)
from ledger_vault.domain.custody import Candidate, rotate, upload
from ledger_vault.domain.errors import (
    ActiveCertificateExpiredError,
    CertificateAlreadyActiveError,
    CertificateChangedError,
    CertificateExpiredError,
    CertificateKeyError,
    CertificateNotYetValidError,
    CertificateUntrustedError,
    CertificateUsageError,
    HolderMismatchError,
    HolderUnknownError,
    InvalidReferenceError,
    NoActiveCertificateError,
)
from ledger_vault.domain.events import CertificateRetired, CertificateRotated, CertificateUploaded
from ledger_vault.domain.expiry import due_threshold
from ledger_vault.domain.holder import (
    COMMON_NAME,
    ORGANIZATION_IDENTIFIER,
    SERIAL_NUMBER,
    HolderBinding,
    HolderKind,
    bind_holder,
    read_holder,
)
from ledger_vault.domain.ids import CertificateId
from ledger_vault.domain.keys import KeyContext, KeyMaterial, WipedKeyError
from ledger_vault.domain.signing import parse_reference
lazy from ledger_vault.domain.errors import CertificateRefusedError

NOW: Final = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
RNC: Final = TaxId.parse("101850043")
CEDULA: Final = TaxId.parse("00112345678")
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
PERSON: Final = ((COMMON_NAME, "JUAN PEREZ"), (SERIAL_NUMBER, "IDCDO-00112345678"))
SEALED: Final = SealedKey(
    provider="local", key_id="local:0", wrapped_key=b"w", nonce=b"n" * 12, ciphertext=b"c"
)


FACTS: Final = CertificateFacts(
    der=b"der",
    subject=PERSON,
    issuer_name="LEDGER TEST CA",
    serial_number="1a",
    fingerprint="f" * 64,
    not_before=NOW - timedelta(days=1),
    not_after=NOW + timedelta(days=365),
    key_algorithm="rsa",
    key_bits=2048,
    signs=True,
    trusted=True,
)


def candidate(fingerprint: str = "f" * 64) -> Candidate:
    admitted = admit(replace(FACTS, fingerprint=fingerprint), taxpayer=RNC, now=NOW, policy=CustodyPolicy())
    return Candidate(
        id=CertificateId(new_id()),
        tenant=TENANT,
        facts=replace(FACTS, fingerprint=fingerprint),
        admission=admitted,
        sealed=SEALED,
        by=SubjectId("2001"),
    )


# ── Holders ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("subject", "kind", "identifier", "tax_id"),
    [
        (PERSON, HolderKind.PERSON, "00112345678", CEDULA),
        (((ORGANIZATION_IDENTIFIER, "VATDO-101850043"),), HolderKind.ORGANIZATION, "101850043", RNC),
        (((SERIAL_NUMBER, "001-1234567-8"),), HolderKind.PERSON, "00112345678", CEDULA),
        (((SERIAL_NUMBER, "101850043"),), HolderKind.ORGANIZATION, "101850043", RNC),
        (((SERIAL_NUMBER, "PASUS-AB1234567"),), HolderKind.PERSON, "AB1234567", None),
        (((SERIAL_NUMBER, "IDCES-12345678Z"),), HolderKind.PERSON, "12345678Z", None),
    ],
    ids=["etsi-person", "etsi-seal", "bare-cedula", "bare-rnc", "passport", "foreign-id-card"],
)
def test_each_dominican_convention_names_its_holder(
    subject: tuple[tuple[str, str], ...], kind: HolderKind, identifier: str, tax_id: TaxId | None
) -> None:
    holder = read_holder(subject)
    assert (holder.kind, holder.identifier, holder.tax_id) == (kind, identifier, tax_id)


@pytest.mark.parametrize(
    "subject",
    [
        ((COMMON_NAME, "SOMEONE"),),
        ((SERIAL_NUMBER, "VATDO-101850043"),),
        ((SERIAL_NUMBER, "IDCDO-123"),),
        ((ORGANIZATION_IDENTIFIER, "VATES-B12345678"),),
        ((ORGANIZATION_IDENTIFIER, "VATDO-12"),),
        ((SERIAL_NUMBER, "not a number"),),
    ],
    ids=["no-identifier", "vat-as-serial", "short-cedula", "foreign-vat", "short-rnc", "garbage"],
)
def test_a_subject_no_convention_reads_names_nobody(subject: tuple[tuple[str, str], ...]) -> None:
    with pytest.raises(HolderUnknownError) as caught:
        read_holder(subject)
    assert caught.value.code == "certificate_holder_unknown"


def test_a_holder_is_shown_masked_and_never_in_a_repr() -> None:
    holder = read_holder(PERSON)
    assert holder.hint == "********678"
    assert "00112345678" not in repr(holder)
    assert "JUAN" not in repr(holder)
    assert read_holder(((SERIAL_NUMBER, "PASUS-AB"),)).hint == "AB"


def test_the_taxpayer_s_own_certificate_and_a_representative_s_bind() -> None:
    person = read_holder(PERSON)
    assert bind_holder(person, CEDULA, representatives=False) is HolderBinding.TAXPAYER
    assert bind_holder(person, RNC, representatives=True) is HolderBinding.REPRESENTATIVE
    with pytest.raises(HolderMismatchError):
        bind_holder(person, RNC, representatives=False)
    seal = read_holder(((ORGANIZATION_IDENTIFIER, "VATDO-130000012"),))
    with pytest.raises(HolderMismatchError):
        bind_holder(seal, RNC, representatives=True)


# ── Admission ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("broken", "error"),
    [
        (replace(FACTS, key_algorithm="ec", key_bits=256), CertificateKeyError),
        (replace(FACTS, key_bits=1024), CertificateKeyError),
        (replace(FACTS, signs=False), CertificateUsageError),
        (replace(FACTS, not_before=NOW + timedelta(seconds=1)), CertificateNotYetValidError),
        (replace(FACTS, not_after=NOW), CertificateExpiredError),
        (replace(FACTS, trusted=False), CertificateUntrustedError),
        (replace(FACTS, subject=((COMMON_NAME, "X"),)), HolderUnknownError),
    ],
    ids=["ec", "short-rsa", "no-signing", "not-yet", "expired", "untrusted", "nobody"],
)
def test_a_certificate_that_fails_a_rule_is_refused_with_its_code(
    broken: CertificateFacts, error: type[CertificateRefusedError]
) -> None:
    with pytest.raises(error) as caught:
        admit(broken, taxpayer=RNC, now=NOW, policy=CustodyPolicy())
    assert caught.value.kind is ErrorKind.INVALID
    assert "00112345678" not in str(caught.value)


def test_a_certificate_that_passes_every_rule_is_admitted() -> None:
    admitted = admit(FACTS, taxpayer=RNC, now=NOW, policy=CustodyPolicy())
    assert admitted.binding is HolderBinding.REPRESENTATIVE


# ── Installation and rotation ─────────────────────────────────────────────────


def test_a_first_certificate_is_installed_at_the_next_generation() -> None:
    first = upload(None, candidate(), last_generation=2, now=NOW)
    assert first.changed
    assert (first.certificate.generation, first.certificate.status, first.retired) == (
        3,
        CertificateStatus.ACTIVE,
        None,
    )
    assert first.event == CertificateUploaded(
        tenant=TENANT, certificate=first.certificate.id, generation=3, not_after=first.certificate.not_after
    )


def test_the_same_certificate_again_changes_nothing_and_another_needs_a_rotation() -> None:
    active = upload(None, candidate(), last_generation=0, now=NOW).certificate
    assert upload(active, candidate(), last_generation=1, now=NOW).event is None
    with pytest.raises(CertificateAlreadyActiveError) as caught:
        upload(active, candidate("e" * 64), last_generation=1, now=NOW)
    assert caught.value.kind is ErrorKind.CONFLICT


def test_a_rotation_retires_the_generation_it_names() -> None:
    active = upload(None, candidate(), last_generation=0, now=NOW).certificate
    later = NOW + timedelta(days=1)
    rotated = rotate(active, candidate("e" * 64), replaces=1, now=later)
    assert rotated.retired is not None
    assert (rotated.retired.status, rotated.retired.retirement, rotated.retired.retired_at) == (
        CertificateStatus.RETIRED,
        Retirement.ROTATED,
        later,
    )
    assert rotated.certificate.generation == 2
    assert isinstance(rotated.event, CertificateRotated)
    assert rotated.event.retired == active.id
    assert rotate(rotated.certificate, candidate("e" * 64), replaces=1, now=later).event is None
    with pytest.raises(CertificateChangedError, match="not generation 1"):
        rotate(rotated.certificate, candidate("d" * 64), replaces=1, now=later)
    with pytest.raises(NoActiveCertificateError):
        rotate(None, candidate(), replaces=1, now=later)


def test_an_active_certificate_signs_until_it_expires_and_retires_with_an_event() -> None:
    active = upload(None, candidate(), last_generation=0, now=NOW).certificate
    active.require_usable(active.not_after - timedelta(seconds=1))
    with pytest.raises(ActiveCertificateExpiredError) as caught:
        active.require_usable(active.not_after)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "certificate_expired")
    retired, event = active.retire(Retirement.COMPANY_DEACTIVATED, now=NOW)
    assert retired.retirement is Retirement.COMPANY_DEACTIVATED
    assert event == CertificateRetired(tenant=TENANT, certificate=active.id, generation=1)


# ── Expiry ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("days_left", "announced", "due"),
    [(31, None, None), (30, None, 30), (5, None, 7), (5, 7, None), (0.5, 7, 1), (-1, None, 1), (-1, 1, None)],
)
def test_each_threshold_is_announced_once(days_left: float, announced: int | None, due: int | None) -> None:
    assert due_threshold(NOW + timedelta(days=days_left), NOW, announced) == due


@given(st.floats(min_value=-10, max_value=60), st.sampled_from([None, 30, 7, 1]))
def test_an_announcement_is_never_wider_than_one_made(days_left: float, announced: int | None) -> None:
    due = due_threshold(NOW + timedelta(days=days_left), NOW, announced)
    assert due is None or announced is None or due < announced


# ── Keys and references ─────────────────────────────────────────────────────────


def test_key_material_is_wiped_however_its_block_ends_and_never_shown() -> None:
    material = KeyMaterial.copied(b"secret key bytes")
    with pytest.raises(ZeroDivisionError), material:
        _ = 1 / 0
    assert material.wiped
    assert repr(material) == "KeyMaterial(<withheld>)"
    with pytest.raises(WipedKeyError):
        _ = material.buffer
    material.wipe()


def test_a_key_is_bound_to_its_certificate_and_company() -> None:
    certificate = CertificateId(new_id())
    context = KeyContext(TENANT, certificate)
    assert context.encryption_context() == {
        "ledger:organization": str(TENANT.organization),
        "ledger:company": str(TENANT.company),
        "ledger:certificate": str(certificate),
    }
    assert context.associated_data().startswith(b"ledger.vault.v1.private-key|")
    assert KeyContext(TENANT, CertificateId(new_id())).associated_data() != context.associated_data()


@pytest.mark.parametrize("text", ["E310000000001", "x" * 64])
def test_a_reference_is_kept_as_given(text: str) -> None:
    assert parse_reference(text) == text
    assert parse_reference("") is None


@pytest.mark.parametrize("text", ["x" * 65, "has space", "ñ"])
def test_anything_else_is_not_a_reference(text: str) -> None:
    with pytest.raises(InvalidReferenceError):
        parse_reference(text)
```

`services/vault/tests/test_contract_codes.py`:

```python
from ledger_contracts.vault import CertificateRefusal
from ledger_vault.domain.errors import CertificateRefusedError


def test_every_refusal_vault_raises_is_a_code_of_its_contract() -> None:
    codes = {error.code for error in CertificateRefusedError.__subclasses__()}
    assert codes == {refusal.value for refusal in CertificateRefusal}
```

(The vault package depends on `ledger-contracts` from Task 10 on; until then this test imports it through the workspace's dev environment, which deptry does not check for tests.)

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/vault -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_vault.domain.certificate'`.

- [ ] **Step 4: Implement**

`services/vault/src/ledger_vault/domain/ids.py`:

```python
"""Identifiers of vault's own entities; organizations and companies use the kernel's."""

from typing import NewType
from uuid import UUID

CertificateId = NewType("CertificateId", UUID)
SignatureId = NewType("SignatureId", UUID)
```

`services/vault/src/ledger_vault/domain/errors.py`:

```python
"""vault's errors: each with its kind and a stable code callers match on (§4.2).

A certificate vault refuses is ``INVALID`` and says why in its code. Those codes are part of
``VaultService``'s contract (``ledger_contracts.vault.CertificateRefusal``): identity answers its own clients
with them. Messages name limits, dates and fields, never the bundle, its password or its subject: a
certificate names a person.
"""

from enum import StrEnum
from typing import ClassVar
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_kernel.errors import ConflictError, InvalidInputError, NotFoundError, excerpt


class CertificateRefusedError(InvalidInputError):
    """A certificate vault does not take into custody; each subclass says why."""

    code: ClassVar[str] = "certificate_refused"


class CertificateTooLargeError(CertificateRefusedError):
    """The bundle is larger than any certificate with its chain: refused unread."""

    code: ClassVar[str] = "certificate_too_large"

    def __init__(self, limit: int) -> None:
        super().__init__(f"a certificate bundle is at most {limit} bytes")


class CertificateUnreadableError(CertificateRefusedError):
    """The bytes are not a PKCS#12 bundle that can be read."""

    code: ClassVar[str] = "certificate_unreadable"

    def __init__(self) -> None:
        super().__init__("the bundle is not a PKCS#12 file (.p12, .pfx) that can be read")


class CertificatePasswordError(CertificateRefusedError):
    """The password does not open the bundle. Neither is ever repeated."""

    code: ClassVar[str] = "certificate_password_wrong"

    def __init__(self) -> None:
        super().__init__("the password does not open the bundle")


class BundleProblem(StrEnum):
    """What a bundle that opened lacks."""

    NO_KEY = "holds no private key"
    NO_CERTIFICATE = "holds no certificate for its private key"


class CertificateIncompleteError(CertificateRefusedError):
    """The bundle opened, but it is not a private key with its certificate."""

    code: ClassVar[str] = "certificate_bundle_incomplete"

    def __init__(self, problem: BundleProblem) -> None:
        super().__init__(f"the bundle {problem}")


class CertificateKeyError(CertificateRefusedError):
    """The key is not RSA, or it is too short to be trusted with a signature."""

    code: ClassVar[str] = "certificate_key_unsupported"

    def __init__(self, *, algorithm: str, bits: int, minimum: int) -> None:
        super().__init__(f"the key must be RSA of at least {minimum} bits, not {algorithm} of {bits} bits")


class CertificateUsageError(CertificateRefusedError):
    """The certificate's key usage allows neither a digital signature nor non-repudiation."""

    code: ClassVar[str] = "certificate_not_for_signing"

    def __init__(self) -> None:
        super().__init__("the certificate's key usage allows no signature")


class CertificateNotYetValidError(CertificateRefusedError):
    """The certificate's validity has not begun."""

    code: ClassVar[str] = "certificate_not_yet_valid"

    def __init__(self, not_before: datetime) -> None:
        super().__init__(f"the certificate is valid from {not_before.isoformat()}")


class CertificateExpiredError(CertificateRefusedError):
    """The certificate's validity has ended."""

    code: ClassVar[str] = "certificate_expired"

    def __init__(self, not_after: datetime) -> None:
        super().__init__(f"the certificate expired on {not_after.isoformat()}")


class CertificateUntrustedError(CertificateRefusedError):
    """The certificate does not chain to a certification authority of the trust store."""

    code: ClassVar[str] = "certificate_untrusted"

    def __init__(self) -> None:
        super().__init__("the certificate does not chain to a certification authority vault accepts")


class HolderUnknownError(CertificateRefusedError):
    """The subject names nobody by a convention vault reads (``ledger_vault.domain.holder``)."""

    code: ClassVar[str] = "certificate_holder_unknown"

    def __init__(self) -> None:
        super().__init__("the certificate names no person or organization by a Dominican identifier")


class HolderMismatchError(CertificateRefusedError):
    """The certificate is another taxpayer's, and its holder cannot act for the company."""

    code: ClassVar[str] = "certificate_holder_mismatch"

    def __init__(self) -> None:
        super().__init__("the certificate belongs to another taxpayer than the company")


class CertificateAlreadyActiveError(ConflictError):
    """The company has an active certificate: another one replaces it by a rotation, which names it."""

    code: ClassVar[str] = "certificate_already_active"

    def __init__(self) -> None:
        super().__init__("the company has an active certificate: rotate it to install another")


class CertificateChangedError(ConflictError):
    """A rotation named a generation that is not the active certificate's (any more)."""

    code: ClassVar[str] = "certificate_changed"

    def __init__(self, generation: int) -> None:
        super().__init__(f"the company's active certificate is not generation {generation}")


class NoActiveCertificateError(ConflictError):
    """The company has no certificate to sign with, or to rotate."""

    code: ClassVar[str] = "no_active_certificate"

    def __init__(self, company: UUID) -> None:
        super().__init__(f"company {company} has no active certificate")


class ActiveCertificateExpiredError(ConflictError):
    """The company's active certificate has expired: it signs nothing until another is installed.

    The same code as ``CertificateExpiredError``, the refusal of an expired certificate at upload: both say
    that the certificate's validity has ended. This one is a state of the company (``CONFLICT``), that one a
    property of what was sent (``INVALID``).
    """

    code: ClassVar[str] = "certificate_expired"

    def __init__(self, not_after: datetime) -> None:
        super().__init__(f"the company's certificate expired on {not_after.isoformat()}")


class CertificateNotFoundError(NotFoundError):
    """No certificate by this id in the company."""

    code: ClassVar[str] = "certificate_not_found"

    def __init__(self, certificate: UUID) -> None:
        super().__init__(f"certificate {certificate} was not found")


class DocumentRefusedError(InvalidInputError):
    """A document vault does not sign; each subclass says why. The message never quotes the document."""

    code: ClassVar[str] = "document_refused"


class DocumentTooLargeError(DocumentRefusedError):
    """The document is larger than the limit: refused unread."""

    code: ClassVar[str] = "document_too_large"

    def __init__(self, limit: int) -> None:
        super().__init__(f"a document is at most {limit} bytes")


class DocumentMalformedError(DocumentRefusedError):
    """The document is not well-formed XML. The parser's own message quotes it, so it is not repeated."""

    code: ClassVar[str] = "document_malformed"

    def __init__(self) -> None:
        super().__init__("the document is not well-formed XML")


class DocumentDoctypeError(DocumentRefusedError):
    """The document declares a DOCTYPE: vault reads no DTD, internal or external."""

    code: ClassVar[str] = "document_doctype_refused"

    def __init__(self) -> None:
        super().__init__("a document with a DOCTYPE is refused: vault reads no DTD")


class DocumentSignedError(DocumentRefusedError):
    """The document already holds an XML signature: an enveloped signature would not cover it alone."""

    code: ClassVar[str] = "document_already_signed"

    def __init__(self) -> None:
        super().__init__("the document already holds an XML signature")


class SeedMalformedError(DocumentRefusedError):
    """What was to be signed as a seed is not the ``SemillaModel`` DGII hands out."""

    code: ClassVar[str] = "seed_malformed"

    def __init__(self) -> None:
        super().__init__("a seed is DGII's SemillaModel: a valor, then a fecha")


class InvalidReferenceError(InvalidInputError):
    """A document's reference that is not 1 to 64 visible ASCII characters."""

    code: ClassVar[str] = "invalid_reference"

    def __init__(self, text: str) -> None:
        super().__init__(f"a reference is 1 to 64 visible ASCII characters, not {excerpt(text)}")
```

`services/vault/src/ledger_vault/domain/holder.py`:

```python
"""Whose a certificate is, read from its subject as the Dominican certification authorities write it.

DGII has e-CF signed with a "certificado digital para procedimiento tributario": a qualified certificate of a
natural person who acts for the taxpayer, the taxpayer itself when it is a person or its authorized
representative (DGII, *Preguntas frecuentes e-CF generales*, 2.3.1 and 16.1.4). The authorities INDOTEL
accredits for it (Viafirma/Avansi, Digifirma of the Cámara de Comercio y Producción de Santo Domingo,
Novofirma of Novosit; ibid. 13.1.2) identify the holder in the subject:

* a natural person by ``serialNumber`` (2.5.4.5) in the form of ETSI EN 319 412-1 §5.1.3, ``IDCDO-<cédula>``
  or ``PAS<country>-<passport>`` (Viafirma's policies since 2021: *CP-VIAFIRMA-DO-PI-TAX-SW* v2.0, §3.1.2 and
  §7.1.2);
* an organization (an electronic seal) by ``organizationIdentifier`` (2.5.4.97), ``VATDO-<RNC>`` (§5.1.4;
  *CP-VIAFIRMA-ELECTRONIC-SEAL* v2.0, §7.1.2);
* in certificates issued before those conventions, by a ``serialNumber`` that is the cédula itself (Avansi's
  policies until 2021, *PC-AVS-PR* 1.2, §7.1.2).

Each convention is a ``HolderReader``, and the first that recognizes the subject decides. The published
policies of Digifirma and Novofirma were not found; a convention of theirs that differs is one more reader.
A subject none of them recognizes is refused: vault could not say whose the certificate is.

Whether a holder may sign for a company (``bind_holder``): a certificate that names the company's own tax id
is the taxpayer's; a natural person's that names another may be its representative's, which DGII, not vault,
can confirm (it refuses an e-CF signed by someone the taxpayer did not authorize). ``CustodyPolicy`` can
demand the taxpayer's own certificate instead.
"""

import re
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol, final, override
lazy from collections.abc import Sequence

from ledger_kernel.countries.do import TaxId, TaxIdKind
from ledger_kernel.errors import InvalidInputError
from ledger_vault.domain.errors import HolderMismatchError, HolderUnknownError

SERIAL_NUMBER: Final = "2.5.4.5"
ORGANIZATION_IDENTIFIER: Final = "2.5.4.97"
COMMON_NAME: Final = "2.5.4.3"
ORGANIZATION_NAME: Final = "2.5.4.10"
DOMINICAN_REPUBLIC: Final = "DO"
_SHOWN: Final = 3
"""How many of an identifier's last characters ``Holder.hint`` shows."""
_SEMANTICS: Final = re.compile(
    r"(?P<scheme>IDC|PAS|VAT)(?P<country>[A-Z]{2})-(?P<value>[A-Za-z0-9]{1,32})", re.ASCII
)
"""ETSI EN 319 412-1's semantics identifier: what it is (three letters), the country, a hyphen, the value."""


type SubjectAttribute = tuple[str, str]
"""One attribute of a certificate's subject: its OID (dotted) and its value."""


class HolderKind(StrEnum):
    """Whether a certificate is a person's or an organization's (an electronic seal)."""

    PERSON = "person"
    ORGANIZATION = "organization"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Holder:
    """Whose a certificate is. Its name and identifier are personal data: never in a ``repr`` or a log."""

    kind: HolderKind
    name: str = field(repr=False)
    """The common name (or the organization's name), as the certificate states it; may be empty."""
    identifier: str = field(repr=False)
    """The identifier without its prefix: the cédula, the RNC or the passport number."""
    tax_id: TaxId | None
    """The Dominican tax id the identifier is (``TaxId``'s own ``repr`` masks it); ``None`` for a passport."""

    @property
    def hint(self) -> str:
        """The identifier with all but its last three characters masked, as a person recognizes it."""
        shown = self.identifier[-_SHOWN:]
        return "*" * (len(self.identifier) - len(shown)) + shown


class HolderReader(Protocol, metaclass=ABCMeta):
    """One convention for naming a certificate's holder in its subject.

    The method is abstract so that it needs no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    def read(self, subject: Sequence[SubjectAttribute]) -> Holder | None:
        """The holder ``subject`` names by this convention; ``None`` when it does not follow it."""


def _first(subject: Sequence[SubjectAttribute], oid: str) -> str | None:
    return next((value for name, value in subject if name == oid), None)


def _name(subject: Sequence[SubjectAttribute], *oids: str) -> str:
    """The first of ``oids`` the subject holds; empty when it holds none."""
    return next((found for oid in oids if (found := _first(subject, oid)) is not None), "")


def _tax_id(text: str) -> TaxId | None:
    try:
        return TaxId.parse(text)
    except InvalidInputError:
        return None


def _semantics(subject: Sequence[SubjectAttribute], oid: str) -> re.Match[str] | None:
    found = _first(subject, oid)
    return None if found is None else _SEMANTICS.fullmatch(found)


@final
class EtsiOrganizationIdentifier(HolderReader):
    """``organizationIdentifier`` ``VATDO-<RNC>``: an organization's electronic seal (§5.1.4)."""

    @override
    def read(self, subject: Sequence[SubjectAttribute]) -> Holder | None:
        match = _semantics(subject, ORGANIZATION_IDENTIFIER)
        if match is None or match["scheme"] != "VAT" or match["country"] != DOMINICAN_REPUBLIC:
            return None
        tax_id = _tax_id(match["value"])
        if tax_id is None:
            return None
        return Holder(
            kind=HolderKind.ORGANIZATION,
            name=_name(subject, ORGANIZATION_NAME, COMMON_NAME),
            identifier=match["value"],
            tax_id=tax_id,
        )


@final
class EtsiPersonIdentifier(HolderReader):
    """``serialNumber`` ``IDCDO-<cédula>`` or ``PAS<country>-<passport>``: a natural person (§5.1.3).

    An identity card of another country names a person vault knows no tax id of.
    """

    @override
    def read(self, subject: Sequence[SubjectAttribute]) -> Holder | None:
        match = _semantics(subject, SERIAL_NUMBER)
        if match is None or match["scheme"] == "VAT":
            return None
        dominican = match["scheme"] == "IDC" and match["country"] == DOMINICAN_REPUBLIC
        tax_id = _tax_id(match["value"]) if dominican else None
        if dominican and tax_id is None:
            return None
        return Holder(
            kind=HolderKind.PERSON,
            name=_name(subject, COMMON_NAME),
            identifier=match["value"],
            tax_id=tax_id,
        )


@final
class BareSerialNumber(HolderReader):
    """``serialNumber`` that is the cédula (or an RNC) itself: certificates older than ETSI's form."""

    @override
    def read(self, subject: Sequence[SubjectAttribute]) -> Holder | None:
        found = _first(subject, SERIAL_NUMBER)
        tax_id = None if found is None else _tax_id(found)
        if tax_id is None:
            return None
        kind = HolderKind.ORGANIZATION if tax_id.kind is TaxIdKind.RNC else HolderKind.PERSON
        return Holder(
            kind=kind,
            name=_name(subject, COMMON_NAME, ORGANIZATION_NAME),
            identifier=str(tax_id),
            tax_id=tax_id,
        )


DOMINICAN_CONVENTIONS: Final[tuple[HolderReader, ...]] = (
    EtsiOrganizationIdentifier(),
    EtsiPersonIdentifier(),
    BareSerialNumber(),
)
"""The conventions of the Dominican authorities, in the order they are tried."""


def read_holder(
    subject: Sequence[SubjectAttribute], readers: Sequence[HolderReader] = DOMINICAN_CONVENTIONS
) -> Holder:
    """Whose the certificate with ``subject`` is, by the first convention that recognizes it.

    ``HolderUnknownError`` when none does.
    """
    for reader in readers:
        holder = reader.read(subject)
        if holder is not None:
            return holder
    raise HolderUnknownError


class HolderBinding(StrEnum):
    """Why a holder may sign for a company."""

    TAXPAYER = "taxpayer"
    """The certificate names the company's own tax id."""
    REPRESENTATIVE = "representative"
    """A natural person acting for the company: DGII checks that the taxpayer authorized them."""


def bind_holder(holder: Holder, taxpayer: TaxId, *, representatives: bool) -> HolderBinding:
    """Whether ``holder`` may sign for the taxpayer ``taxpayer``, and why; ``HolderMismatchError`` if not.

    The taxpayer's own certificate always may. A natural person's naming someone else may when
    ``representatives`` allows representatives. An organization's naming another tax id never may.
    """
    if holder.tax_id == taxpayer:
        return HolderBinding.TAXPAYER
    if representatives and holder.kind is HolderKind.PERSON:
        return HolderBinding.REPRESENTATIVE
    raise HolderMismatchError
```

`services/vault/src/ledger_vault/domain/keys.py`:

```python
"""Key material in vault's hands, and what binds a sealed key to its certificate and company.

Plaintext key material (a private key, a data key) lives in a ``KeyMaterial``: one ``bytearray`` that is
overwritten with zeros when the operation that needed it ends. That is best effort, and Python allows no more:

* an immutable ``bytes`` cannot be wiped, and some arrive as one (the plaintext data key a boto3 call
  returns, the DER ``cryptography`` hands out of a key); they are copied into a ``KeyMaterial`` at once and
  dropped, and their memory is freed, not cleared;
* ``cryptography`` and OpenSSL hold their own copies while a key object lives (OpenSSL clears an RSA key's
  numbers when it frees them; when the object is collected is the garbage collector's business);
* the operating system may have swapped a page out, and a core dump holds what the process held.

What ``KeyMaterial`` does promise: the bytes are never in its ``repr``, it is wiped however the block that
holds it ends, and a wiped one refuses to be read. The pod's own measures do the rest (no core dumps, no swap,
a read-only file system).
"""

from dataclasses import dataclass
from typing import ClassVar, Final, Self, final, override
lazy from types import TracebackType

from ledger_kernel.errors import LedgerError
lazy from ledger_kernel.ids import Tenant
lazy from ledger_vault.domain.ids import CertificateId

_PURPOSE: Final = "ledger.vault.v1.private-key"
"""What the associated data of a sealed private key says it is, so it cannot pass for anything else."""


class WipedKeyError(LedgerError):
    """Key material was read after it was wiped: a defect of the caller."""

    code: ClassVar[str] = "wiped_key"

    def __init__(self) -> None:
        super().__init__("key material was read after it was wiped")


@final
class KeyMaterial:
    """Plaintext key bytes for the length of one operation: ``with material: ...`` wipes them at the end."""

    __slots__ = ("_buffer", "_wiped")

    def __init__(self, buffer: bytearray) -> None:
        self._buffer = buffer
        self._wiped = False

    @classmethod
    def copied(cls, data: bytes) -> Self:
        """Material holding a copy of ``data``; the caller drops ``data`` at once (it cannot be wiped)."""
        return cls(bytearray(data))

    @property
    def buffer(self) -> bytearray:
        """The bytes, for the one call that needs them. ``WipedKeyError`` once wiped."""
        if self._wiped:
            raise WipedKeyError
        return self._buffer

    @property
    def wiped(self) -> bool:
        """Whether the bytes were overwritten."""
        return self._wiped

    def wipe(self) -> None:
        """Overwrite the bytes with zeros; harmless when done already."""
        self._buffer[:] = bytes(len(self._buffer))
        self._wiped = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.wipe()

    @override
    def __repr__(self) -> str:
        return "KeyMaterial(<withheld>)"


@final
@dataclass(frozen=True, slots=True)
class KeyContext:
    """What a sealed key is bound to: its certificate and the company that owns it.

    The same binding twice: as the associated data of the AES-GCM ciphertext of the key, and as the KMS
    encryption context of its data key. A key sealed for one certificate or company cannot be opened as
    another's, and the KMS's audit trail (CloudTrail) names the certificate whose data key it unwrapped.
    """

    tenant: Tenant
    certificate: CertificateId

    def encryption_context(self) -> dict[str, str]:
        """The KMS encryption context of the key's data key."""
        return {
            "ledger:organization": str(self.tenant.organization),
            "ledger:company": str(self.tenant.company),
            "ledger:certificate": str(self.certificate),
        }

    def associated_data(self) -> bytes:
        """The AES-GCM associated data of the sealed key."""
        parts = (_PURPOSE, str(self.tenant.organization), str(self.tenant.company), str(self.certificate))
        return "|".join(parts).encode("ascii")
```

`services/vault/src/ledger_vault/domain/events.py`:

```python
"""What vault announces (§4.1): the facts other services build on, as the domain states them.

Each carries the certificate's generation, the company's certificate version: it only grows.
"""

from dataclasses import dataclass
from typing import final
lazy from datetime import datetime

lazy from ledger_kernel.ids import Tenant
lazy from ledger_vault.domain.ids import CertificateId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateUploaded:
    """A company's first certificate (or its first after its last was retired) was installed."""

    tenant: Tenant
    certificate: CertificateId
    generation: int
    not_after: datetime


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateRotated:
    """A certificate replaced the company's active one, which is retired and kept."""

    tenant: Tenant
    certificate: CertificateId
    generation: int
    not_after: datetime
    retired: CertificateId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateRetired:
    """The company's active certificate was retired without a replacement: the company was deactivated."""

    tenant: Tenant
    certificate: CertificateId
    generation: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateExpiring:
    """The active certificate expires within ``days_left`` days (30, 7 or 1); each announced once."""

    tenant: Tenant
    certificate: CertificateId
    generation: int
    not_after: datetime
    days_left: int


type VaultEvent = CertificateUploaded | CertificateRotated | CertificateRetired | CertificateExpiring
```

`services/vault/src/ledger_vault/domain/certificate.py`:

```python
"""A company's signing certificate in vault's custody: what it states, whose it is, and its key, sealed.

A company has at most one active certificate, the one all its signatures use. Installing another retires it
(rotation); a retired certificate is kept, with its sealed key, so that what it signed stays traceable to it.
Each certificate a company installs takes the next generation: callers name it to rotate (compare-and-set),
and identity's API answers it as the ``ETag`` of the company's certificate.

Before a certificate is installed it is admitted (``admit``): an RSA key of at least ``MIN_RSA_BITS``, a key
usage that allows a signature, within its validity, chaining to an authority of the trust store, and a holder
that may sign for the company (``ledger_vault.domain.holder``). The checks run in that order, so a bundle with
several problems is refused for the first.
"""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final, final
lazy from datetime import datetime

from ledger_vault.domain.errors import (
    ActiveCertificateExpiredError,
    CertificateExpiredError,
    CertificateKeyError,
    CertificateNotYetValidError,
    CertificateUntrustedError,
    CertificateUsageError,
)
from ledger_vault.domain.events import CertificateRetired
from ledger_vault.domain.holder import bind_holder, read_holder
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import Tenant
lazy from ledger_vault.domain.holder import Holder, HolderBinding, SubjectAttribute
lazy from ledger_vault.domain.ids import CertificateId

MIN_RSA_BITS: Final = 2048
RSA: Final = "rsa"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateFacts:
    """What a certificate states, and whether it chains to an accepted authority, as its bundle was read."""

    der: bytes = field(repr=False)
    """The certificate itself (DER): public, and what a signature's ``KeyInfo`` carries."""
    subject: tuple[SubjectAttribute, ...] = field(repr=False)
    """Its subject's attributes in order; they name a person."""
    issuer_name: str
    """The common name of the authority that issued it."""
    serial_number: str
    """Lowercase hexadecimal."""
    fingerprint: str
    """SHA-256 of ``der``, lowercase hexadecimal: the same certificate uploaded twice has the same one."""
    not_before: datetime
    not_after: datetime
    key_algorithm: str
    """``rsa``, ``ec`` or ``other``."""
    key_bits: int
    signs: bool
    """Its key usage allows a digital signature or non-repudiation (or it states none)."""
    trusted: bool
    """It chains to an authority of the trust store, at the time it was read."""


@final
@dataclass(frozen=True, slots=True)
class CustodyPolicy:
    """What vault demands of a certificate beyond the checks every one passes."""

    min_rsa_bits: int = MIN_RSA_BITS
    representatives: bool = True
    """Whether a natural person's certificate may sign for a taxpayer other than that person."""


@final
@dataclass(frozen=True, slots=True)
class Admission:
    """A certificate admitted for a company: whose it is, and why it may sign for the company."""

    holder: Holder
    binding: HolderBinding


def admit(facts: CertificateFacts, *, taxpayer: TaxId, now: datetime, policy: CustodyPolicy) -> Admission:
    """Admit the certificate ``facts`` describes for the taxpayer ``taxpayer`` at ``now`` (see the module)."""
    if facts.key_algorithm != RSA or facts.key_bits < policy.min_rsa_bits:
        raise CertificateKeyError(
            algorithm=facts.key_algorithm, bits=facts.key_bits, minimum=policy.min_rsa_bits
        )
    if not facts.signs:
        raise CertificateUsageError
    if now < facts.not_before:
        raise CertificateNotYetValidError(facts.not_before)
    if now >= facts.not_after:
        raise CertificateExpiredError(facts.not_after)
    if not facts.trusted:
        raise CertificateUntrustedError
    holder = read_holder(facts.subject)
    return Admission(holder, bind_holder(holder, taxpayer, representatives=policy.representatives))


class CertificateStatus(StrEnum):
    """Whether a certificate signs for its company."""

    ACTIVE = "active"
    RETIRED = "retired"


class Retirement(StrEnum):
    """Why a certificate was retired."""

    ROTATED = "rotated"
    COMPANY_DEACTIVATED = "company_deactivated"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SealedKey:
    """A private key as vault keeps it: AES-256-GCM ciphertext under a data key the KMS wrapped (envelope)."""

    provider: str
    """The kind of KMS that wrapped the data key: ``aws-kms`` or ``local``."""
    key_id: str
    """The KMS key that wrapped it: an ARN, or the fingerprint of the local key."""
    wrapped_key: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)
    """The key (PKCS#8 DER) encrypted, with the GCM tag at its end."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Certificate:
    """A certificate in a company's custody, at its current state."""

    id: CertificateId
    tenant: Tenant
    generation: int
    status: CertificateStatus
    holder: Holder
    binding: HolderBinding
    der: bytes = field(repr=False)
    issuer_name: str
    serial_number: str
    fingerprint: str
    not_before: datetime
    not_after: datetime
    key_bits: int
    sealed: SealedKey
    uploaded_at: datetime
    uploaded_by: SubjectId
    retired_at: datetime | None = None
    retirement: Retirement | None = None

    def require_usable(self, now: datetime) -> None:
        """Refuse to sign with it once it has expired: ``ActiveCertificateExpiredError``."""
        if now >= self.not_after:
            raise ActiveCertificateExpiredError(self.not_after)

    def retire(self, reason: Retirement, *, now: datetime) -> tuple[Certificate, CertificateRetired]:
        """The certificate retired for ``reason``: it signs no more, and is kept."""
        retired = replace(self, status=CertificateStatus.RETIRED, retired_at=now, retirement=reason)
        return retired, CertificateRetired(
            tenant=self.tenant, certificate=self.id, generation=self.generation
        )
```

`services/vault/src/ledger_vault/domain/custody.py`:

```python
"""Installing a company's certificate: the first one, and the ones that replace it (rotation).

Both are idempotent by the certificate: installing the certificate that is already the company's active one
changes nothing and announces nothing, so a retried upload or rotation is harmless. Otherwise an upload needs
a company without an active certificate, and a rotation names the generation of the active one it replaces.
"""

from dataclasses import dataclass
from typing import final
lazy from datetime import datetime

from ledger_vault.domain.certificate import Certificate, CertificateStatus, Retirement
from ledger_vault.domain.errors import (
    CertificateAlreadyActiveError,
    CertificateChangedError,
    NoActiveCertificateError,
)
from ledger_vault.domain.events import CertificateRotated, CertificateUploaded
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.ids import Tenant
lazy from ledger_vault.domain.certificate import Admission, CertificateFacts, SealedKey
lazy from ledger_vault.domain.ids import CertificateId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Candidate:
    """A certificate admitted for a company, its key sealed under its own id, ready to be installed."""

    id: CertificateId
    tenant: Tenant
    facts: CertificateFacts
    admission: Admission
    sealed: SealedKey
    by: SubjectId
    """Who asked for it: the principal that uploaded it."""


@final
@dataclass(frozen=True, slots=True)
class Installation:
    """What installing a candidate leaves: the company's active certificate, the one retired, the event.

    No event when the candidate was the active certificate already: nothing changed.
    """

    certificate: Certificate
    retired: Certificate | None
    event: CertificateUploaded | CertificateRotated | None

    @property
    def changed(self) -> bool:
        """Whether the candidate was installed (``False``: it was the active certificate already)."""
        return self.event is not None


def _installed(candidate: Candidate, generation: int, now: datetime) -> Certificate:
    facts = candidate.facts
    return Certificate(
        id=candidate.id,
        tenant=candidate.tenant,
        generation=generation,
        status=CertificateStatus.ACTIVE,
        holder=candidate.admission.holder,
        binding=candidate.admission.binding,
        der=facts.der,
        issuer_name=facts.issuer_name,
        serial_number=facts.serial_number,
        fingerprint=facts.fingerprint,
        not_before=facts.not_before,
        not_after=facts.not_after,
        key_bits=facts.key_bits,
        sealed=candidate.sealed,
        uploaded_at=now,
        uploaded_by=candidate.by,
    )


def upload(
    current: Certificate | None, candidate: Candidate, *, last_generation: int, now: datetime
) -> Installation:
    """Install ``candidate`` as the company's first active certificate, after ``last_generation``.

    ``current`` is the company's active certificate: the same certificate again changes nothing; another one
    is a ``CertificateAlreadyActiveError`` (a rotation replaces it).
    """
    if current is not None:
        if current.fingerprint == candidate.facts.fingerprint:
            return Installation(current, None, None)
        raise CertificateAlreadyActiveError
    certificate = _installed(candidate, last_generation + 1, now)
    uploaded = CertificateUploaded(
        tenant=certificate.tenant,
        certificate=certificate.id,
        generation=certificate.generation,
        not_after=certificate.not_after,
    )
    return Installation(certificate, None, uploaded)


def rotate(
    current: Certificate | None, candidate: Candidate, *, replaces: int, now: datetime
) -> Installation:
    """Replace the company's active certificate, of generation ``replaces``, by ``candidate``.

    The same certificate again changes nothing (a retried rotation finds it active). Without an active
    certificate, ``NoActiveCertificateError``; when the active one is of another generation (another rotation
    won), ``CertificateChangedError``.
    """
    if current is None:
        raise NoActiveCertificateError(candidate.tenant.company)
    if current.fingerprint == candidate.facts.fingerprint:
        return Installation(current, None, None)
    if current.generation != replaces:
        raise CertificateChangedError(replaces)
    retired, _ = current.retire(Retirement.ROTATED, now=now)
    certificate = _installed(candidate, current.generation + 1, now)
    rotated = CertificateRotated(
        tenant=certificate.tenant,
        certificate=certificate.id,
        generation=certificate.generation,
        not_after=certificate.not_after,
        retired=current.id,
    )
    return Installation(certificate, retired, rotated)
```

`services/vault/src/ledger_vault/domain/signing.py`:

```python
"""Signatures vault makes, and what its audit keeps of each (§9): never the document, only its digest.

Every signature is recorded before it is handed back: who asked (the calling service, as it names itself),
for which company, with which certificate, for what (a document, or DGII's authentication seed), the SHA-256
of the document as it was received and, when the caller gives one, its reference (an e-NCF such as
``E310000000001``). The record is append-only (its table refuses updates and deletes).
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, final
lazy from datetime import datetime

from ledger_vault.domain.errors import InvalidReferenceError
lazy from ledger_kernel.ids import Tenant
lazy from ledger_vault.domain.ids import CertificateId, SignatureId

MAX_REFERENCE: Final = 64
_REFERENCE: Final = re.compile(rf"[!-~]{{1,{MAX_REFERENCE}}}", re.ASCII)
"""Visible ASCII without spaces: an e-NCF, a document id."""


class SigningPurpose(StrEnum):
    """What a signature is for, which decides the shape the signed document must have."""

    DOCUMENT = "document"
    """A document DGII receives: an e-CF, an approval, an annulment, a summary."""
    SEED = "seed"
    """DGII's authentication seed (``SemillaModel``), signed to obtain a token."""


def parse_reference(text: str) -> str | None:
    """A document's reference as the caller gave it, ``None`` for none; ``InvalidReferenceError`` if bad."""
    if not text:
        return None
    if _REFERENCE.fullmatch(text) is None:
        raise InvalidReferenceError(text)
    return text


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SignatureRecord:
    """The audit of one signature."""

    id: SignatureId
    tenant: Tenant
    certificate: CertificateId
    purpose: SigningPurpose
    document_sha256: bytes = field(repr=False)
    reference: str | None
    caller: str | None
    """The calling service as it names itself (unauthenticated until the mesh); ``None`` when unnamed."""
    signed_at: datetime
```

`services/vault/src/ledger_vault/domain/expiry.py`:

```python
"""When a company is told its certificate expires: 30, 7 and 1 day before, each announced once (§5.3)."""

from datetime import timedelta
from typing import Final
lazy from datetime import datetime

THRESHOLDS: Final = (30, 7, 1)
"""Days before expiry at which an announcement is due, the widest first."""
HORIZON: Final = timedelta(days=max(THRESHOLDS))
"""How far ahead a certificate has to expire for any announcement to be due."""


def due_threshold(not_after: datetime, now: datetime, announced: int | None) -> int | None:
    """The announcement due at ``now`` for a certificate expiring at ``not_after``, if any.

    The smallest threshold reached, unless ``announced`` (the smallest announced so far) already covers it:
    a certificate uploaded five days before its expiry is announced at 7 days, never at 30 as well, and one
    announced at 7 days is announced again at 1.
    """
    left = not_after - now
    reached = [days for days in THRESHOLDS if left <= timedelta(days=days)]
    if not reached:
        return None
    due = min(reached)
    if announced is not None and announced <= due:
        return None
    return due
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/vault -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`ledger_vault` included).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/vault
git commit -m "feat(vault): the service's package and its domain

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Vault's use cases and their ports

**Files:**
- Create: `services/vault/src/ledger_vault/application/__init__.py`, `ports.py`, `commands.py`, `queries.py`
- Modify: `pyproject.toml` (root: import-linter)
- Test: `services/vault/tests/test_application.py`

**Interfaces:**
- Consumes: the domain (Task 4).
- Produces: `ledger_vault.application.ports`: protocols `Certificates` (`lock`, `active`, `last_generation`, `get`, `newest(limit)`, `add`, `retired`), `Signatures` (`add`), `ExpiryWatch` (`watch`, `forget`, `claim(certificate, days) -> bool`), `Events` (`publish`), `Units` (`unit(tenant) -> AbstractAsyncContextManager[VaultUnit]`), `ExpiryCandidates` (`due(until, limit) -> Sequence[Watched]`), `BundleReader` (`open(bundle, password, *, now) -> OpenedBundle`), `KeyCustody` (`seal`, `unseal`), `XmlSigner` (`sign(document, key, certificate, purpose) -> SignedDocument`); dataclasses `VaultUnit(tenant, certificates, signatures, watch, events, now)`, `Watched`, `OpenedBundle(facts, key)`, `SignedDocument(content, document_sha256)`. `ledger_vault.application.commands`: `CertificateUpload(tenant, taxpayer, requested_by, bundle, password)`, `Signed(content, certificate, signature, document_sha256)`, `VaultCommands(*, units, reader, custody, signer, policy, clock)` (a frozen dataclass: its fields are its collaborators) with `async upload(request) -> Installation`, `async rotate(request, *, replaces) -> Installation`, `async sign(tenant, document, purpose, *, reference, caller) -> Signed`, `async announce_expiring(candidates, *, limit=500) -> int`; `async retire_company(unit) -> CertificateId | None`. `ledger_vault.application.queries`: `LIST_LIMIT = 100`, `VaultQueries(units)` with `certificate(tenant, certificate)`, `certificates(tenant)`.

- [ ] **Step 1: Layering**

Root `pyproject.toml`: "Vault is hexagonal" `layers = ["application", "domain"]`, and:

```toml
[[tool.importlinter.contracts]]
name = "Vault's use cases know ports, not adapters or frameworks"
type = "forbidden"
source_modules = ["ledger_vault.application"]
forbidden_modules = [
    "ledger_contracts", "ledger_platform", "pydantic", "sqlalchemy", "fastapi", "starlette", "grpc", "nats",
    "google", "psycopg", "alembic", "cryptography", "lxml", "boto3", "botocore",
]
```

- [ ] **Step 2: Write the failing tests**

`services/vault/tests/test_application.py` (fakes of every port; the PostgreSQL ones come in Task 10):

```python
import hashlib
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Sequence

import pytest

from ledger_kernel.access import SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_vault.application.commands import CertificateUpload, VaultCommands, retire_company
from ledger_vault.application.ports import (
    BundleReader,
    Certificates,
    Events,
    ExpiryCandidates,
    ExpiryWatch,
    KeyCustody,
    OpenedBundle,
    Signatures,
    SignedDocument,
    Units,
    VaultUnit,
    Watched,
    XmlSigner,
)
from ledger_vault.application.queries import VaultQueries
from ledger_vault.domain.certificate import (
    CertificateFacts,
    CertificateStatus,
    CustodyPolicy,
    Retirement,
    SealedKey,
)
from ledger_vault.domain.errors import (
    ActiveCertificateExpiredError,
    CertificateAlreadyActiveError,
    CertificateExpiredError,
    CertificateNotFoundError,
    NoActiveCertificateError,
)
from ledger_vault.domain.events import (
    CertificateExpiring,
    CertificateRetired,
    CertificateRotated,
    CertificateUploaded,
)
from ledger_vault.domain.holder import COMMON_NAME, SERIAL_NUMBER
from ledger_vault.domain.keys import KeyMaterial
from ledger_vault.domain.signing import SigningPurpose
lazy from ledger_vault.domain.certificate import Certificate
lazy from ledger_vault.domain.events import VaultEvent
lazy from ledger_vault.domain.ids import CertificateId
lazy from ledger_vault.domain.keys import KeyContext
lazy from ledger_vault.domain.signing import SignatureRecord

NOW: Final = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
RNC: Final = TaxId.parse("101850043")
FACTS: Final = CertificateFacts(
    der=b"certificate",
    subject=((COMMON_NAME, "JUAN PEREZ"), (SERIAL_NUMBER, "IDCDO-00112345678")),
    issuer_name="LEDGER TEST CA",
    serial_number="1a",
    fingerprint="f" * 64,
    not_before=NOW - timedelta(days=1),
    not_after=NOW + timedelta(days=365),
    key_algorithm="rsa",
    key_bits=2048,
    signs=True,
    trusted=True,
)


@final
class State:
    """Vault's state, as a fake keeps it: every company's, so a test can look across."""

    def __init__(self) -> None:
        self.certificates: dict[CertificateId, Certificate] = {}
        self.signatures: list[SignatureRecord] = []
        self.watched: dict[CertificateId, Watched] = {}
        self.events: list[VaultEvent] = []
        self.locks = 0


@final
class FakeCertificates(Certificates):
    def __init__(self, state: State, tenant: Tenant) -> None:
        self._state, self._tenant = state, tenant

    def _mine(self) -> list[Certificate]:
        return [
            certificate
            for certificate in self._state.certificates.values()
            if certificate.tenant == self._tenant
        ]

    @override
    async def lock(self) -> None:
        self._state.locks += 1

    @override
    async def active(self) -> Certificate | None:
        return next((c for c in self._mine() if c.status is CertificateStatus.ACTIVE), None)

    @override
    async def last_generation(self) -> int:
        return max((certificate.generation for certificate in self._mine()), default=0)

    @override
    async def get(self, certificate: CertificateId) -> Certificate | None:
        return next((c for c in self._mine() if c.id == certificate), None)

    @override
    async def newest(self, limit: int) -> tuple[Certificate, ...]:
        return tuple(sorted(self._mine(), key=lambda certificate: -certificate.generation)[:limit])

    @override
    async def add(self, certificate: Certificate) -> None:
        self._state.certificates[certificate.id] = certificate

    @override
    async def retired(self, certificate: Certificate) -> None:
        self._state.certificates[certificate.id] = certificate


@final
class FakeSignatures(Signatures):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def add(self, record: SignatureRecord) -> None:
        self._state.signatures.append(record)


@final
class FakeWatch(ExpiryWatch, ExpiryCandidates):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def watch(self, certificate: Certificate) -> None:
        self._state.watched[certificate.id] = Watched(
            certificate=certificate.id,
            tenant=certificate.tenant,
            generation=certificate.generation,
            not_after=certificate.not_after,
            announced=None,
        )

    @override
    async def forget(self, certificate: CertificateId) -> None:
        self._state.watched.pop(certificate, None)

    @override
    async def claim(self, certificate: CertificateId, days: int) -> bool:
        watched = self._state.watched[certificate]
        if watched.announced is not None and watched.announced <= days:
            return False
        self._state.watched[certificate] = replace(watched, announced=days)
        return True

    @override
    async def due(self, until: datetime, limit: int) -> Sequence[Watched]:
        due = sorted(
            (w for w in self._state.watched.values() if w.not_after <= until), key=lambda w: w.not_after
        )
        return due[:limit]


@final
class FakeEvents(Events):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def publish(self, event: VaultEvent) -> None:
        self._state.events.append(event)


@final
class FakeUnits(Units):
    def __init__(self, state: State) -> None:
        self._state = state

    def bound(self, tenant: Tenant) -> VaultUnit:
        return VaultUnit(
            tenant=tenant,
            certificates=FakeCertificates(self._state, tenant),
            signatures=FakeSignatures(self._state),
            watch=FakeWatch(self._state),
            events=FakeEvents(self._state),
            now=NOW,
        )

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[VaultUnit]:
        yield self.bound(tenant)


@final
class FakeReader(BundleReader):
    def __init__(self) -> None:
        self.facts = FACTS
        self.keys: list[KeyMaterial] = []

    @override
    async def open(self, bundle: bytes, password: str, *, now: datetime) -> OpenedBundle:
        key = KeyMaterial.copied(bundle)
        self.keys.append(key)
        return OpenedBundle(replace(self.facts, fingerprint=hashlib.sha256(bundle).hexdigest()), key)


@final
class FakeCustody(KeyCustody):
    @override
    async def seal(self, key: KeyMaterial, context: KeyContext) -> SealedKey:
        return SealedKey(
            provider="fake",
            key_id=str(context.certificate),
            wrapped_key=b"",
            nonce=b"",
            ciphertext=bytes(key.buffer),
        )

    @override
    async def unseal(self, sealed: SealedKey, context: KeyContext) -> KeyMaterial:
        assert sealed.key_id == str(context.certificate)
        return KeyMaterial.copied(sealed.ciphertext)


@final
class FakeSigner(XmlSigner):
    def __init__(self) -> None:
        self.keys: list[KeyMaterial] = []

    @override
    async def sign(
        self, document: bytes, key: KeyMaterial, certificate: bytes, purpose: SigningPurpose
    ) -> SignedDocument:
        self.keys.append(key)
        return SignedDocument(document + b"<signed/>", hashlib.sha256(document).digest())


@final
class Vault:
    def __init__(self) -> None:
        self.state = State()
        self.units = FakeUnits(self.state)
        self.reader = FakeReader()
        self.signer = FakeSigner()
        self.moment = NOW
        self.commands = VaultCommands(
            units=self.units,
            reader=self.reader,
            custody=FakeCustody(),
            signer=self.signer,
            policy=CustodyPolicy(),
            clock=lambda: self.moment,
        )
        self.queries = VaultQueries(self.units)


def upload(bundle: bytes = b"bundle-1") -> CertificateUpload:
    return CertificateUpload(
        tenant=TENANT, taxpayer=RNC, requested_by=SubjectId("2001"), bundle=bundle, password="p"
    )


@pytest.fixture
def vault() -> Vault:
    return Vault()


async def test_an_upload_installs_the_first_certificate_and_says_so(vault: Vault) -> None:
    installed = await vault.commands.upload(upload())
    assert installed.changed
    assert isinstance(vault.state.events[0], CertificateUploaded)
    assert vault.state.locks == 1
    assert installed.certificate.id in vault.state.watched
    assert all(key.wiped for key in vault.reader.keys)


async def test_uploading_the_same_certificate_again_changes_nothing(vault: Vault) -> None:
    first = await vault.commands.upload(upload())
    again = await vault.commands.upload(upload())
    assert (again.changed, again.certificate.id) == (False, first.certificate.id)
    assert len(vault.state.events) == 1
    with pytest.raises(CertificateAlreadyActiveError):
        await vault.commands.upload(upload(b"bundle-2"))


async def test_a_refused_certificate_is_wiped_and_nothing_is_kept(vault: Vault) -> None:
    vault.reader.facts = replace(FACTS, not_after=NOW)
    with pytest.raises(CertificateExpiredError):
        await vault.commands.upload(upload())
    assert vault.state.certificates == {}
    assert all(key.wiped for key in vault.reader.keys)


async def test_a_rotation_retires_the_active_certificate(vault: Vault) -> None:
    first = await vault.commands.upload(upload())
    rotated = await vault.commands.rotate(upload(b"bundle-2"), replaces=1)
    assert rotated.certificate.generation == 2
    assert vault.state.certificates[first.certificate.id].status is CertificateStatus.RETIRED
    assert first.certificate.id not in vault.state.watched
    assert isinstance(vault.state.events[-1], CertificateRotated)
    replayed = await vault.commands.rotate(upload(b"bundle-2"), replaces=1)
    assert not replayed.changed


async def test_a_signature_is_made_with_the_active_certificate_and_audited(vault: Vault) -> None:
    installed = await vault.commands.upload(upload())
    signed = await vault.commands.sign(
        TENANT, b"<ECF/>", SigningPurpose.DOCUMENT, reference="E310000000001", caller="fiscal-do"
    )
    (record,) = vault.state.signatures
    assert signed.content == b"<ECF/><signed/>"
    assert (record.certificate, record.purpose, record.reference, record.caller) == (
        installed.certificate.id,
        SigningPurpose.DOCUMENT,
        "E310000000001",
        "fiscal-do",
    )
    assert record.document_sha256 == hashlib.sha256(b"<ECF/>").digest() == signed.document_sha256
    assert (signed.certificate, signed.signature) == (installed.certificate.id, record.id)
    assert all(key.wiped for key in vault.signer.keys)


async def test_nothing_is_signed_without_a_usable_certificate(vault: Vault) -> None:
    with pytest.raises(NoActiveCertificateError):
        await vault.commands.sign(TENANT, b"<ECF/>", SigningPurpose.DOCUMENT, reference=None, caller=None)
    await vault.commands.upload(upload())
    vault.moment = NOW + timedelta(days=366)
    with pytest.raises(ActiveCertificateExpiredError):
        await vault.commands.sign(TENANT, b"<ECF/>", SigningPurpose.SEED, reference=None, caller=None)
    assert vault.state.signatures == []


async def test_a_deactivated_company_s_certificate_is_retired(vault: Vault) -> None:
    installed = await vault.commands.upload(upload())
    retired = await retire_company(vault.units.bound(TENANT))
    assert retired == installed.certificate.id
    certificate = vault.state.certificates[installed.certificate.id]
    assert (certificate.status, certificate.retirement) == (
        CertificateStatus.RETIRED,
        Retirement.COMPANY_DEACTIVATED,
    )
    assert vault.state.events[-1] == CertificateRetired(
        tenant=TENANT, certificate=installed.certificate.id, generation=1
    )
    assert await retire_company(vault.units.bound(TENANT)) is None


async def test_expiry_is_announced_once_per_threshold(vault: Vault) -> None:
    vault.reader.facts = replace(FACTS, not_after=NOW + timedelta(days=20))
    installed = await vault.commands.upload(upload())
    candidates = FakeWatch(vault.state)
    assert await vault.commands.announce_expiring(candidates) == 1
    assert await vault.commands.announce_expiring(candidates) == 0
    vault.moment = NOW + timedelta(days=14)
    assert await vault.commands.announce_expiring(candidates) == 1
    expiring = [event for event in vault.state.events if isinstance(event, CertificateExpiring)]
    assert [event.days_left for event in expiring] == [30, 7]
    assert all(event.certificate == installed.certificate.id for event in expiring)


async def test_an_expiry_another_replica_announced_is_not_announced_again(vault: Vault) -> None:
    vault.reader.facts = replace(FACTS, not_after=NOW + timedelta(days=20))
    installed = await vault.commands.upload(upload())
    stale = FakeWatch(vault.state)
    due = await stale.due(NOW + timedelta(days=30), 10)
    assert await vault.units.bound(TENANT).watch.claim(installed.certificate.id, 30)

    class Stale(ExpiryCandidates):
        @override
        async def due(self, until: datetime, limit: int) -> Sequence[Watched]:
            return due

    assert await vault.commands.announce_expiring(Stale()) == 0


async def test_certificates_are_read_in_their_company_only(vault: Vault) -> None:
    installed = await vault.commands.upload(upload())
    await vault.commands.rotate(upload(b"bundle-2"), replaces=1)
    assert (await vault.queries.certificate(TENANT, installed.certificate.id)).id == installed.certificate.id
    assert [c.generation for c in await vault.queries.certificates(TENANT)] == [2, 1]
    stranger = Tenant(TENANT.organization, CompanyId(new_id()))
    with pytest.raises(CertificateNotFoundError):
        await vault.queries.certificate(stranger, installed.certificate.id)
    assert await vault.queries.certificates(stranger) == ()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/vault/tests/test_application.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_vault.application'`.

- [ ] **Step 4: Implement**

`services/vault/src/ledger_vault/application/__init__.py`:

```python
"""vault's use cases, over ports: custody of certificates, signing, and the audit of both."""
```

`services/vault/src/ledger_vault/application/ports.py`:

```python
"""What vault's use cases need of the world.

Two kinds of port. Those of a unit of work (``VaultUnit``): vault's state as one transaction scoped to one
company sees it, read and changed by repositories that name that company in every statement while row-level
security enforces it again (§2, tenancy checked twice). And those that do the work around a unit, never inside
one, so that no transaction stays open across a network call or a long computation: opening a bundle
(``BundleReader``), sealing and unsealing keys through the KMS (``KeyCustody``), signing (``XmlSigner``).

The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol, final
lazy from collections.abc import Sequence
lazy from contextlib import AbstractAsyncContextManager
lazy from datetime import datetime

lazy from ledger_kernel.ids import Tenant
lazy from ledger_vault.domain.certificate import Certificate, CertificateFacts, SealedKey
lazy from ledger_vault.domain.events import VaultEvent
lazy from ledger_vault.domain.ids import CertificateId
lazy from ledger_vault.domain.keys import KeyContext, KeyMaterial
lazy from ledger_vault.domain.signing import SignatureRecord, SigningPurpose


class Certificates(Protocol, metaclass=ABCMeta):
    """The certificates of the unit's company."""

    @abstractmethod
    async def lock(self) -> None:
        """Serialize the changes of the company's custody: the next one waits until this unit ends."""

    @abstractmethod
    async def active(self) -> Certificate | None:
        """The company's active certificate, or ``None``."""

    @abstractmethod
    async def last_generation(self) -> int:
        """The generation of the company's newest certificate, active or retired; 0 before its first."""

    @abstractmethod
    async def get(self, certificate: CertificateId) -> Certificate | None:
        """One of the company's certificates, active or retired, or ``None``."""

    @abstractmethod
    async def newest(self, limit: int) -> tuple[Certificate, ...]:
        """The company's ``limit`` newest certificates, newest first."""

    @abstractmethod
    async def add(self, certificate: Certificate) -> None:
        """Keep a new certificate."""

    @abstractmethod
    async def retired(self, certificate: Certificate) -> None:
        """Keep ``certificate``, retired, in place of its active self."""


class Signatures(Protocol, metaclass=ABCMeta):
    """The audit of the signatures made for the unit's company: appended to, never changed."""

    @abstractmethod
    async def add(self, record: SignatureRecord) -> None:
        """Record a signature."""


class ExpiryWatch(Protocol, metaclass=ABCMeta):
    """The expiry dates of the active certificates, which the expiry announcements read across companies."""

    @abstractmethod
    async def watch(self, certificate: Certificate) -> None:
        """Watch the expiry of ``certificate``, the company's new active one."""

    @abstractmethod
    async def forget(self, certificate: CertificateId) -> None:
        """Stop watching a certificate that is no longer active."""

    @abstractmethod
    async def claim(self, certificate: CertificateId, days: int) -> bool:
        """Record that the ``days`` announcement of ``certificate`` was made; false when one was already."""


class Events(Protocol, metaclass=ABCMeta):
    """What the unit announces when it commits."""

    @abstractmethod
    async def publish(self, event: VaultEvent) -> None:
        """Announce ``event`` when, and only if, the unit commits (the outbox)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class VaultUnit:
    """One unit of work for one company: vault's state as one transaction sees it, and what it announces."""

    tenant: Tenant
    certificates: Certificates
    signatures: Signatures
    watch: ExpiryWatch
    events: Events
    now: datetime
    """The unit's time: what it records and announces happened then."""


class Units(Protocol, metaclass=ABCMeta):
    """Where units of work come from."""

    @abstractmethod
    def unit(self, tenant: Tenant) -> AbstractAsyncContextManager[VaultUnit]:
        """A unit of work for ``tenant``'s company: it commits when the block ends normally."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Watched:
    """An active certificate whose expiry is watched, as the announcements see it, outside any company."""

    certificate: CertificateId
    tenant: Tenant
    generation: int
    not_after: datetime
    announced: int | None
    """The smallest announcement made so far (in days), or ``None``."""


class ExpiryCandidates(Protocol, metaclass=ABCMeta):
    """The watched certificates of every company, read outside any company's scope."""

    @abstractmethod
    async def due(self, until: datetime, limit: int) -> Sequence[Watched]:
        """Up to ``limit`` watched certificates that expire before ``until``, soonest first."""


@final
@dataclass(frozen=True, slots=True)
class OpenedBundle:
    """A bundle opened: what its certificate states, and its private key (wiped by whoever holds it)."""

    facts: CertificateFacts
    key: KeyMaterial


class BundleReader(Protocol, metaclass=ABCMeta):
    """Opens the PKCS#12 bundles companies upload."""

    @abstractmethod
    async def open(self, bundle: bytes, password: str, *, now: datetime) -> OpenedBundle:
        """The certificate and key of ``bundle``, its chain checked at ``now``; a refusal if it is not one."""


class KeyCustody(Protocol, metaclass=ABCMeta):
    """Seals private keys for storage and unseals them for one operation (envelope encryption, §9)."""

    @abstractmethod
    async def seal(self, key: KeyMaterial, context: KeyContext) -> SealedKey:
        """``key`` encrypted under a new data key, which the KMS wraps, both bound to ``context``."""

    @abstractmethod
    async def unseal(self, sealed: SealedKey, context: KeyContext) -> KeyMaterial:
        """The key ``sealed`` holds; the caller wipes it as soon as it is done."""


@final
@dataclass(frozen=True, slots=True)
class SignedDocument:
    """A document with its enveloped signature, and the SHA-256 of the document as it was received."""

    content: bytes = field(repr=False)
    document_sha256: bytes = field(repr=False)


class XmlSigner(Protocol, metaclass=ABCMeta):
    """Signs XML documents the way DGII verifies them (§5.1)."""

    @abstractmethod
    async def sign(
        self, document: bytes, key: KeyMaterial, certificate: bytes, purpose: SigningPurpose
    ) -> SignedDocument:
        """``document`` with an enveloped signature by ``key``, whose certificate (DER) it carries."""
```

`services/vault/src/ledger_vault/application/commands.py`:

```python
"""What changes vault's state, and the signatures it makes (Decisions 3 to 5).

The work outside a transaction comes first and never inside one: a bundle is opened and its key sealed
through the KMS before the unit that installs it begins; a key is unsealed and a document signed between the
unit that reads the active certificate and the one that records the signature. A change of a company's custody
takes the company's lock first, so two at once serialize and the second sees the first's result. A signature
is handed back only once its audit has committed.
"""

from dataclasses import dataclass, field
from typing import Final, final
lazy from collections.abc import Callable
lazy from datetime import datetime

from ledger_kernel.ids import new_id
from ledger_vault.domain import custody
from ledger_vault.domain.certificate import Retirement, admit
from ledger_vault.domain.custody import Candidate
from ledger_vault.domain.errors import NoActiveCertificateError
from ledger_vault.domain.events import CertificateExpiring
from ledger_vault.domain.expiry import HORIZON, due_threshold
from ledger_vault.domain.ids import CertificateId, SignatureId
from ledger_vault.domain.keys import KeyContext
from ledger_vault.domain.signing import SignatureRecord
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import Tenant
lazy from ledger_vault.application.ports import (
    BundleReader,
    ExpiryCandidates,
    KeyCustody,
    Units,
    VaultUnit,
    XmlSigner,
)
lazy from ledger_vault.domain.certificate import CustodyPolicy
lazy from ledger_vault.domain.custody import Installation
lazy from ledger_vault.domain.signing import SigningPurpose

ANNOUNCEMENTS_PER_PASS: Final = 500


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateUpload:
    """A certificate a company asked vault to keep: its bundle and password are never in a ``repr``."""

    tenant: Tenant
    taxpayer: TaxId
    requested_by: SubjectId
    bundle: bytes = field(repr=False)
    password: str = field(repr=False)


@final
@dataclass(frozen=True, slots=True)
class Signed:
    """A signed document, the certificate that signed it, and its audit."""

    content: bytes = field(repr=False)
    certificate: CertificateId
    signature: SignatureId
    document_sha256: bytes = field(repr=False)


async def _record(unit: VaultUnit, installation: Installation) -> None:
    """Keep what an installation changed and announce it; nothing when it changed nothing."""
    if installation.event is None:
        return
    if installation.retired is not None:
        await unit.certificates.retired(installation.retired)
        await unit.watch.forget(installation.retired.id)
    await unit.certificates.add(installation.certificate)
    await unit.watch.watch(installation.certificate)
    await unit.events.publish(installation.event)


async def retire_company(unit: VaultUnit) -> CertificateId | None:
    """Retire the active certificate of the unit's company, deactivated; which one, if any."""
    await unit.certificates.lock()
    current = await unit.certificates.active()
    if current is None:
        return None
    retired, event = current.retire(Retirement.COMPANY_DEACTIVATED, now=unit.now)
    await unit.certificates.retired(retired)
    await unit.watch.forget(current.id)
    await unit.events.publish(event)
    return current.id


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class VaultCommands:
    """vault's use cases that change state or sign (see the module)."""

    units: Units
    reader: BundleReader
    custody: KeyCustody
    signer: XmlSigner
    policy: CustodyPolicy
    clock: Callable[[], datetime]

    async def _candidate(self, request: CertificateUpload) -> Candidate:
        """The request's certificate opened, admitted for its company and its key sealed; the key is wiped."""
        now = self.clock()
        opened = await self.reader.open(request.bundle, request.password, now=now)
        with opened.key as key:
            admission = admit(opened.facts, taxpayer=request.taxpayer, now=now, policy=self.policy)
            certificate = CertificateId(new_id())
            sealed = await self.custody.seal(key, KeyContext(request.tenant, certificate))
        return Candidate(
            id=certificate,
            tenant=request.tenant,
            facts=opened.facts,
            admission=admission,
            sealed=sealed,
            by=request.requested_by,
        )

    async def upload(self, request: CertificateUpload) -> Installation:
        """Install the company's first certificate (Decision 3)."""
        candidate = await self._candidate(request)
        async with self.units.unit(request.tenant) as unit:
            await unit.certificates.lock()
            installation = custody.upload(
                await unit.certificates.active(),
                candidate,
                last_generation=await unit.certificates.last_generation(),
                now=unit.now,
            )
            await _record(unit, installation)
        return installation

    async def rotate(self, request: CertificateUpload, *, replaces: int) -> Installation:
        """Replace the company's active certificate of generation ``replaces`` (Decision 3)."""
        candidate = await self._candidate(request)
        async with self.units.unit(request.tenant) as unit:
            await unit.certificates.lock()
            installation = custody.rotate(
                await unit.certificates.active(), candidate, replaces=replaces, now=unit.now
            )
            await _record(unit, installation)
        return installation

    async def sign(
        self,
        tenant: Tenant,
        document: bytes,
        purpose: SigningPurpose,
        *,
        reference: str | None,
        caller: str | None,
    ) -> Signed:
        """``document`` signed with the company's active certificate, once its audit has committed."""
        async with self.units.unit(tenant) as unit:
            certificate = await unit.certificates.active()
        if certificate is None:
            raise NoActiveCertificateError(tenant.company)
        certificate.require_usable(self.clock())
        with await self.custody.unseal(certificate.sealed, KeyContext(tenant, certificate.id)) as key:
            signed = await self.signer.sign(document, key, certificate.der, purpose)
        async with self.units.unit(tenant) as unit:
            record = SignatureRecord(
                id=SignatureId(new_id()),
                tenant=tenant,
                certificate=certificate.id,
                purpose=purpose,
                document_sha256=signed.document_sha256,
                reference=reference,
                caller=caller,
                signed_at=unit.now,
            )
            await unit.signatures.add(record)
        return Signed(signed.content, certificate.id, record.id, signed.document_sha256)

    async def announce_expiring(
        self, candidates: ExpiryCandidates, *, limit: int = ANNOUNCEMENTS_PER_PASS
    ) -> int:
        """Announce every expiry due now (Decision 8), each in its company's unit; how many were announced."""
        now = self.clock()
        announced = 0
        for watched in await candidates.due(now + HORIZON, limit):
            days = due_threshold(watched.not_after, now, watched.announced)
            if days is None:
                continue
            async with self.units.unit(watched.tenant) as unit:
                if not await unit.watch.claim(watched.certificate, days):
                    continue  # another replica announced it first
                expiring = CertificateExpiring(
                    tenant=watched.tenant,
                    certificate=watched.certificate,
                    generation=watched.generation,
                    not_after=watched.not_after,
                    days_left=days,
                )
                await unit.events.publish(expiring)
            announced += 1
        return announced
```

`services/vault/src/ledger_vault/application/queries.py`:

```python
"""What reads vault's state: a company's certificates, never their keys."""

from typing import Final, final

from ledger_vault.domain.errors import CertificateNotFoundError
lazy from ledger_kernel.ids import Tenant
lazy from ledger_vault.application.ports import Units
lazy from ledger_vault.domain.certificate import Certificate
lazy from ledger_vault.domain.ids import CertificateId

LIST_LIMIT: Final = 100
"""A company installs about one certificate a year: a hundred is a century of them."""


@final
class VaultQueries:
    """Reads, each in a unit scoped to the asking company."""

    __slots__ = ("_units",)

    def __init__(self, units: Units) -> None:
        self._units = units

    async def certificate(self, tenant: Tenant, certificate: CertificateId) -> Certificate:
        """One of the company's certificates; ``CertificateNotFoundError`` otherwise."""
        async with self._units.unit(tenant) as unit:
            found = await unit.certificates.get(certificate)
        if found is None:
            raise CertificateNotFoundError(certificate)
        return found

    async def certificates(self, tenant: Tenant) -> tuple[Certificate, ...]:
        """The company's certificates, newest first, at most ``LIST_LIMIT``."""
        async with self._units.unit(tenant) as unit:
            return await unit.certificates.newest(LIST_LIMIT)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/vault -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (the module's branches were measured on 2026-10-12: 100 % with these tests).
- Test: `services/vault/tests/test_bundle.py`, `services/vault/tests/test_custody.py`

**Interfaces:**
- Consumes: the ports (Task 5), `KeyMaterial`, `KeyContext`, `SealedKey`, `CertificateFacts` and the refusal errors (Task 4); `InvalidOptionError`, `require_at_least` (`ledger_platform.options`); `ledger_testing.pki`, `ledger_testing.clock.Moment` (Task 2, M2).
- Produces: `ledger_vault.adapters.pool`: `MAX_THREADS = 64`, `SigningPool(threads)` with `opened()`, `run(work)`, `PoolClosedError`, `InvalidPoolOptionError`. `ledger_vault.adapters.bundle`: `DEFAULT_MAX_BUNDLE = 65536`, `TrustStore(roots)` with `from_pem(pem)`, `at(path | None)`, `trusts(leaf, intermediates, at)`; `facts_of(...)`; `open_bundle(bundle, password, trust, now)`; `Pkcs12Reader(pool, trust, *, limit)`. `ledger_vault.adapters.kms`: `KmsUnavailableError(reason)`, `KmsRefusedError(reason)`, `DataKey(plaintext, wrapped, key_id)`, `KeyManagementService` (`provider`, `opened()`, `generate(context)`, `unwrap(wrapped, key_id, context)`). `ledger_vault.adapters.kms.local`: `LocalKms(key)` with `key_id`, `InvalidLocalKeyError`. `ledger_vault.adapters.custody`: `DataKeyCache(seconds=0, *, capacity=1000, clock=monotonic)`, `EnvelopeCustody(kms, cache=None)` with `opened()`, `SealedKeyTamperedError`, `ProviderMismatchError`, `InvalidCacheOptionError`.

- [ ] **Step 1: Dependencies and layering**

`services/vault/pyproject.toml` — `dependencies = ["ledger-kernel", "ledger-platform", "cryptography>=50.0.2"]`, `ledger-platform = { workspace = true }` in `[tool.uv.sources]`. Root: "Vault is hexagonal" `layers = ["adapters", "application", "domain"]`; dev group gains `"types-lxml>=2026.2.16"`, `"types-boto3-lite[kms]>=1.43.111"`, `"signxml>=5.1.0"` (used from Tasks 7 and 8; added now so the lock changes once). Run: `uv lock && uv sync`.

`services/vault/src/ledger_vault/adapters/__init__.py`:

```python
"""How vault reaches the world: the crypto libraries, the KMS, PostgreSQL, the outbox."""
```

- [ ] **Step 2: Write the failing tests**

`services/vault/tests/test_bundle.py`:

```python
from datetime import UTC, datetime, timedelta
from typing import Final
lazy from collections.abc import AsyncGenerator
lazy from pathlib import Path

import pytest

from ledger_testing.pki import (
    ENCIPHERING_ONLY,
    OPENING_PHRASE,
    Authority,
    bundle,
    ec_key,
    ed25519_key,
    pem,
    person,
    rsa_key,
)
from ledger_vault.adapters.bundle import Pkcs12Reader, TrustStore
from ledger_vault.adapters.pool import InvalidPoolOptionError, PoolClosedError, SigningPool
from ledger_vault.domain.errors import (
    BundleProblem,
    CertificateIncompleteError,
    CertificatePasswordError,
    CertificateTooLargeError,
    CertificateUnreadableError,
)
from ledger_vault.domain.holder import SERIAL_NUMBER

NOW: Final = datetime.now(UTC)
LATER: Final = NOW + timedelta(days=400)


@pytest.fixture(scope="module")
def root() -> Authority:
    return Authority.root()


@pytest.fixture(scope="module")
def intermediate(root: Authority) -> Authority:
    return root.intermediate()


@pytest.fixture
async def pool() -> AsyncGenerator[SigningPool]:
    async with SigningPool(2).opened() as opened:
        yield opened


@pytest.fixture
def reader(pool: SigningPool, root: Authority) -> Pkcs12Reader:
    return Pkcs12Reader(pool, TrustStore.from_pem(pem(root.certificate)), limit=16 * 1024)


async def test_a_bundle_opens_into_its_facts_and_its_key(
    reader: Pkcs12Reader, intermediate: Authority
) -> None:
    issued = intermediate.issue(person())
    opened = await reader.open(bundle(issued, chain=[intermediate.certificate]), OPENING_PHRASE, now=NOW)
    facts = opened.facts
    assert (facts.key_algorithm, facts.key_bits, facts.signs, facts.trusted) == ("rsa", 2048, True, True)
    assert facts.issuer_name == "LEDGER TEST QUALIFIED CERTIFICATES"
    assert (SERIAL_NUMBER, "IDCDO-00112345678") in facts.subject
    assert facts.serial_number == format(issued.certificate.serial_number, "x")
    assert len(facts.fingerprint) == 64
    assert facts.not_after == issued.certificate.not_valid_after_utc
    with opened.key as key:
        assert key.buffer.startswith(b"\x30\x82")
    assert opened.key.wiped


async def test_a_chain_is_trusted_only_through_the_store(
    pool: SigningPool, root: Authority, intermediate: Authority
) -> None:
    issued = intermediate.issue(person())
    without_chain = await Pkcs12Reader(pool, TrustStore.from_pem(pem(root.certificate))).open(
        bundle(issued), OPENING_PHRASE, now=NOW
    )
    nobody_trusted = await Pkcs12Reader(pool, TrustStore(())).open(
        bundle(issued, chain=[intermediate.certificate]), OPENING_PHRASE, now=NOW
    )
    later = await Pkcs12Reader(pool, TrustStore.from_pem(pem(root.certificate))).open(
        bundle(issued, chain=[intermediate.certificate]),
        OPENING_PHRASE,
        now=LATER,
    )
    assert (without_chain.facts.trusted, nobody_trusted.facts.trusted, later.facts.trusted) == (
        False,
        False,
        False,
    )


def test_a_trust_store_is_read_from_a_file_or_from_nothing(tmp_path: Path, root: Authority) -> None:
    roots = tmp_path / "roots.pem"
    roots.write_bytes(pem(root.certificate))
    assert TrustStore.at(roots).trusts(root.intermediate().issue(person()).certificate, [], NOW) is False
    assert TrustStore.from_pem(b"# no certificate here\n").trusts(root.certificate, [], NOW) is False
    assert TrustStore.at(None).trusts(root.certificate, [], NOW) is False


async def test_what_is_not_a_bundle_is_told_from_a_wrong_password(
    reader: Pkcs12Reader, intermediate: Authority
) -> None:
    with pytest.raises(CertificateUnreadableError):
        await reader.open(b"\x30\x82\x00\x00 not pkcs12", "x", now=NOW)
    with pytest.raises(CertificatePasswordError) as caught:
        await reader.open(bundle(intermediate.issue(person())), "not the password", now=NOW)
    assert "not the password" not in str(caught.value)
    with pytest.raises(CertificateTooLargeError, match="at most 16384 bytes"):
        await reader.open(b"\x00" * (16 * 1024 + 1), "x", now=NOW)


async def test_a_bundle_must_be_a_key_with_its_certificate(
    reader: Pkcs12Reader, intermediate: Authority
) -> None:
    issued = intermediate.issue(person())
    foreign = intermediate.issue(person(), key=rsa_key())
    cases = (
        (BundleProblem.NO_KEY, bundle(issued, key=False)),
        (BundleProblem.NO_CERTIFICATE, bundle(issued, certificate=False)),
        (BundleProblem.NO_CERTIFICATE, bundle(issued, certificate=False, chain=[foreign.certificate])),
    )
    for problem, data in cases:
        with pytest.raises(CertificateIncompleteError, match=problem.value):
            await reader.open(data, OPENING_PHRASE, now=NOW)


async def test_what_custody_refuses_is_still_read_so_that_the_domain_can_say_why(
    reader: Pkcs12Reader, intermediate: Authority
) -> None:
    curve = await reader.open(bundle(intermediate.issue(person(), key=ec_key())), OPENING_PHRASE, now=NOW)
    enciphering = await reader.open(
        bundle(intermediate.issue(person(), usage=ENCIPHERING_ONLY)), OPENING_PHRASE, now=NOW
    )
    no_usage = await reader.open(bundle(intermediate.issue(person(), usage=None)), OPENING_PHRASE, now=NOW)
    edwards = await reader.open(
        bundle(intermediate.issue(person(), key=ed25519_key())), OPENING_PHRASE, now=NOW
    )
    assert (curve.facts.key_algorithm, curve.facts.key_bits) == ("ec", 256)
    assert (edwards.facts.key_algorithm, edwards.facts.key_bits) == ("other", 0)
    assert (enciphering.facts.signs, no_usage.facts.signs) == (False, True)


async def test_the_pool_runs_work_while_open_only() -> None:
    pool = SigningPool(1)
    with pytest.raises(PoolClosedError):
        await pool.run(lambda: 1)
    async with pool.opened():
        assert await pool.run(lambda: 2) == 2
    for threads in (0, 65):
        with pytest.raises(InvalidPoolOptionError):
            SigningPool(threads)
```

`services/vault/tests/test_custody.py`:

```python
from dataclasses import replace
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Mapping
lazy from contextlib import AbstractAsyncContextManager

import pytest

from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.options import InvalidOptionError
from ledger_testing.clock import Moment
from ledger_vault.adapters.custody import (
    DataKeyCache,
    EnvelopeCustody,
    ProviderMismatchError,
    SealedKeyTamperedError,
)
from ledger_vault.adapters.kms import KeyManagementService, KmsRefusedError
from ledger_vault.adapters.kms.local import InvalidLocalKeyError, LocalKms
from ledger_vault.domain.ids import CertificateId
from ledger_vault.domain.keys import KeyContext, KeyMaterial
lazy from ledger_vault.adapters.kms import DataKey

KEK: Final = bytes(range(32))
PRIVATE: Final = b"\x30\x82 a private key in PKCS#8"
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))


def context() -> KeyContext:
    return KeyContext(TENANT, CertificateId(new_id()))


@final
class Counting(KeyManagementService):
    """The local KMS, counting the data keys it unwraps."""

    def __init__(self) -> None:
        self.kms = LocalKms(KEK)
        self.unwrapped = 0

    @property
    @override
    def provider(self) -> str:
        return self.kms.provider

    @override
    def opened(self) -> AbstractAsyncContextManager[KeyManagementService]:
        return self.kms.opened()

    @override
    async def generate(self, context: Mapping[str, str]) -> DataKey:
        return await self.kms.generate(context)

    @override
    async def unwrap(self, wrapped: bytes, key_id: str, context: Mapping[str, str]) -> KeyMaterial:
        self.unwrapped += 1
        return await self.kms.unwrap(wrapped, key_id, context)


@pytest.fixture
async def custody() -> AsyncGenerator[EnvelopeCustody]:
    async with LocalKms(KEK).opened() as kms, EnvelopeCustody(kms).opened() as opened:
        yield opened


async def test_a_key_sealed_for_a_certificate_opens_for_it_only(custody: EnvelopeCustody) -> None:
    bound = context()
    sealed = await custody.seal(KeyMaterial.copied(PRIVATE), bound)
    assert PRIVATE not in sealed.ciphertext
    assert (sealed.provider, len(sealed.nonce)) == ("local", 12)
    with await custody.unseal(sealed, bound) as key:
        assert bytes(key.buffer) == PRIVATE
    assert key.wiped
    with pytest.raises(KmsRefusedError, match="does not open under its context"):
        await custody.unseal(sealed, context())


async def test_an_altered_or_moved_ciphertext_does_not_open(custody: EnvelopeCustody) -> None:
    bound = context()
    sealed = await custody.seal(KeyMaterial.copied(PRIVATE), bound)
    other = await custody.seal(KeyMaterial.copied(PRIVATE), bound)
    flipped = bytes([sealed.ciphertext[0] ^ 1]) + sealed.ciphertext[1:]
    for broken in (replace(sealed, ciphertext=flipped), replace(sealed, nonce=other.nonce)):
        with pytest.raises(SealedKeyTamperedError):
            await custody.unseal(broken, bound)


async def test_a_key_sealed_by_another_kms_is_not_unsealed(custody: EnvelopeCustody) -> None:
    sealed = await custody.seal(KeyMaterial.copied(PRIVATE), context())
    with pytest.raises(ProviderMismatchError):
        await custody.unseal(replace(sealed, provider="aws-kms"), context())
    with pytest.raises(KmsRefusedError, match="another local key"):
        await EnvelopeCustody(LocalKms(bytes(32))).unseal(sealed, context())


def test_a_local_key_is_32_bytes() -> None:
    with pytest.raises(InvalidLocalKeyError, match="32 bytes"):
        LocalKms(b"short")
    assert LocalKms(KEK).key_id.startswith("local:")


async def test_without_a_lifetime_every_unseal_asks_the_kms() -> None:
    kms = Counting()
    custody = EnvelopeCustody(kms)
    bound = context()
    sealed = await custody.seal(KeyMaterial.copied(PRIVATE), bound)
    for _ in range(3):
        (await custody.unseal(sealed, bound)).wipe()
    assert kms.unwrapped == 3


async def test_a_cached_data_key_is_kept_for_its_lifetime_and_wiped_after() -> None:
    kms, moment = Counting(), Moment(0.0)
    cache = DataKeyCache(60, capacity=1, clock=moment)
    custody = EnvelopeCustody(kms, cache)
    bound, other = context(), context()
    sealed = await custody.seal(KeyMaterial.copied(PRIVATE), bound)
    second = await custody.seal(KeyMaterial.copied(PRIVATE), other)
    for _ in range(3):
        (await custody.unseal(sealed, bound)).wipe()
    assert kms.unwrapped == 1
    moment.now = 61.0
    (await custody.unseal(sealed, bound)).wipe()
    (await custody.unseal(second, other)).wipe()  # pushes the first out: capacity 1
    (await custody.unseal(sealed, bound)).wipe()
    assert kms.unwrapped == 4
    async with custody.opened():
        pass
    (await custody.unseal(sealed, bound)).wipe()
    assert kms.unwrapped == 5


def test_a_cache_lives_at_most_five_minutes() -> None:
    assert not DataKeyCache().enabled
    for seconds in (-1, 301):
        with pytest.raises(InvalidOptionError):
            DataKeyCache(seconds)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/vault/tests/test_bundle.py services/vault/tests/test_custody.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_vault.adapters.bundle'`.

- [ ] **Step 4: Implement**

`services/vault/src/ledger_vault/adapters/pool.py`:

```python
"""A bounded pool of threads for vault's CPU-bound work: opening bundles, canonicalizing, signing.

That work runs off the event loop, so that a large document or a bundle's key derivation never stalls the
other calls the process serves. At most ``threads`` jobs run at once, and the rest wait in asyncio, not in the
executor's queue: a call whose deadline passes (its task is cancelled) stops waiting at once and leaves no job
behind it. A job already running finishes, and its result is dropped.

The pool is a resource of the service: it is opened when the service starts and shut down when it stops,
after the gRPC server has let the calls in flight finish.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import ClassVar, Final, final
lazy from collections.abc import AsyncGenerator, Callable

from ledger_kernel.errors import LedgerError
from ledger_platform.options import InvalidOptionError, require_at_least

MAX_THREADS: Final = 64


class PoolClosedError(LedgerError):
    """Work was handed to the pool while it was not open: a service that never opened it."""

    code: ClassVar[str] = "signing_pool_closed"

    def __init__(self) -> None:
        super().__init__("the signing pool is not open")


class InvalidPoolOptionError(InvalidOptionError):
    """A pool option that cannot work."""

    code: ClassVar[str] = "invalid_pool_option"
    label: ClassVar[str] = "signing pool option"


@final
class SigningPool:
    """Runs CPU-bound work on at most ``threads`` threads (see the module)."""

    __slots__ = ("_executor", "_slots", "_threads")

    def __init__(self, threads: int) -> None:
        require_at_least(InvalidPoolOptionError, "threads", threads)
        if threads > MAX_THREADS:
            raise InvalidPoolOptionError(
                option="threads", requirement=f"at most {MAX_THREADS}", value=threads
            )
        self._threads = threads
        self._slots = asyncio.Semaphore(threads)
        self._executor: ThreadPoolExecutor | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[SigningPool]:
        """This pool, its threads available for the length of the block; shut down after it."""
        executor = ThreadPoolExecutor(max_workers=self._threads, thread_name_prefix="vault-signing")
        self._executor = executor
        try:
            yield self
        finally:
            self._executor = None
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)

    async def run[T](self, work: Callable[[], T]) -> T:
        """``work()``, on a thread of the pool, once one is free."""
        executor = self._executor
        if executor is None:
            raise PoolClosedError
        async with self._slots:
            return await asyncio.get_running_loop().run_in_executor(executor, work)
```

`services/vault/src/ledger_vault/adapters/bundle.py`:

```python
"""Opening the PKCS#12 bundles companies upload, and checking their chain against the trust store.

``Pkcs12Reader`` refuses a bundle over its size limit unread, then opens it on the signing pool (a bundle's
key derivation costs milliseconds of CPU): a wrong password and bytes that are no bundle are told apart by
what ``cryptography`` answers (50.0 words them differently; a test pins both). What it reads becomes
``CertificateFacts``: the certificate's subject, issuer, serial, fingerprint, validity, key and key usage, and
whether it chains to the trust store at the time of the upload. The private key leaves as ``KeyMaterial``
(PKCS#8 DER), which the caller wipes once it is sealed. The certificate is the one ``cryptography`` pairs with
the key (by the bundle's local key id): a bundle whose only certificates are another key's holds none for it.

``TrustStore`` holds the roots of the authorities vault accepts, from a PEM file the deployment names: none is
built in, and an empty store trusts nothing. The chain is verified by ``cryptography``'s path builder with the
Web PKI's rules for the authorities (basic constraints, key usage, signature algorithms, validity) and none
for the end entity beyond its signature and validity: a certificate for tax procedures needs no subject
alternative name or extended key usage. The intermediates come from the bundle itself.
"""

import hashlib
from typing import Final, final, override
lazy from collections.abc import Sequence
lazy from datetime import datetime
lazy from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)
from cryptography.x509.oid import NameOID
from cryptography.x509.verification import ExtensionPolicy, PolicyBuilder, Store, VerificationError
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes, PublicKeyTypes

from ledger_vault.application.ports import BundleReader, OpenedBundle
from ledger_vault.domain.certificate import CertificateFacts
from ledger_vault.domain.errors import (
    BundleProblem,
    CertificateIncompleteError,
    CertificatePasswordError,
    CertificateTooLargeError,
    CertificateUnreadableError,
)
from ledger_vault.domain.keys import KeyMaterial
lazy from ledger_vault.adapters.pool import SigningPool

DEFAULT_MAX_BUNDLE: Final = 64 * 1024
"""Far above a certificate with its key and chain (a few kilobytes), far below what could hurt."""
_NOT_OPENED: Final = "Invalid password or PKCS12 data"
"""What ``cryptography`` 50 says when the bytes are a bundle and the password does not open it. For bytes
that are no bundle it says ``Could not deserialize PKCS12 data``; ``test_bundle.py`` pins both."""
_OTHER: Final = "other"


@final
class TrustStore:
    """The roots of the certification authorities whose certificates vault takes into custody."""

    __slots__ = ("_store",)

    def __init__(self, roots: Sequence[x509.Certificate]) -> None:
        self._store = Store(list(roots)) if roots else None

    @classmethod
    def from_pem(cls, pem: bytes) -> TrustStore:
        """The roots of a PEM file's certificates; no certificate in it, no root."""
        return cls(x509.load_pem_x509_certificates(pem) if b"-----BEGIN CERTIFICATE-----" in pem else ())

    @classmethod
    def at(cls, path: Path | None) -> TrustStore:
        """The roots of the PEM file at ``path``; none without a path."""
        return cls(()) if path is None else cls.from_pem(path.read_bytes())

    def trusts(self, leaf: x509.Certificate, intermediates: Sequence[x509.Certificate], at: datetime) -> bool:
        """Whether ``leaf`` chains through ``intermediates`` to one of the roots, all valid ``at``."""
        if self._store is None:
            return False
        verifier = (
            PolicyBuilder()
            .store(self._store)
            .time(at)
            .extension_policies(
                ca_policy=ExtensionPolicy.webpki_defaults_ca(), ee_policy=ExtensionPolicy.permit_all()
            )
            .build_client_verifier()
        )
        try:
            verifier.verify(leaf, list(intermediates))
        except VerificationError:
            return False
        return True


def _algorithm(key: PublicKeyTypes) -> tuple[str, int]:
    if isinstance(key, RSAPublicKey):
        return "rsa", key.key_size
    if isinstance(key, EllipticCurvePublicKey):
        return "ec", key.key_size
    return _OTHER, 0


def _signs(certificate: x509.Certificate) -> bool:
    """Whether the key usage allows a digital signature or non-repudiation; a certificate without one does."""
    try:
        usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return True
    return usage.digital_signature or usage.content_commitment


def _common_name(name: x509.Name) -> str:
    values = [attribute.value for attribute in name.get_attributes_for_oid(NameOID.COMMON_NAME)]
    return next((value for value in values if isinstance(value, str)), "")


def facts_of(
    certificate: x509.Certificate, chain: Sequence[x509.Certificate], trust: TrustStore, now: datetime
) -> CertificateFacts:
    """What ``certificate`` states, and whether it chains through ``chain`` to ``trust`` at ``now``."""
    der = certificate.public_bytes(Encoding.DER)
    algorithm, bits = _algorithm(certificate.public_key())
    return CertificateFacts(
        der=der,
        subject=tuple(
            (attribute.oid.dotted_string, attribute.value)
            for attribute in certificate.subject
            if isinstance(attribute.value, str)
        ),
        issuer_name=_common_name(certificate.issuer),
        serial_number=format(certificate.serial_number, "x"),
        fingerprint=hashlib.sha256(der).hexdigest(),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
        key_algorithm=algorithm,
        key_bits=bits,
        signs=_signs(certificate),
        trusted=trust.trusts(certificate, chain, now),
    )


def _material(key: PrivateKeyTypes) -> KeyMaterial:
    """The key as PKCS#8 DER in a ``KeyMaterial``. ``cryptography`` hands out an immutable copy first."""
    return KeyMaterial.copied(key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption()))


def open_bundle(bundle: bytes, password: str, trust: TrustStore, now: datetime) -> OpenedBundle:
    """The certificate and key of ``bundle`` (see the module); runs on the signing pool."""
    try:
        loaded = pkcs12.load_pkcs12(bundle, password.encode())
    except ValueError as error:
        if str(error) == _NOT_OPENED:
            raise CertificatePasswordError from None
        raise CertificateUnreadableError from None
    key, holder = loaded.key, loaded.cert
    if key is None:
        raise CertificateIncompleteError(BundleProblem.NO_KEY)
    if holder is None:
        raise CertificateIncompleteError(BundleProblem.NO_CERTIFICATE)
    certificate = holder.certificate
    chain = [extra.certificate for extra in loaded.additional_certs]
    return OpenedBundle(facts_of(certificate, chain, trust, now), _material(key))


@final
class Pkcs12Reader(BundleReader):
    """Opens bundles on the signing pool, with a size limit and a trust store (see the module)."""

    __slots__ = ("_limit", "_pool", "_trust")

    def __init__(self, pool: SigningPool, trust: TrustStore, *, limit: int = DEFAULT_MAX_BUNDLE) -> None:
        self._pool = pool
        self._trust = trust
        self._limit = limit

    @override
    async def open(self, bundle: bytes, password: str, *, now: datetime) -> OpenedBundle:
        if len(bundle) > self._limit:
            raise CertificateTooLargeError(self._limit)
        return await self._pool.run(lambda: open_bundle(bundle, password, self._trust, now))
```

`services/vault/src/ledger_vault/adapters/kms/__init__.py`:

```python
"""The key management service that wraps vault's data keys, behind one port (§9: envelope encryption).

A data key is a fresh AES-256 key per certificate. The KMS generates it, hands it back in the clear and
wrapped (encrypted under a key that never leaves the KMS), and later unwraps it again. Both calls carry an
encryption context, which the KMS binds into the wrapping: unwrapping under another context fails. vault's
context names the organization, the company and the certificate (``KeyContext``), so a wrapped data key copied
to another certificate's row is useless, and the KMS's own audit trail says whose key it unwrapped.

Adapters: ``AwsKms`` (AWS KMS, LocalStack in tests and locally) and ``LocalKms`` (a key from a setting, for a
laptop; the settings refuse it in production). A KMS that cannot answer now raises ``KmsUnavailableError``
(the caller may retry); one that refuses (a disabled key, a context that does not match, a key vault was not
configured with) raises ``KmsRefusedError``, a defect or an attack, never the client's fault.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Protocol, final
lazy from collections.abc import Mapping
lazy from contextlib import AbstractAsyncContextManager

from ledger_kernel.errors import LedgerError, UnavailableError
lazy from ledger_vault.domain.keys import KeyMaterial


class KmsUnavailableError(UnavailableError):
    """The KMS could not answer (unreachable, throttling, failing): a later call may work."""

    code: ClassVar[str] = "kms_unavailable"

    def __init__(self, reason: str) -> None:
        super().__init__(f"the key management service could not answer: {reason}")


class KmsRefusedError(LedgerError):
    """The KMS refused: a disabled or unknown key, or a context that does not match. Never retried."""

    code: ClassVar[str] = "kms_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(f"the key management service refused: {reason}")


@final
@dataclass(frozen=True, slots=True)
class DataKey:
    """A new data key: in the clear (wiped by its holder) and wrapped, with the KMS key that wrapped it."""

    plaintext: KeyMaterial
    wrapped: bytes = field(repr=False)
    key_id: str


class KeyManagementService(Protocol, metaclass=ABCMeta):
    """Generates and unwraps data keys, bound to an encryption context.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
    """

    @property
    @abstractmethod
    def provider(self) -> str:
        """The kind of KMS, kept beside every key it wrapped: ``aws-kms`` or ``local``."""

    @abstractmethod
    def opened(self) -> AbstractAsyncContextManager[KeyManagementService]:
        """This KMS, usable for the length of the block: the service's resource."""

    @abstractmethod
    async def generate(self, context: Mapping[str, str]) -> DataKey:
        """A new AES-256 data key, wrapped under ``context``."""

    @abstractmethod
    async def unwrap(self, wrapped: bytes, key_id: str, context: Mapping[str, str]) -> KeyMaterial:
        """The data key ``wrapped`` holds, which ``key_id`` wrapped under ``context``."""
```

`services/vault/src/ledger_vault/adapters/kms/local.py`:

```python
"""A KMS of vault's own, for a laptop and tests: the key-encryption key is a setting.

It wraps a data key with AES-256-GCM under a 32-byte key read from ``LEDGER_LOCAL_KMS_KEY``, with the
encryption context as associated data (its JSON, keys sorted), so it binds a data key to its context exactly
as a cloud KMS does. Its key id is a fingerprint of the key (``local:`` and 16 hex digits of its SHA-256): a
data key wrapped under another local key is refused, not misread.

It is no KMS: the key-encryption key is in the process's memory and in its configuration, which is what a KMS
exists to avoid. The settings refuse it in production (``VaultSettings``).
"""

import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from typing import ClassVar, Final, final, override
lazy from collections.abc import AsyncGenerator, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ledger_platform.options import InvalidOptionError
from ledger_vault.adapters.kms import DataKey, KeyManagementService, KmsRefusedError
from ledger_vault.domain.keys import KeyMaterial

KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
TAG_BYTES: Final = 16
PROVIDER: Final = "local"
_ANOTHER_KEY: Final = "the data key was wrapped by another local key"
_ANOTHER_CONTEXT: Final = "the data key does not open under its context"


class InvalidLocalKeyError(InvalidOptionError):
    """A local key-encryption key that is not 32 bytes."""

    code: ClassVar[str] = "invalid_local_kms_key"
    label: ClassVar[str] = "local KMS option"


def _associated(context: Mapping[str, str]) -> bytes:
    return json.dumps(dict(context), sort_keys=True, separators=(",", ":")).encode("utf-8")


@final
class LocalKms(KeyManagementService):
    """Wraps data keys under a key-encryption key held in memory (see the module)."""

    __slots__ = ("_aead", "_key_id")

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise InvalidLocalKeyError(
                option="key", requirement=f"{KEY_BYTES} bytes", value=f"{len(key)} bytes"
            )
        self._aead = AESGCM(key)
        self._key_id = f"{PROVIDER}:{hashlib.sha256(key).hexdigest()[:16]}"

    @property
    @override
    def provider(self) -> str:
        return PROVIDER

    @property
    def key_id(self) -> str:
        """The fingerprint every data key this KMS wraps is kept with."""
        return self._key_id

    @override
    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[LocalKms]:
        yield self

    @override
    async def generate(self, context: Mapping[str, str]) -> DataKey:
        plaintext = KeyMaterial(bytearray(secrets.token_bytes(KEY_BYTES)))
        nonce = secrets.token_bytes(NONCE_BYTES)
        wrapped = nonce + self._aead.encrypt(nonce, plaintext.buffer, _associated(context))
        return DataKey(plaintext, wrapped, self._key_id)

    @override
    async def unwrap(self, wrapped: bytes, key_id: str, context: Mapping[str, str]) -> KeyMaterial:
        if key_id != self._key_id:
            raise KmsRefusedError(_ANOTHER_KEY)
        nonce, ciphertext = wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:]
        plaintext = KeyMaterial(bytearray(max(len(ciphertext) - TAG_BYTES, 0)))
        try:
            self._aead.decrypt_into(nonce, ciphertext, _associated(context), plaintext.buffer)
        except (InvalidTag, ValueError) as error:
            plaintext.wipe()
            raise KmsRefusedError(_ANOTHER_CONTEXT) from error
        return plaintext
```

`services/vault/src/ledger_vault/adapters/custody.py`:

```python
"""Envelope encryption of private keys (§9): each key under a data key of its own, which the KMS wraps.

Sealing asks the KMS for a new data key, encrypts the private key (PKCS#8 DER) with it, AES-256-GCM with a
random 96-bit nonce and the ``KeyContext`` as associated data, and wipes the data key: what is stored is the
ciphertext, the nonce and the data key as the KMS wrapped it. Unsealing asks the KMS to unwrap the data key
under the same context, decrypts the private key into a ``KeyMaterial`` and wipes the data key again. A
ciphertext moved to another row, or altered, fails its tag: ``SealedKeyTamperedError``, a defect or an attack.

Data keys are unwrapped once per signature unless ``DataKeyCache`` is given a lifetime
(``LEDGER_DATA_KEY_CACHE_SECONDS``, 0 by default: off). Then an unwrapped data key is kept, in this process's
memory only, for at most that many seconds (300 at most) and for at most ``capacity`` certificates, and wiped
when it expires, when it is pushed out, and when the service stops. Private keys themselves are never kept.
Why it is allowed at all (Decision 10): rule 7 is that keys never leave vault, and a data key kept in vault's
memory does not; what the cache costs is time, as the window in which a memory dump would yield a data key
grows from one call to the lifetime. It is for when the KMS's latency or its request quota threatens the
signing SLO, measured first.
"""

import secrets
from collections import OrderedDict
from contextlib import asynccontextmanager
from time import monotonic
from typing import ClassVar, Final, final, override
lazy from collections.abc import AsyncGenerator, Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ledger_kernel.errors import LedgerError
from ledger_platform.options import InvalidOptionError, require_at_least
from ledger_vault.application.ports import KeyCustody
from ledger_vault.domain.certificate import SealedKey
from ledger_vault.domain.keys import KeyMaterial
lazy from ledger_vault.adapters.kms import KeyManagementService
lazy from ledger_vault.domain.keys import KeyContext

NONCE_BYTES: Final = 12
TAG_BYTES: Final = 16
MAX_CACHE_SECONDS: Final = 300
DEFAULT_CACHE_CAPACITY: Final = 1000


class SealedKeyTamperedError(LedgerError):
    """A sealed key does not open under its own context: moved to another row, altered, or misconfigured."""

    code: ClassVar[str] = "sealed_key_tampered"

    def __init__(self) -> None:
        super().__init__("a sealed private key does not open under its context")


class ProviderMismatchError(LedgerError):
    """A key sealed through one kind of KMS was to be unsealed through another: a configuration mistake."""

    code: ClassVar[str] = "kms_provider_mismatch"

    def __init__(self, sealed: str, configured: str) -> None:
        super().__init__(f"a key sealed by {sealed} cannot be unsealed by {configured}")


class InvalidCacheOptionError(InvalidOptionError):
    """A data-key cache option that cannot work."""

    code: ClassVar[str] = "invalid_data_key_cache_option"
    label: ClassVar[str] = "data key cache option"


type _Entry = tuple[float, KeyMaterial]
type _Key = tuple[str, bytes, bytes]
"""The KMS key, the wrapped data key and the context it was unwrapped under."""


@final
class DataKeyCache:
    """Unwrapped data keys kept for ``seconds`` at most, ``capacity`` at most; off when ``seconds`` is 0."""

    __slots__ = ("_capacity", "_clock", "_entries", "_seconds")

    def __init__(
        self,
        seconds: int = 0,
        *,
        capacity: int = DEFAULT_CACHE_CAPACITY,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not 0 <= seconds <= MAX_CACHE_SECONDS:
            raise InvalidCacheOptionError(
                option="seconds", requirement=f"between 0 and {MAX_CACHE_SECONDS}", value=seconds
            )
        require_at_least(InvalidCacheOptionError, "capacity", capacity)
        self._seconds = seconds
        self._capacity = capacity
        self._clock = clock
        self._entries: OrderedDict[_Key, _Entry] = OrderedDict()

    @property
    def enabled(self) -> bool:
        """Whether data keys are kept at all."""
        return self._seconds > 0

    def get(self, key: _Key) -> KeyMaterial | None:
        """A copy of the data key kept for ``key`` (the caller wipes it), or ``None``."""
        self._expire()
        entry = self._entries.get(key)
        if entry is None:
            return None
        return KeyMaterial(bytearray(entry[1].buffer))

    def keep(self, key: _Key, data_key: KeyMaterial) -> None:
        """Keep a copy of ``data_key`` for ``key``; the oldest goes when the cache is full."""
        if not self.enabled:
            return
        self._expire()
        self._evict(key)
        self._entries[key] = (self._clock() + self._seconds, KeyMaterial(bytearray(data_key.buffer)))
        while len(self._entries) > self._capacity:
            self._evict(next(iter(self._entries)))

    def clear(self) -> None:
        """Wipe and drop every data key kept."""
        for key in list(self._entries):
            self._evict(key)

    def _expire(self) -> None:
        now = self._clock()
        for key in [key for key, (expires, _) in self._entries.items() if expires <= now]:
            self._evict(key)

    def _evict(self, key: _Key) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            entry[1].wipe()


@final
class EnvelopeCustody(KeyCustody):
    """Seals and unseals private keys through ``kms`` (see the module)."""

    __slots__ = ("_cache", "_kms")

    def __init__(self, kms: KeyManagementService, cache: DataKeyCache | None = None) -> None:
        self._kms = kms
        self._cache = DataKeyCache() if cache is None else cache

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[EnvelopeCustody]:
        """This custody for the length of the block; the cached data keys are wiped after it."""
        try:
            yield self
        finally:
            self._cache.clear()

    @override
    async def seal(self, key: KeyMaterial, context: KeyContext) -> SealedKey:
        data_key = await self._kms.generate(context.encryption_context())
        nonce = secrets.token_bytes(NONCE_BYTES)
        with data_key.plaintext as plaintext:
            ciphertext = AESGCM(plaintext.buffer).encrypt(nonce, key.buffer, context.associated_data())
        return SealedKey(
            provider=self._kms.provider,
            key_id=data_key.key_id,
            wrapped_key=data_key.wrapped,
            nonce=nonce,
            ciphertext=ciphertext,
        )

    @override
    async def unseal(self, sealed: SealedKey, context: KeyContext) -> KeyMaterial:
        if sealed.provider != self._kms.provider:
            raise ProviderMismatchError(sealed.provider, self._kms.provider)
        with await self._data_key(sealed, context) as data_key:
            key = KeyMaterial(bytearray(max(len(sealed.ciphertext) - TAG_BYTES, 0)))
            try:
                AESGCM(data_key.buffer).decrypt_into(
                    sealed.nonce, sealed.ciphertext, context.associated_data(), key.buffer
                )
            except (InvalidTag, ValueError) as error:
                key.wipe()
                raise SealedKeyTamperedError from error
            return key

    async def _data_key(self, sealed: SealedKey, context: KeyContext) -> KeyMaterial:
        cache_key = (sealed.key_id, sealed.wrapped_key, context.associated_data())
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        data_key = await self._kms.unwrap(sealed.wrapped_key, sealed.key_id, context.encryption_context())
        self._cache.keep(cache_key, data_key)
        return data_key
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/vault -q --no-cov`
Expected: PASS. Every block of this task was run as written on 2026-10-12 (CPython 3.15, cryptography 50.0.2): both test modules pass, and ruff, basedpyright `all` and mypy `--strict` report nothing.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/vault
git commit -m "feat(vault): bundles opened on a bounded pool, a configured trust store, envelope custody

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: AWS KMS, tested against LocalStack

**Files:**
- Create: `services/vault/src/ledger_vault/adapters/kms/aws.py`
- Modify: `services/vault/pyproject.toml`
- Test: `services/vault/tests/test_kms_aws.py`

**Interfaces:**
- Consumes: `KeyManagementService`, `DataKey`, `KmsUnavailableError`, `KmsRefusedError` (Task 6); `localstack_kms`, `localstack_address` (Task 2).
- Produces: `ledger_vault.adapters.kms.aws`: `PROVIDER = "aws-kms"`, `failure_of(error) -> LedgerError`, `AwsKms(key_id, *, region, endpoint=None, session=None)` with `opened()` (checks the key: enabled, `SYMMETRIC_DEFAULT`, `ENCRYPT_DECRYPT`), `generate`, `unwrap`; `KmsClientClosedError`, `KmsKeyUnusableError`.

- [ ] **Step 1: Dependencies**

`services/vault/pyproject.toml` — add `"boto3>=1.43.111"` and `"botocore>=1.43.111"` (vault imports `botocore.config` and `botocore.exceptions` itself: deptry would call it transitive otherwise). Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`services/vault/tests/test_kms_aws.py` (the test's admin client is the second `cast` of the milestone, for the reason `aws.py` states):

```python
from typing import Final, cast
lazy from collections.abc import AsyncGenerator, Iterator

import boto3
import pytest
from botocore.exceptions import ClientError
lazy from types_boto3_kms.client import KMSClient

from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_testing.containers import localstack_address, localstack_kms
from ledger_vault.adapters.custody import EnvelopeCustody
from ledger_vault.adapters.kms import KmsRefusedError, KmsUnavailableError
from ledger_vault.adapters.kms.aws import AwsKms, KmsClientClosedError, KmsKeyUnusableError, failure_of
from ledger_vault.domain.ids import CertificateId
from ledger_vault.domain.keys import KeyContext, KeyMaterial

REGION: Final = "us-east-1"
ALIAS: Final = "alias/ledger-vault-test"
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))


def session() -> boto3.session.Session:
    """LocalStack takes any credentials; these never leave the test."""
    return boto3.session.Session(aws_access_key_id="test", aws_secret_access_key="test", region_name=REGION)


def admin(endpoint: str) -> KMSClient:
    return cast("KMSClient", session().client("kms", endpoint_url=endpoint))


@pytest.fixture(scope="module")
def endpoint() -> Iterator[str]:
    with localstack_kms() as container:
        address = localstack_address(container)
        key = admin(address).create_key(KeySpec="SYMMETRIC_DEFAULT", KeyUsage="ENCRYPT_DECRYPT")
        admin(address).create_alias(AliasName=ALIAS, TargetKeyId=key["KeyMetadata"]["KeyId"])
        yield address


@pytest.fixture
async def kms(endpoint: str) -> AsyncGenerator[AwsKms]:
    async with AwsKms(ALIAS, region=REGION, endpoint=endpoint, session=session()).opened() as opened:
        yield opened


def context() -> KeyContext:
    return KeyContext(TENANT, CertificateId(new_id()))


async def test_a_data_key_is_wrapped_by_the_kms_and_unwrapped_under_its_context(kms: AwsKms) -> None:
    bound = context()
    data_key = await kms.generate(bound.encryption_context())
    with data_key.plaintext as plaintext:
        assert len(plaintext.buffer) == 32
        clear = bytes(plaintext.buffer)
    assert data_key.key_id.startswith("arn:aws:kms:")
    with await kms.unwrap(data_key.wrapped, data_key.key_id, bound.encryption_context()) as again:
        assert bytes(again.buffer) == clear
    with pytest.raises(KmsRefusedError, match="InvalidCiphertextException"):
        await kms.unwrap(data_key.wrapped, data_key.key_id, context().encryption_context())


async def test_custody_seals_through_aws_kms(kms: AwsKms) -> None:
    custody = EnvelopeCustody(kms)
    bound = context()
    sealed = await custody.seal(KeyMaterial.copied(b"private key"), bound)
    assert sealed.provider == "aws-kms"
    with await custody.unseal(sealed, bound) as key:
        assert bytes(key.buffer) == b"private key"


async def test_a_key_that_cannot_wrap_stops_the_start(endpoint: str) -> None:
    disabled = admin(endpoint).create_key(KeySpec="SYMMETRIC_DEFAULT", KeyUsage="ENCRYPT_DECRYPT")[
        "KeyMetadata"
    ]
    admin(endpoint).disable_key(KeyId=disabled["KeyId"])
    with pytest.raises(KmsKeyUnusableError, match="Disabled"):
        async with AwsKms(disabled["KeyId"], region=REGION, endpoint=endpoint, session=session()).opened():
            pass
    with pytest.raises(KmsRefusedError, match="NotFoundException"):
        async with AwsKms("alias/nothing", region=REGION, endpoint=endpoint, session=session()).opened():
            pass


async def test_a_kms_that_does_not_answer_is_unavailable() -> None:
    nowhere = AwsKms(ALIAS, region=REGION, endpoint="http://127.0.0.1:1", session=session())
    with pytest.raises(KmsUnavailableError):
        async with nowhere.opened():
            pass
    with pytest.raises(KmsClientClosedError):
        await nowhere.generate({})


def test_what_aws_documents_as_transient_is_unavailable_and_the_rest_refused() -> None:
    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Decrypt")
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Decrypt")
    assert isinstance(failure_of(throttled), KmsUnavailableError)
    assert isinstance(failure_of(denied), KmsRefusedError)
    assert "slow down" not in str(failure_of(throttled))


async def test_a_key_disabled_while_vault_runs_refuses_new_data_keys(endpoint: str) -> None:
    key = admin(endpoint).create_key(KeySpec="SYMMETRIC_DEFAULT", KeyUsage="ENCRYPT_DECRYPT")["KeyMetadata"]
    async with AwsKms(key["KeyId"], region=REGION, endpoint=endpoint, session=session()).opened() as kms:
        admin(endpoint).disable_key(KeyId=key["KeyId"])
        with pytest.raises(KmsRefusedError, match="DisabledException"):
            await kms.generate(context().encryption_context())
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/vault/tests/test_kms_aws.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_vault.adapters.kms.aws'`.

- [ ] **Step 4: Implement**

`services/vault/src/ledger_vault/adapters/kms/aws.py`:

```python
"""AWS KMS through boto3: data keys generated and unwrapped by a symmetric KMS key that never leaves AWS.

boto3 is synchronous, so each call runs on a thread (``asyncio.to_thread``); its client is safe to share
between threads. Credentials come from boto3's own chain (the environment, a web identity token on Kubernetes,
an instance profile), never from vault's settings. Each call is bounded (connect 2 s, read 5 s) and retried by
boto3's standard mode (3 attempts, with backoff and jitter, on throttling and transient errors).

When the service starts (``opened``), the key must exist, be enabled, symmetric and meant for encryption: a
key
that is not is a configuration mistake, and the start fails instead of every upload later. An error code AWS
documents as transient (throttling, an internal error, a key or dependency unavailable for now) is a
``KmsUnavailableError``, and so is a request that never got an answer; any other refusal (a disabled or
missing key, a context that does not match, access denied) is a ``KmsRefusedError``.

boto3 builds its service clients at runtime, and its stubs (``types-boto3-lite``) type ``Session.client`` as
returning ``Any``: the one ``cast`` here names the typed client the stubs describe for KMS.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import ClassVar, Final, cast, final, override
lazy from collections.abc import AsyncGenerator, Mapping

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
lazy from types_boto3_kms.client import KMSClient

from ledger_kernel.errors import LedgerError
from ledger_vault.adapters.kms import DataKey, KeyManagementService, KmsRefusedError, KmsUnavailableError
from ledger_vault.domain.keys import KeyMaterial

PROVIDER: Final = "aws-kms"
_CONFIG: Final = Config(
    connect_timeout=2,
    read_timeout=5,
    retries={"mode": "standard", "max_attempts": 3},
    user_agent_extra="ledger-vault",
)
_TRANSIENT: Final = frozenset(
    {
        "ThrottlingException",
        "KMSInternalException",
        "KeyUnavailableException",
        "DependencyTimeoutException",
        "ServiceUnavailableException",
    }
)
"""The error codes AWS KMS documents as worth a later retry."""
_UNKNOWN: Final = "unknown"


class KmsClientClosedError(LedgerError):
    """The KMS was called while its client was not open: a service that never opened it."""

    code: ClassVar[str] = "kms_client_closed"

    def __init__(self) -> None:
        super().__init__("the AWS KMS client is not open")


class KmsKeyUnusableError(LedgerError):
    """The configured key cannot wrap data keys: disabled, asymmetric or not for encryption."""

    code: ClassVar[str] = "kms_key_unusable"

    def __init__(self, state: str) -> None:
        super().__init__(f"the configured KMS key cannot wrap data keys ({state})")


def failure_of(error: ClientError | BotoCoreError) -> LedgerError:
    """The ledger error a failed call to AWS KMS is: unavailable if AWS says retry, refused otherwise."""
    if isinstance(error, BotoCoreError):
        return KmsUnavailableError(type(error).__name__)
    code = error.response.get("Error", {}).get("Code", _UNKNOWN)
    return KmsUnavailableError(code) if code in _TRANSIENT else KmsRefusedError(code)


@final
class AwsKms(KeyManagementService):
    """Wraps vault's data keys with the AWS KMS key ``key_id`` (an ARN, a key id or an alias)."""

    __slots__ = ("_client", "_endpoint", "_key_id", "_region", "_session")

    def __init__(
        self,
        key_id: str,
        *,
        region: str | None,
        endpoint: str | None = None,
        session: boto3.session.Session | None = None,
    ) -> None:
        self._key_id = key_id
        self._region = region
        self._endpoint = endpoint
        self._session = session
        self._client: KMSClient | None = None

    @property
    @override
    def provider(self) -> str:
        return PROVIDER

    @override
    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[AwsKms]:
        session = boto3.session.Session() if self._session is None else self._session
        client = cast(
            "KMSClient",
            session.client("kms", region_name=self._region, endpoint_url=self._endpoint, config=_CONFIG),
        )
        try:
            self._client = client
            await self._check()
            yield self
        finally:
            self._client = None
            client.close()

    def _open(self) -> KMSClient:
        client = self._client
        if client is None:
            raise KmsClientClosedError
        return client

    async def _check(self) -> None:
        client = self._open()
        try:
            described = await asyncio.to_thread(client.describe_key, KeyId=self._key_id)
        except (ClientError, BotoCoreError) as error:
            raise failure_of(error) from error
        metadata = described["KeyMetadata"]
        state = (metadata.get("KeyState"), metadata.get("KeySpec"), metadata.get("KeyUsage"))
        if state != ("Enabled", "SYMMETRIC_DEFAULT", "ENCRYPT_DECRYPT"):
            raise KmsKeyUnusableError("/".join(str(part) for part in state))

    @override
    async def generate(self, context: Mapping[str, str]) -> DataKey:
        client = self._open()
        try:
            answer = await asyncio.to_thread(
                client.generate_data_key,
                KeyId=self._key_id,
                KeySpec="AES_256",
                EncryptionContext=dict(context),
            )
        except (ClientError, BotoCoreError) as error:
            raise failure_of(error) from error
        return DataKey(KeyMaterial.copied(answer["Plaintext"]), answer["CiphertextBlob"], answer["KeyId"])

    @override
    async def unwrap(self, wrapped: bytes, key_id: str, context: Mapping[str, str]) -> KeyMaterial:
        client = self._open()
        try:
            answer = await asyncio.to_thread(
                client.decrypt, CiphertextBlob=wrapped, KeyId=key_id, EncryptionContext=dict(context)
            )
        except (ClientError, BotoCoreError) as error:
            raise failure_of(error) from error
        return KeyMaterial.copied(answer["Plaintext"])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/vault/tests/test_kms_aws.py -q --no-cov`
Expected: PASS (about 20 s: LocalStack starts once per module). Verified as written against LocalStack 4.14.0 on 2026-10-12.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`_failure`'s three outcomes: a `BotoCoreError` from the unreachable endpoint, a transient code is not reachable against LocalStack — if coverage reports `KmsUnavailableError(code)` uncovered, add a unit test of `_failure` with `ClientError({"Error": {"Code": "ThrottlingException"}}, "Decrypt")`, which botocore's typed constructor accepts).

- [ ] **Step 7: Commit**

```bash
git add uv.lock services/vault
git commit -m "feat(vault): AWS KMS wraps the data keys, checked at start, tested on LocalStack

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The DGII XMLDSig signer, proven by xmlsec1 and signxml

**Files:**
- Create: `services/vault/src/ledger_vault/adapters/xml.py`
- Modify: `services/vault/pyproject.toml`
- Test: `services/vault/tests/test_xml.py`

**Interfaces:**
- Consumes: `XmlSigner`, `SignedDocument` (Task 5), `SigningPool` (Task 6), the document errors and `SigningPurpose` (Task 4); `Xmlsec`, `pki` (Task 2).
- Produces: `ledger_vault.adapters.xml`: the algorithm URIs (`DS`, `C14N`, `RSA_SHA256`, `SHA256`, `ENVELOPED`), `DEFAULT_MAX_DOCUMENT = 1 MiB`, `SEED_ROOT`, `SEED_CHILDREN`, `signed_info(digest, in_scope=None)`, `signature(digest, value, certificate)`, `sign_enveloped(document, key, certificate, purpose) -> SignedDocument`, `DgiiXmlSigner(pool, *, limit)`, `NotAnRsaKeyError`.

- [ ] **Step 1: Dependencies**

`services/vault/pyproject.toml` — add `"lxml>=6.1.3"`. Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`services/vault/tests/test_xml.py` — the golden canonical form and signature shape, every document shape through xmlsec1 with its chain, signxml on those without nodes outside the root (Decision 13), a tampered document, hostile documents, and two property tests (60 documents through signxml with the round-trip law, 15 through xmlsec1):

```python
import base64
import hashlib
import re
from abc import ABCMeta, abstractmethod
from datetime import UTC, datetime
from typing import Final, Protocol, cast, final
lazy from collections.abc import Iterator

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.verifier import SignatureConfiguration, VerifyResult, XMLVerifier
lazy from cryptography import x509

from ledger_testing.pki import Authority, ec_key, pem, person
from ledger_testing.xmlsec import Xmlsec
from ledger_vault.adapters.pool import SigningPool
from ledger_vault.adapters.xml import DS, DgiiXmlSigner, NotAnRsaKeyError, sign_enveloped
from ledger_vault.domain.errors import (
    DocumentDoctypeError,
    DocumentMalformedError,
    DocumentSignedError,
    DocumentTooLargeError,
    SeedMalformedError,
)
from ledger_vault.domain.keys import KeyMaterial
from ledger_vault.domain.signing import SigningPurpose

ECF: Final = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<ECF xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
    "<Encabezado><Version>1.0</Version><IdDoc><TipoeCF>31</TipoeCF><eNCF>E310000000001</eNCF></IdDoc>"
    "<Emisor><RNCEmisor>101850043</RNCEmisor>"
    "<RazonSocialEmisor>Ferretería Duarte &amp; Hijos</RazonSocialEmisor></Emisor></Encabezado>"
    "<!-- a comment --><DetallesItems><Item><NombreItem>Martillo &lt;12&quot;&gt;</NombreItem></Item>"
    "</DetallesItems>"
    "<FechaHoraFirma>12-10-2026 09:00:00</FechaHoraFirma></ECF>"
).encode()
SEED: Final = (
    b'<?xml version="1.0" encoding="utf-8"?><SemillaModel xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    b' xmlns:xsd="http://www.w3.org/2001/XMLSchema"><valor>NmE1ZDg2YzQtMTc5OS00ZmM4</valor>'
    b"<fecha>2026-10-12T09:00:00.0000000-04:00</fecha></SemillaModel>"
)
SHAPES: Final = {
    "ecf": ECF,
    "seed": SEED,
    "plain": b"<a><b>x</b></a>",
    "default-namespace": b'<Doc xmlns="urn:d" a="1"><x:Part xmlns:x="urn:example:x">t</x:Part></Doc>',
    "nested-defaults": b'<r><x xmlns="urn:a"><y><z q="1"/></y></x><w xmlns:p="urn:p"><p:v/></w></r>',
    "references": b'<r>Pe\xc3\xb1a &#241; &#x1F600; &lt; &gt; &amp; "q"<e a="&#9;tab&#10;nl&#13;"/></r>',
    "outside-root": b'<?xml version="1.0"?><?pi before?><!-- c --><r><!-- in --><?pi x?>t</r><!-- after -->',
}
GOLDEN_C14N_INPUT: Final = (
    b'<?xml version="1.0" encoding="utf-8"?>\n<?pi before?>\n<ECF  b="2" a="1&#9;&#10;x"'
    b' xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    b"<Empty/><!-- gone --><T>Pe&#241;a &lt;12&quot;&gt; \r\n&amp;</T>"
    b'<x:P xmlns:x="urn:x" xmlns="urn:d"><Q/></x:P></ECF>'
)
GOLDEN_C14N: Final = (
    b'<?pi before?>\n<ECF xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
    b' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" a="1&#x9;&#xA;x" b="2"><Empty></Empty>'
    b'<T>Pe\xc3\xb1a &lt;12"&gt; \n&amp;</T><x:P xmlns="urn:d" xmlns:x="urn:x"><Q></Q></x:P></ECF>'
)
"""The inclusive canonical form (C14N 1.0, without comments) of the input above: attributes sorted and their
whitespace written as references, CR LF read as LF, empty elements written out, comments dropped, the
processing instruction before the root kept with its line break, namespace declarations first and sorted."""
GOLDEN_SIGNATURE: Final = (
    '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignedInfo>'
    '<CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>'
    '<SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>'
    '<Reference URI=""><Transforms><Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>'
    '</Transforms><DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
    "<DigestValue>{digest}</DigestValue></Reference></SignedInfo><SignatureValue>{value}</SignatureValue>"
    "<KeyInfo><X509Data><X509Certificate>{certificate}</X509Certificate></X509Data></KeyInfo></Signature>"
)
"""DGII's shape (*Firmado de e-CF*), exactly: what a signature looks like with its three values taken out."""
VALUES: Final = re.compile(r"<(DigestValue|SignatureValue|X509Certificate)>([^<]*)<")
CONFIGURATION: Final = SignatureConfiguration(
    require_x509=True,
    location="./",
    expect_references=1,
    signature_methods=frozenset({SignatureMethod.RSA_SHA256}),
    digest_algorithms=frozenset({DigestAlgorithm.SHA256}),
)


class _Verifies(Protocol, metaclass=ABCMeta):
    """signxml's ``XMLVerifier`` as these tests call it.

    signxml 5.1 leaves ``verify``'s ``data``, ``parser`` and ``**deprecated_kwargs`` unannotated, which
    basedpyright ``all`` reports wherever the method is touched: the ``cast`` below states the arguments these
    tests pass. ``excise_empty_xmlns_declarations`` makes signxml undo lxml's spurious ``xmlns=""`` (its
    issue 193; see ``ledger_vault.adapters.xml``), without which it cannot verify a signature in the default
    namespace.
    """

    excise_empty_xmlns_declarations: bool

    @abstractmethod
    def verify(
        self, data: bytes, *, x509_cert: x509.Certificate, expect_config: SignatureConfiguration
    ) -> VerifyResult | list[VerifyResult]:
        """Verify ``data`` with ``x509_cert``."""


def verify_with_signxml(signed: bytes, certificate: x509.Certificate) -> bytes:
    verifier = cast("_Verifies", XMLVerifier())
    verifier.excise_empty_xmlns_declarations = True
    result = verifier.verify(signed, x509_cert=certificate, expect_config=CONFIGURATION)
    assert isinstance(result, VerifyResult)
    return result.signed_data


@final
class Signer:
    def __init__(self) -> None:
        self.root = Authority.root()
        self.intermediate = self.root.intermediate()
        self.issued = self.intermediate.issue(person())
        self.der = self.issued.certificate.public_bytes(Encoding.DER)
        self.key = self.issued.key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())

    def sign(self, document: bytes, purpose: SigningPurpose = SigningPurpose.DOCUMENT) -> bytes:
        return sign_enveloped(document, KeyMaterial.copied(self.key), self.der, purpose).content

    def verified_by_xmlsec(self, xmlsec: Xmlsec, signed: bytes) -> bool:
        verdict = xmlsec.verify(
            signed,
            trusted=pem(self.root.certificate),
            untrusted=pem(self.intermediate.certificate),
            at=datetime.now(UTC),
        )
        return verdict.verified


@pytest.fixture(scope="module")
def signer() -> Signer:
    return Signer()


@pytest.fixture(scope="module")
def xmlsec() -> Iterator[Xmlsec]:
    with Xmlsec.started() as started:
        yield started


def without_signature(signed: bytes) -> bytes:
    root = etree.fromstring(signed)
    signature = root.find(f"{{{DS}}}Signature")
    assert signature is not None
    root.remove(signature)
    return etree.tostring(root.getroottree(), method="c14n", exclusive=False, with_comments=False)


def canonical(document: bytes) -> bytes:
    return etree.tostring(
        etree.fromstring(document).getroottree(), method="c14n", exclusive=False, with_comments=False
    )


@pytest.mark.parametrize("shape", list(SHAPES), ids=list(SHAPES))
def test_every_shape_of_document_verifies_in_xmlsec1_with_its_chain(
    signer: Signer, xmlsec: Xmlsec, shape: str
) -> None:
    purpose = SigningPurpose.SEED if shape == "seed" else SigningPurpose.DOCUMENT
    signed = signer.sign(SHAPES[shape], purpose)
    assert signer.verified_by_xmlsec(xmlsec, signed)


@pytest.mark.parametrize("shape", [shape for shape in SHAPES if shape != "outside-root"])
def test_signxml_verifies_them_too(signer: Signer, shape: str) -> None:
    signed = signer.sign(SHAPES[shape], SigningPurpose.SEED if shape == "seed" else SigningPurpose.DOCUMENT)
    assert verify_with_signxml(signed, signer.issued.certificate)


def test_a_tampered_document_fails_both_verifiers(signer: Signer, xmlsec: Xmlsec) -> None:
    tampered = signer.sign(ECF).replace(b"101850043", b"101850044")
    assert not signer.verified_by_xmlsec(xmlsec, tampered)
    with pytest.raises(Exception, match="Digest mismatch"):
        verify_with_signxml(tampered, signer.issued.certificate)


def test_the_signature_has_exactly_dgii_s_shape(signer: Signer) -> None:
    signed = signer.sign(ECF)
    found = re.search(rb"<Signature .*</Signature>", signed)
    assert found is not None
    element = found.group().decode()
    found_values = [match.group(2) for match in VALUES.finditer(element)]
    values = dict(zip(("digest", "value", "certificate"), found_values, strict=True))
    assert element == GOLDEN_SIGNATURE.format(**values)
    assert base64.b64decode(values["digest"]) == hashlib.sha256(canonical(ECF)).digest()
    assert base64.b64decode(values["certificate"]) == signer.der
    assert signed.endswith(GOLDEN_SIGNATURE.format(**values).encode() + b"</ECF>")


def test_the_canonical_form_is_c14n_1_0_inclusive_without_comments() -> None:
    assert canonical(GOLDEN_C14N_INPUT) == GOLDEN_C14N


def test_the_audit_digest_is_of_the_document_as_received(signer: Signer) -> None:
    signed = sign_enveloped(ECF, KeyMaterial.copied(signer.key), signer.der, SigningPurpose.DOCUMENT)
    assert signed.document_sha256 == hashlib.sha256(ECF).digest()


@pytest.mark.parametrize(
    ("document", "purpose", "error"),
    [
        (b"<a>", SigningPurpose.DOCUMENT, DocumentMalformedError),
        (b"not xml", SigningPurpose.DOCUMENT, DocumentMalformedError),
        (b'<!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', SigningPurpose.DOCUMENT, DocumentDoctypeError),
        (
            b'<!DOCTYPE a [<!ENTITY a "aaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><a>&b;</a>',
            SigningPurpose.DOCUMENT,
            DocumentDoctypeError,
        ),
        (
            b'<!DOCTYPE a SYSTEM "http://169.254.169.254/latest/meta-data/"><a/>',
            SigningPurpose.DOCUMENT,
            DocumentDoctypeError,
        ),
        (b"<SemillaModel><fecha/><valor/></SemillaModel>", SigningPurpose.SEED, SeedMalformedError),
        (b"<Semilla><valor/><fecha/></Semilla>", SigningPurpose.SEED, SeedMalformedError),
        (ECF, SigningPurpose.SEED, SeedMalformedError),
    ],
    ids=[
        "unclosed",
        "text",
        "internal-entity",
        "billion-laughs",
        "external-dtd",
        "seed-order",
        "seed-root",
        "ecf-as-seed",
    ],
)
def test_a_document_that_cannot_be_signed_is_refused_unread(
    signer: Signer, document: bytes, purpose: SigningPurpose, error: type[Exception]
) -> None:
    with pytest.raises(error) as caught:
        signer.sign(document, purpose)
    assert document.decode("utf-8", "replace")[:20] not in str(caught.value)


def test_a_signed_document_is_not_signed_again(signer: Signer) -> None:
    signed = signer.sign(ECF)
    with pytest.raises(DocumentSignedError):
        signer.sign(signed)
    with pytest.raises(DocumentSignedError):
        signer.sign(f'<Signature xmlns="{DS}"/>'.encode())


NAMESPACES: Final[tuple[dict[str | None, str], ...]] = ({}, {"xsi": "urn:xsi"}, {None: "urn:d"})
_NAMES: Final = st.sampled_from(["a", "b", "Item", "eNCF", "x-y", "z.1"])
_TEXT: Final = st.text(
    st.characters(codec="utf-8", exclude_categories=("Cs", "Cc", "Co"), include_characters="\t\n\r"),
    max_size=12,
).filter(lambda text: "￾" not in text and "￿" not in text)


@st.composite
def documents(draw: st.DrawFn) -> bytes:
    """Well-formed documents: nested elements, attributes, text of any character XML allows, comments,
    prefixed and default namespaces declared on the root or below."""
    root = etree.Element(draw(_NAMES), nsmap=draw(st.sampled_from(NAMESPACES)))
    elements = [root]
    for _ in range(draw(st.integers(0, 8))):
        parent = draw(st.sampled_from(elements))
        namespace = draw(st.sampled_from([None, "urn:n"]))
        tag = draw(_NAMES) if namespace is None else f"{{{namespace}}}{draw(_NAMES)}"
        child = etree.SubElement(parent, tag, {draw(_NAMES): draw(_TEXT)} if draw(st.booleans()) else {})
        child.text = draw(_TEXT)
        if draw(st.booleans()):
            parent.append(etree.Comment(draw(st.sampled_from(["c", " a note "]))))
        elements.append(child)
    root.text = draw(_TEXT)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(documents())
def test_any_well_formed_document_round_trips_and_verifies(signer: Signer, document: bytes) -> None:
    signed = signer.sign(document)
    assert without_signature(signed) == canonical(document)
    assert verify_with_signxml(signed, signer.issued.certificate)


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(documents())
def test_xmlsec1_agrees_on_any_well_formed_document(signer: Signer, xmlsec: Xmlsec, document: bytes) -> None:
    assert signer.verified_by_xmlsec(xmlsec, signer.sign(document))


async def test_a_document_over_the_limit_is_refused_unread(signer: Signer) -> None:
    async with SigningPool(1).opened() as pool:
        with pytest.raises(DocumentTooLargeError, match="at most 10 bytes"):
            await DgiiXmlSigner(pool, limit=10).sign(
                b"<a>longer than ten</a>", KeyMaterial.copied(signer.key), signer.der, SigningPurpose.DOCUMENT
            )
        signed = await DgiiXmlSigner(pool).sign(
            ECF, KeyMaterial.copied(signer.key), signer.der, SigningPurpose.DOCUMENT
        )
    assert signed.content.endswith(b"</Signature></ECF>")


def test_only_an_rsa_key_signs(signer: Signer) -> None:
    curve = ec_key().private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    with pytest.raises(NotAnRsaKeyError):
        sign_enveloped(ECF, KeyMaterial.copied(curve), signer.der, SigningPurpose.DOCUMENT)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/vault/tests/test_xml.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_vault.adapters.xml'`.

- [ ] **Step 4: Implement**

`services/vault/src/ledger_vault/adapters/xml.py`:

```python
"""XML signatures as DGII verifies them (§5.1; DGII, *Firmado de e-CF*, 2023).

An enveloped XMLDSig signature, appended as the last child of the document's root element::

    <Signature xmlns="http://www.w3.org/2000/09/xmldsig#">
      <SignedInfo>
        <CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>
        <SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>
        <Reference URI="">
          <Transforms><Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/></Transforms>
          <DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
          <DigestValue>...</DigestValue>
        </Reference>
      </SignedInfo>
      <SignatureValue>...</SignatureValue>
      <KeyInfo><X509Data><X509Certificate>...</X509Certificate></X509Data></KeyInfo>
    </Signature>

(without the indentation: the element is written on one line). The reference covers the whole document
(``URI=""``) less the signature; its digest is SHA-256 over the document's inclusive canonical form (C14N 1.0,
without comments), taken before the signature is added, which is the same thing. ``SignatureValue`` is
RSASSA-PKCS1-v1_5 with SHA-256 over the canonical form of ``SignedInfo``.

That canonical form is taken in the signature's place in the document, where inclusive C14N gives
``SignedInfo`` every namespace declaration in scope: the default namespace of ``Signature`` and the prefixes
the root element declares (an e-CF's ``xmlns:xsi`` and ``xmlns:xsd``). It is built as text, declaring exactly
those, and canonicalized on its own: lxml's C14N of an element inside a document writes a spurious
``xmlns=""`` on its grandchildren under a default namespace (lxml 6.1 with libxml2 2.11 and 2.14; signxml's
issue 193), and a signature over that form fails in xmlsec1 and in .NET. The independent verifier
(``ledger_testing.xmlsec``) holds every signature this module makes to that.

Documents are parsed hardened (§9): the size is checked before parsing, entities are not resolved, no DTD is
loaded and a document that declares one is refused, nothing is fetched from the network, ``huge_tree`` is off
(libxml2's own limits on depth and text stay). A document that already holds a signature is refused: an
enveloped signature would leave it unsigned by this one. A seed must be the ``SemillaModel`` DGII hands out.

The signed document is the received one written again by lxml (UTF-8, with its declaration): equal to it under
canonicalization, which is all a signature covers, though not always byte for byte (character references come
out as characters, attributes in double quotes).
"""

import base64
import hashlib
from functools import partial
from typing import ClassVar, Final, final, override
from xml.sax.saxutils import quoteattr
lazy from collections.abc import Mapping

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_der_private_key
from lxml import etree

from ledger_kernel.errors import LedgerError
from ledger_vault.application.ports import SignedDocument, XmlSigner
from ledger_vault.domain.errors import (
    DocumentDoctypeError,
    DocumentMalformedError,
    DocumentSignedError,
    DocumentTooLargeError,
    SeedMalformedError,
)
from ledger_vault.domain.signing import SigningPurpose
lazy from ledger_vault.adapters.pool import SigningPool
lazy from ledger_vault.domain.keys import KeyMaterial

DS: Final = "http://www.w3.org/2000/09/xmldsig#"
C14N: Final = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
RSA_SHA256: Final = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
SHA256: Final = "http://www.w3.org/2001/04/xmlenc#sha256"
ENVELOPED: Final = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
DEFAULT_MAX_DOCUMENT: Final = 1024 * 1024
"""An e-CF of a thousand lines is a few hundred kilobytes; gRPC's own limit on a message is 4 MiB."""
SEED_ROOT: Final = "SemillaModel"
SEED_CHILDREN: Final = ("valor", "fecha")


class NotAnRsaKeyError(LedgerError):
    """A key that is not RSA reached the signer: custody admits RSA keys only, so this is a defect."""

    code: ClassVar[str] = "not_an_rsa_key"

    def __init__(self) -> None:
        super().__init__("only RSA keys sign")


def _parser() -> etree.XMLParser:
    """A parser that reads no DTD, resolves no entity and fetches nothing (one per call: it keeps state)."""
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        collect_ids=False,
        remove_blank_text=False,
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def signed_info(digest: str, in_scope: Mapping[str, str] | None = None) -> str:
    """``SignedInfo`` as text: in the document (``in_scope`` is ``None``), or standalone for canonicalization.

    Standalone, it declares what is in scope where it is placed: the default namespace of ``Signature`` and
    ``in_scope``, the prefixed namespaces of the document's root element.
    """
    if in_scope is None:
        declarations = ""
    else:
        prefixed = "".join(f" xmlns:{prefix}={quoteattr(uri)}" for prefix, uri in sorted(in_scope.items()))
        declarations = f' xmlns="{DS}"{prefixed}'
    return (
        f"<SignedInfo{declarations}>"
        f'<CanonicalizationMethod Algorithm="{C14N}"/>'
        f'<SignatureMethod Algorithm="{RSA_SHA256}"/>'
        f'<Reference URI=""><Transforms><Transform Algorithm="{ENVELOPED}"/></Transforms>'
        f'<DigestMethod Algorithm="{SHA256}"/><DigestValue>{digest}</DigestValue></Reference>'
        "</SignedInfo>"
    )


def signature(digest: str, value: str, certificate: str) -> str:
    """The ``Signature`` element as text, the XMLDSig namespace its default one."""
    return (
        f'<Signature xmlns="{DS}">{signed_info(digest)}'
        f"<SignatureValue>{value}</SignatureValue>"
        f"<KeyInfo><X509Data><X509Certificate>{certificate}</X509Certificate></X509Data></KeyInfo>"
        "</Signature>"
    )


def _rsa(key: KeyMaterial) -> RSAPrivateKey:
    """The private key ``key`` holds, not checked for consistency again.

    The check is 30 ms of RSA arithmetic per signature. It was made when the bundle was opened, and the
    AES-GCM tag of the sealed key proves these are the same bytes.
    """
    loaded = load_der_private_key(key.buffer, None, unsafe_skip_rsa_key_validation=True)
    if not isinstance(loaded, RSAPrivateKey):
        raise NotAnRsaKeyError
    return loaded


def sign_enveloped(
    document: bytes, key: KeyMaterial, certificate: bytes, purpose: SigningPurpose
) -> SignedDocument:
    """``document`` signed with ``key`` (see the module); runs on the signing pool."""
    try:
        root = etree.fromstring(document, _parser())
    except etree.XMLSyntaxError as error:
        raise DocumentMalformedError from error
    tree = root.getroottree()
    if tree.docinfo.doctype:
        raise DocumentDoctypeError
    if root.find(f".//{{{DS}}}Signature") is not None or root.tag == f"{{{DS}}}Signature":
        raise DocumentSignedError
    elements = [child.tag for child in root if isinstance(child.tag, str)]
    if purpose is SigningPurpose.SEED and (root.tag != SEED_ROOT or elements != list(SEED_CHILDREN)):
        raise SeedMalformedError
    canonical = etree.tostring(tree, method="c14n", exclusive=False, with_comments=False)
    digest = _b64(hashlib.sha256(canonical).digest())
    in_scope = {prefix: uri for prefix, uri in root.nsmap.items() if prefix is not None}
    canonical_info = etree.tostring(
        etree.fromstring(signed_info(digest, in_scope)), method="c14n", exclusive=False, with_comments=False
    )
    value = _b64(_rsa(key).sign(canonical_info, padding.PKCS1v15(), hashes.SHA256()))
    root.append(etree.fromstring(signature(digest, value, _b64(certificate))))
    content = etree.tostring(tree, xml_declaration=True, encoding="UTF-8")
    return SignedDocument(content, hashlib.sha256(document).digest())


@final
class DgiiXmlSigner(XmlSigner):
    """Signs on the signing pool, refusing documents over ``limit`` bytes unread (see the module)."""

    __slots__ = ("_limit", "_pool")

    def __init__(self, pool: SigningPool, *, limit: int = DEFAULT_MAX_DOCUMENT) -> None:
        self._pool = pool
        self._limit = limit

    @override
    async def sign(
        self, document: bytes, key: KeyMaterial, certificate: bytes, purpose: SigningPurpose
    ) -> SignedDocument:
        if len(document) > self._limit:
            raise DocumentTooLargeError(self._limit)
        return await self._pool.run(partial(sign_enveloped, document, key, certificate, purpose))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/vault/tests/test_xml.py -q --no-cov`
Expected: PASS (28 tests, a few seconds once the xmlsec1 image exists). Verified as written on 2026-10-12: every shape and every generated document verifies in xmlsec1 1.2.41 with its chain; signxml agrees with `excise_empty_xmlns_declarations`; the golden `Signature` and canonical form match.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`DgiiXmlSigner.sign`'s size refusal and `NotAnRsaKeyError` are covered in Task 11's servicer tests; if the gate runs before then, add here: `DgiiXmlSigner(pool, limit=10).sign(b"<a>this is too long</a>", ...)` raises `DocumentTooLargeError`, and `sign_enveloped` with an EC key's PKCS#8 raises `NotAnRsaKeyError`).

- [ ] **Step 7: Commit**

```bash
git add uv.lock services/vault
git commit -m "feat(vault): XML signatures as DGII verifies them, proven by xmlsec1 and signxml

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Vault's settings, schema and migrations

**Files:**
- Create: `services/vault/src/ledger_vault/settings.py`
- Create: `services/vault/src/ledger_vault/adapters/postgres/__init__.py`, `tables.py`, `migrate.py`, `migrations/__init__.py`, `migrations/env.py`, `migrations/versions/__init__.py`, `migrations/versions/r0001_vault.py`
- Modify: `services/vault/pyproject.toml`, `pyproject.toml` (root)
- Test: `services/vault/tests/conftest.py`, `services/vault/tests/test_settings.py`, `services/vault/tests/test_migrations.py`

**Interfaces:**
- Consumes: `ServiceSettings` (M1, with M2's additions); `Migrations`, `run_migrations`, `SchemaNotCurrentError` (Task 3); `CHASSIS_METADATA`, `CHASSIS_POLICIES` (`ledger_platform.schema`, M2 Task 5); `tenant_isolation`, `Database`; `MAX_SUBJECT_LENGTH`; `MAX_REFERENCE` (Task 4); `Postgres`, `restored_logging` (`ledger_testing`, M2 Task 2).
- Produces: `ledger_vault.settings`: `KmsProvider` (`AWS`, `LOCAL`), `KmsProblem`, `VaultSettings(ServiceSettings)` with `kms_provider` (required), `kms_key_id`, `kms_region`, `kms_endpoint_url`, `local_kms_key: SecretStr | None` (base64 of 32 bytes), `trusted_cas_path: Path | None`, `accept_representatives = True`, `max_bundle_bytes = 65536`, `max_document_bytes = 1048576`, `signing_threads = 4`, `data_key_cache_seconds = 0`, `expiry_check_seconds = 3600`, and `local_key() -> bytes`. Its validator refuses: AWS without `kms_key_id` or `kms_region`; local without `local_kms_key`, or with a key that is not 32 bytes; **local in `production`**; no trust store in `staging` or `production`; an endpoint that is not `https://` there. `ledger_vault.adapters.postgres.tables`: `VaultTable`, `CertificateRecord` (`certificates`), `SignatureRecordRow` (`signatures`), `ExpiryWatchRecord` (`expiry_watch`). `ledger_vault.adapters.postgres.migrate`: `SCRIPTS`, `MIGRATIONS`, `upgrade`, `downgrade`, `head`, `require_head`, `main` (console script `ledger-vault-migrate`). Revision `0001`: the chassis's tables and policies, the three tables, `tenant_isolation` on `certificates` and `signatures` (not on `expiry_watch`: Decision 8), the trigger function `vault_refuse_change()` and the triggers `signatures_append_only` (before update or delete) and `certificates_kept` (before delete). Test fixtures: `postgres`, `database_url` (migrated), `fresh_database_url`, `database` (tables emptied after each test, the audit's trigger notwithstanding: `TRUNCATE` fires no row trigger).

Re-check before starting: identity's `conftest.py` and `test_migrations.py` (M2 Task 11): vault's follow them; `CHASSIS_POLICIES` and the chassis's DDL as Milestone 2's `r0001_identity` spells them (vault's revision spells them out the same way: a revision records what was done, M2 Decision 13).

- [ ] **Step 1: Dependencies, script, layering**

`services/vault/pyproject.toml`:

```toml
[project]
name = "ledger-vault"
version = "0.1.0"
description = "The vault service: custody of companies' signing certificates, and XML signatures made with them."
requires-python = ">=3.15"
dependencies = [
    "ledger-kernel",
    "ledger-platform",
    "alembic>=1.20.0",
    "boto3>=1.43.111",
    "botocore>=1.43.111",
    "cryptography>=50.0.2",
    "lxml>=6.1.3",
    "pydantic>=2.14.1",
    "sqlalchemy>=2.1.4",
]

[project.scripts]
ledger-vault-migrate = "ledger_vault.adapters.postgres.migrate:main"

[tool.uv.sources]
ledger-kernel = { workspace = true }
ledger-platform = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

Root `pyproject.toml`: `"ledger_vault.adapters.postgres.tables.VaultTable"` in ruff's `runtime-evaluated-base-classes`; "Vault is hexagonal" `layers = ["adapters", "settings", "application", "domain"]`; `"alembic"` is already in both vault `forbidden` contracts. Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`services/vault/tests/conftest.py`:

```python
"""Fixtures of vault's tests: a real PostgreSQL migrated to this version's schema."""

lazy from collections.abc import AsyncGenerator, Generator

import pytest
from sqlalchemy import text

from ledger_kernel.ids import new_id
from ledger_platform.database import Database
from ledger_testing.containers import Postgres
from ledger_testing.logs import restored_logging
from ledger_vault.adapters.postgres.migrate import upgrade

EMPTY_EVERY_TABLE = text("TRUNCATE signatures, expiry_watch, certificates, outbox, inbox, idempotency_keys")
"""``TRUNCATE`` is the owner's, fires no row trigger and is not filtered by row-level security; in production
the runtime role holds no ``TRUNCATE`` (§7: the migration owner is another role)."""


@pytest.fixture(autouse=True)
def restore_logging() -> Generator[None]:
    """Each test starts from, and leaves, the plain baseline: building a service configures logging."""
    with restored_logging():
        yield


@pytest.fixture(scope="session")
def postgres() -> Generator[Postgres]:
    """A real PostgreSQL for the whole session."""
    with Postgres.started() as server:
        yield server


@pytest.fixture(scope="session")
def database_url(postgres: Postgres) -> str:
    """vault's database, migrated to this version; the service connects as its owner, no superuser."""
    url = postgres.database("vault", role="vault", password="vault-password")
    upgrade(url)
    return url


@pytest.fixture
def fresh_database_url(postgres: Postgres) -> str:
    """An empty database of its own, owned by a role of its own: for the tests of the migrations."""
    name = f"fresh_{new_id().hex[16:]}"
    return postgres.database(name, role=name, password="fresh-password")


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """A pool on vault's database, every table emptied after the test."""
    async with Database.open(database_url, pool_size=4) as opened:
        yield opened
        async with opened.engine.begin() as connection:
            await connection.execute(EMPTY_EVERY_TABLE)
```

`services/vault/tests/test_settings.py`:

```python
import base64
from pathlib import Path
from typing import Final

import pytest
from pydantic import SecretStr, ValidationError

from ledger_vault.settings import KmsProvider, VaultSettings

DATABASE: Final = SecretStr("postgresql+psycopg://vault:secret@localhost/vault")
LOCAL_KEY: Final = SecretStr(base64.b64encode(bytes(range(32))).decode())


def test_a_laptop_runs_on_the_local_kms() -> None:
    settings = VaultSettings(database_url=DATABASE, kms_provider=KmsProvider.LOCAL, local_kms_key=LOCAL_KEY)
    assert (settings.service_name, settings.local_key()) == ("vault", bytes(range(32)))
    assert (settings.signing_threads, settings.data_key_cache_seconds, settings.accept_representatives) == (4, 0, True)


def test_aws_needs_a_key_and_a_region() -> None:
    settings = VaultSettings(
        database_url=DATABASE, kms_provider=KmsProvider.AWS, kms_key_id="alias/ledger-vault", kms_region="us-east-1"
    )
    assert settings.kms_endpoint_url is None
    with pytest.raises(ValidationError, match="kms_key_id and kms_region"):
        VaultSettings(database_url=DATABASE, kms_provider=KmsProvider.AWS)


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"local_kms_key": None}, "local_kms_key"),
        ({"local_kms_key": SecretStr("c2hvcnQ=")}, "32 bytes"),
        ({"local_kms_key": SecretStr("not base64!")}, "32 bytes"),
        ({"environment": "production"}, "never in production"),
        ({"environment": "staging"}, "trusted_cas_path"),
        ({"data_key_cache_seconds": 301}, "data_key_cache_seconds"),
        ({"signing_threads": 0}, "signing_threads"),
    ],
    ids=["no-key", "short-key", "not-base64", "production", "staging-without-trust", "cache", "threads"],
)
def test_a_configuration_that_cannot_hold_keys_safely_does_not_start(changes: dict[str, object], problem: str) -> None:
    values: dict[str, object] = {"database_url": DATABASE, "kms_provider": KmsProvider.LOCAL, "local_kms_key": LOCAL_KEY}
    with pytest.raises(ValidationError, match=problem) as caught:
        VaultSettings.model_validate(values | changes)
    assert LOCAL_KEY.get_secret_value() not in str(caught.value)


def test_beyond_a_laptop_the_kms_is_reached_over_https(tmp_path: Path) -> None:
    roots = tmp_path / "roots.pem"
    roots.write_bytes(b"")
    common: dict[str, object] = {
        "database_url": DATABASE,
        "environment": "production",
        "kms_provider": KmsProvider.AWS,
        "kms_key_id": "alias/ledger-vault",
        "kms_region": "us-east-1",
        "trusted_cas_path": roots,
    }
    assert VaultSettings.model_validate(common).trusted_cas_path == roots
    with pytest.raises(ValidationError, match="https"):
        VaultSettings.model_validate(common | {"kms_endpoint_url": "http://kms.internal"})
```

`services/vault/tests/test_migrations.py`:

```python
from typing import Final

import pytest
from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from ledger_kernel.errors import ErrorKind
from ledger_platform.database import Database
from ledger_platform.migrations import SchemaNotCurrentError
from ledger_platform.schema import CHASSIS_METADATA
from ledger_vault.adapters.postgres import migrate
from ledger_vault.adapters.postgres.migrate import downgrade, head, require_head, upgrade
from ledger_vault.adapters.postgres.tables import VaultTable

VAULT_TABLES: Final = frozenset({"certificates", "signatures", "expiry_watch"})
CHASSIS_TABLES: Final = frozenset({"outbox", "inbox", "idempotency_keys"})
PAIRS: Final = TypeAdapter(list[tuple[str, str]])


def tables_of(url: str) -> set[str]:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def models() -> MetaData:
    combined = MetaData()
    for table in (*CHASSIS_METADATA.tables.values(), *VaultTable.metadata.tables.values()):
        table.to_metadata(combined)
    return combined


def pending_changes(url: str) -> str | None:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True})
            operations = produce_migrations(context, models()).upgrade_ops
            return None if operations is None or operations.is_empty() else render_python_code(operations)
    finally:
        engine.dispose()


def test_the_schema_is_built_from_nothing_and_taken_back_to_nothing(fresh_database_url: str) -> None:
    upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {*VAULT_TABLES, *CHASSIS_TABLES, "alembic_version"}
    downgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {"alembic_version"}
    upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) >= VAULT_TABLES


def test_the_migrated_schema_is_exactly_the_models(database_url: str) -> None:
    upgrade(database_url)
    assert pending_changes(database_url) is None


def test_the_company_s_data_is_under_its_policy_and_the_watch_is_not(database_url: str) -> None:
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            policies = PAIRS.validate_python(
                [tuple(row) for row in connection.execute(text("SELECT tablename::text, policyname::text FROM pg_policies"))]
            )
            triggers = PAIRS.validate_python(
                [
                    tuple(row)
                    for row in connection.execute(
                        text("SELECT tgrelid::regclass::text, tgname::text FROM pg_trigger WHERE NOT tgisinternal")
                    )
                ]
            )
    finally:
        engine.dispose()
    assert sorted(policies) == [
        ("certificates", "tenant_isolation"),
        ("idempotency_keys", "organization_isolation"),
        ("signatures", "tenant_isolation"),
    ]
    assert sorted(triggers) == [("certificates", "certificates_kept"), ("signatures", "signatures_append_only")]


```

(The triggers themselves are proven with rows in place, in Task 10's `test_signatures_are_recorded_and_the_audit_is_append_only_in_postgresql_itself`: a row trigger does not fire for a statement that touches no row.)

Continued:

```python
async def test_the_readiness_check_passes_on_a_database_at_this_version_only(
    database: Database, fresh_database_url: str
) -> None:
    assert head() == "0001"
    await require_head(database, head())
    async with Database.open(fresh_database_url, pool_size=1) as empty:
        with pytest.raises(SchemaNotCurrentError) as caught:
            await require_head(empty, head())
    assert caught.value.kind is ErrorKind.UNAVAILABLE


def test_the_command_line_migrates_the_database_its_settings_name(
    monkeypatch: pytest.MonkeyPatch, fresh_database_url: str
) -> None:
    monkeypatch.setenv("LEDGER_DATABASE_URL", fresh_database_url)
    monkeypatch.setenv("LEDGER_KMS_PROVIDER", "local")
    monkeypatch.setenv("LEDGER_LOCAL_KMS_KEY", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
    assert migrate.main() == 0
    assert tables_of(fresh_database_url) >= VAULT_TABLES
```

(Both blocks are the one file `test_migrations.py`.)

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/vault/tests/test_settings.py services/vault/tests/test_migrations.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_vault.settings'`.

- [ ] **Step 4: Implement**

`services/vault/src/ledger_vault/settings.py`:

```python
"""vault's settings: the chassis's, its KMS, its trust store, its limits, from ``LEDGER_*`` variables.

The KMS is always named (``LEDGER_KMS_PROVIDER``): ``aws`` with a key and a region (an endpoint for LocalStack),
or ``local`` with a 32-byte key in base64 for a laptop, which production refuses (Decision 2 of the owner).
Beyond a laptop the trust store is required and the KMS is reached over HTTPS. AWS credentials are boto3's
business (``AWS_*`` variables, a web identity on Kubernetes), never settings of vault's.
"""

import base64
import binascii
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import Field, SecretStr, model_validator

from ledger_platform.settings import ServiceSettings

KEY_BYTES: Final = 32
_BEYOND_A_LAPTOP: Final = frozenset({"staging", "production"})


class KmsProvider(StrEnum):
    """Which key management service wraps vault's data keys."""

    AWS = "aws"
    LOCAL = "local"


class KmsProblem(StrEnum):
    """Why a KMS configuration cannot hold keys safely, naming settings, never their values."""

    AWS_INCOMPLETE = "the aws KMS needs kms_key_id and kms_region"
    LOCAL_IN_PRODUCTION = "the local KMS is never in production"
    LOCAL_KEY = "the local KMS needs local_kms_key: 32 bytes in base64"
    NO_TRUST_STORE = "trusted_cas_path is required in staging and production"
    ENDPOINT_IN_CLEAR = "kms_endpoint_url must be https in staging and production"


class _KmsSettingsError(ValueError):
    """A KMS configuration that cannot hold keys safely."""

    def __init__(self, problem: KmsProblem) -> None:
        super().__init__(problem.value)


def _decoded(key: SecretStr) -> bytes:
    try:
        return base64.b64decode(key.get_secret_value(), validate=True)
    except (binascii.Error, ValueError):
        return b""


class VaultSettings(ServiceSettings):
    """vault's settings (see the module)."""

    service_name: str = Field(default="vault", pattern=r"^[a-z][a-z0-9-]{1,30}$")
    kms_provider: KmsProvider = Field(default=...)
    kms_key_id: str | None = Field(default=None, min_length=1, max_length=2048)
    """An ARN, a key id or an alias (``alias/ledger-vault``)."""
    kms_region: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[a-z]+)+-[0-9]$")
    kms_endpoint_url: str | None = Field(default=None, max_length=2048)
    """Another endpoint than AWS's own: LocalStack's, locally."""
    local_kms_key: SecretStr | None = None
    """The local KMS's key-encryption key: 32 bytes, base64."""
    trusted_cas_path: Path | None = None
    """A PEM file of the roots of the authorities vault accepts (none is built in)."""
    accept_representatives: bool = True
    """Whether a natural person's certificate may sign for a taxpayer other than that person (Decision 1)."""
    max_bundle_bytes: Annotated[int, Field(ge=1024, le=1024 * 1024)] = 64 * 1024
    max_document_bytes: Annotated[int, Field(ge=1024, le=3 * 1024 * 1024)] = 1024 * 1024
    """At most 3 MiB: a signed document must still fit gRPC's 4 MiB message."""
    signing_threads: Annotated[int, Field(ge=1, le=64)] = 4
    data_key_cache_seconds: Annotated[int, Field(ge=0, le=300)] = 0
    """0: off (Decision 10)."""
    expiry_check_seconds: Annotated[int, Field(ge=60, le=86_400)] = 3_600

    @model_validator(mode="after")
    def require_a_safe_kms(self) -> Self:
        """Refuse a KMS configuration that cannot work, or that holds keys unsafely where it matters."""
        beyond = self.environment in _BEYOND_A_LAPTOP
        if self.kms_provider is KmsProvider.AWS and (self.kms_key_id is None or self.kms_region is None):
            raise _KmsSettingsError(KmsProblem.AWS_INCOMPLETE)
        if self.kms_provider is KmsProvider.LOCAL:
            if self.environment == "production":
                raise _KmsSettingsError(KmsProblem.LOCAL_IN_PRODUCTION)
            if self.local_kms_key is None or len(_decoded(self.local_kms_key)) != KEY_BYTES:
                raise _KmsSettingsError(KmsProblem.LOCAL_KEY)
        if beyond and self.trusted_cas_path is None:
            raise _KmsSettingsError(KmsProblem.NO_TRUST_STORE)
        if beyond and self.kms_endpoint_url is not None and not self.kms_endpoint_url.startswith("https://"):
            raise _KmsSettingsError(KmsProblem.ENDPOINT_IN_CLEAR)
        return self

    def local_key(self) -> bytes:
        """The local KMS's key; empty when there is none (``aws``)."""
        return b"" if self.local_kms_key is None else _decoded(self.local_kms_key)
```


`services/vault/src/ledger_vault/adapters/postgres/__init__.py`:

```python
"""vault's state in PostgreSQL: tables, repositories, units of work and migrations (SQLAlchemy Core, Alembic)."""
```

`services/vault/src/ledger_vault/adapters/postgres/tables.py`:

```python
"""vault's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

``certificates`` and ``signatures`` are a company's data, under ``tenant_isolation``; a signature refers to its
certificate through ``(company_id, certificate_id)``, so it cannot name another company's (PostgreSQL's
referential checks bypass row-level security). ``expiry_watch`` is outside row-level security on purpose
(Decision 8): ids, a generation and a date, read across companies by the expiry announcements.

The migration adds what models do not hold: the trigger that keeps ``signatures`` append-only and the one that
keeps every certificate (``vault_refuse_change``).
"""

from datetime import datetime
from typing import ClassVar, Final
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from ledger_kernel.access import MAX_SUBJECT_LENGTH
from ledger_vault.domain.signing import MAX_REFERENCE

WORD: Final = 32
NAME: Final = 256
FINGERPRINT: Final = 64
IDENTIFIER: Final = 32
TAX_ID: Final = 11
KEY_ID: Final = 2048
CALLER: Final = 32


class VaultTable(DeclarativeBase):
    """vault's own tables; the chassis's are ``PlatformTable``'s."""

    metadata: ClassVar[MetaData] = MetaData()


class CertificateRecord(VaultTable):
    """A certificate of a company: what it states, whose it is, its sealed key and its state."""

    __tablename__: str = "certificates"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "id", name="uq_certificates_company"),
        UniqueConstraint("company_id", "generation", name="uq_certificates_generation"),
        Index("uq_certificates_active", "company_id", unique=True, postgresql_where=text("status = 'active'")),
        CheckConstraint("status IN ('active', 'retired')", name="ck_certificates_status"),
        CheckConstraint("holder_kind IN ('person', 'organization')", name="ck_certificates_holder_kind"),
        CheckConstraint("holder_binding IN ('taxpayer', 'representative')", name="ck_certificates_binding"),
        CheckConstraint(
            "(status = 'active' AND retired_at IS NULL AND retirement IS NULL)"
            " OR (status = 'retired' AND retired_at IS NOT NULL AND retirement IS NOT NULL)",
            name="ck_certificates_retired",
        ),
        CheckConstraint("generation > 0", name="ck_certificates_generation"),
        CheckConstraint("octet_length(nonce) = 12", name="ck_certificates_nonce"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    generation: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(WORD))
    holder_kind: Mapped[str] = mapped_column(String(WORD))
    holder_name: Mapped[str] = mapped_column(String(NAME))
    holder_id: Mapped[str] = mapped_column(String(IDENTIFIER))
    holder_tax_id: Mapped[str | None] = mapped_column(String(TAX_ID))
    holder_binding: Mapped[str] = mapped_column(String(WORD))
    certificate: Mapped[bytes] = mapped_column(LargeBinary)
    issuer_name: Mapped[str] = mapped_column(String(NAME))
    serial_number: Mapped[str] = mapped_column(String(FINGERPRINT))
    fingerprint: Mapped[str] = mapped_column(String(FINGERPRINT))
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    key_bits: Mapped[int] = mapped_column(Integer)
    kms_provider: Mapped[str] = mapped_column(String(WORD))
    kms_key_id: Mapped[str] = mapped_column(String(KEY_ID))
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    sealed_key: Mapped[bytes] = mapped_column(LargeBinary)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    uploaded_by: Mapped[str] = mapped_column(String(MAX_SUBJECT_LENGTH))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retirement: Mapped[str | None] = mapped_column(String(WORD))


class SignatureRow(VaultTable):
    """The audit of one signature: never updated, never deleted (a trigger refuses both)."""

    __tablename__: str = "signatures"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            ["company_id", "certificate_id"],
            ["certificates.company_id", "certificates.id"],
            name="fk_signatures_certificate",
        ),
        CheckConstraint("purpose IN ('document', 'seed')", name="ck_signatures_purpose"),
        CheckConstraint("octet_length(document_sha256) = 32", name="ck_signatures_digest"),
        Index("ix_signatures_signed_at", "company_id", "signed_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    certificate_id: Mapped[UUID] = mapped_column()
    purpose: Mapped[str] = mapped_column(String(WORD))
    document_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    reference: Mapped[str | None] = mapped_column(String(MAX_REFERENCE))
    caller: Mapped[str | None] = mapped_column(String(CALLER))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExpiryWatchRow(VaultTable):
    """An active certificate's expiry, watched across companies (Decision 8)."""

    __tablename__: str = "expiry_watch"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            ["company_id", "certificate_id"],
            ["certificates.company_id", "certificates.id"],
            name="fk_expiry_watch_certificate",
        ),
        CheckConstraint("announced IS NULL OR announced IN (30, 7, 1)", name="ck_expiry_watch_announced"),
        Index("ix_expiry_watch_due", "not_after"),
    )

    certificate_id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    generation: Mapped[int] = mapped_column(BigInteger)
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    announced: Mapped[int | None] = mapped_column(SmallInteger)
```

`services/vault/src/ledger_vault/adapters/postgres/migrate.py`:

```python
"""vault's schema brought to this version's: ``ledger_platform.migrations`` with vault's revisions."""

from typing import Final

from ledger_platform.migrations import BASE, HEAD, Migrations
from ledger_vault.settings import VaultSettings
lazy from ledger_platform.database import Database

SCRIPTS: Final = "ledger_vault.adapters.postgres:migrations"
MIGRATIONS: Final = Migrations(SCRIPTS, owner="vault")


def upgrade(url: str, revision: str = HEAD) -> None:
    """Bring vault's database at ``url`` up to ``revision``."""
    MIGRATIONS.upgrade(url, revision)


def downgrade(url: str, revision: str = BASE) -> None:
    """Take vault's database at ``url`` down to ``revision``."""
    MIGRATIONS.downgrade(url, revision)


def head() -> str:
    """The revision vault's migrations end at."""
    return MIGRATIONS.head()


async def require_head(database: Database, expected: str) -> None:
    """vault's readiness check: the database is at ``expected``."""
    await MIGRATIONS.require_head(database, expected)


def main() -> int:
    """``ledger-vault-migrate``: bring the database of ``LEDGER_DATABASE_URL`` to this version's schema."""
    upgrade(VaultSettings().database_url.get_secret_value())
    return 0
```

`services/vault/src/ledger_vault/adapters/postgres/migrations/__init__.py`:

```python
"""vault's Alembic environment and migration scripts."""
```

`services/vault/src/ledger_vault/adapters/postgres/migrations/env.py`:

```python
"""Alembic's entry point for vault: the platform's runner with vault's tables."""

from ledger_platform.migrations import run_migrations
from ledger_vault.adapters.postgres.tables import VaultTable

run_migrations(VaultTable.metadata)
```

`services/vault/src/ledger_vault/adapters/postgres/migrations/versions/__init__.py`:

```python
"""vault's migration scripts, one per revision."""
```

`services/vault/src/ledger_vault/adapters/postgres/migrations/versions/r0001_vault.py`:

```python
"""Revision 0001, from nothing: vault's first schema.

The chassis's tables (as ``ledger_platform`` declares them at this revision), then certificates, signatures
and the expiry watch; a company's data under row-level security; the triggers that keep the audit append-only
and every certificate kept.
"""

from typing import Final
lazy from collections.abc import Sequence
lazy from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from ledger_platform.database import tenant_isolation
from ledger_platform.schema import CHASSIS_POLICIES

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEWEST_FIRST: Final = ("idempotency_keys", "inbox", "outbox", "expiry_watch", "signatures", "certificates")
_REFUSE_CHANGE: Final = """
CREATE FUNCTION vault_refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege', MESSAGE = TG_TABLE_NAME || ' keeps every row';
END
$$
"""
_TRIGGERS: Final = (
    "CREATE TRIGGER signatures_append_only BEFORE UPDATE OR DELETE ON signatures"
    " FOR EACH ROW EXECUTE FUNCTION vault_refuse_change()",
    "CREATE TRIGGER certificates_kept BEFORE DELETE ON certificates FOR EACH ROW EXECUTE FUNCTION vault_refuse_change()",
)


def _apply(statements: Sequence[str]) -> None:
    for statement in statements:
        op.execute(statement)


def _moment(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _chassis() -> None:
    """The platform's tables and their row-level security, spelled out as Milestone 2's first revision does."""
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("headers", JSONB(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        _moment("created_at"),
        sa.Column("stored_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_outbox_due", "outbox", [sa.text("(attempts > 0)"), "created_at", "id", "next_attempt_at"])
    op.create_table(
        "inbox",
        sa.Column("consumer", sa.String(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        _moment("processed_at"),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("organization_id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("headers", JSONB(), nullable=True),
        sa.Column("body", sa.LargeBinary(), nullable=True),
        _moment("expires_at"),
    )
    op.create_index("ix_idempotency_keys_expiry", "idempotency_keys", ["organization_id", "expires_at"])
    _apply(CHASSIS_POLICIES)


def upgrade() -> None:
    """Create the chassis's tables and vault's, under row-level security, with the audit's triggers."""
    _chassis()
    certificates = op.create_table(
        "certificates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("holder_kind", sa.String(32), nullable=False),
        sa.Column("holder_name", sa.String(256), nullable=False),
        sa.Column("holder_id", sa.String(32), nullable=False),
        sa.Column("holder_tax_id", sa.String(11), nullable=True),
        sa.Column("holder_binding", sa.String(32), nullable=False),
        sa.Column("certificate", sa.LargeBinary(), nullable=False),
        sa.Column("issuer_name", sa.String(256), nullable=False),
        sa.Column("serial_number", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        _moment("not_before"),
        _moment("not_after"),
        sa.Column("key_bits", sa.Integer(), nullable=False),
        sa.Column("kms_provider", sa.String(32), nullable=False),
        sa.Column("kms_key_id", sa.String(2048), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("sealed_key", sa.LargeBinary(), nullable=False),
        _moment("uploaded_at"),
        sa.Column("uploaded_by", sa.String(128), nullable=False),
        _moment("retired_at", nullable=True),
        sa.Column("retirement", sa.String(32), nullable=True),
        sa.UniqueConstraint("company_id", "id", name="uq_certificates_company"),
        sa.UniqueConstraint("company_id", "generation", name="uq_certificates_generation"),
        sa.CheckConstraint("status IN ('active', 'retired')", name="ck_certificates_status"),
        sa.CheckConstraint("holder_kind IN ('person', 'organization')", name="ck_certificates_holder_kind"),
        sa.CheckConstraint("holder_binding IN ('taxpayer', 'representative')", name="ck_certificates_binding"),
        sa.CheckConstraint(
            "(status = 'active' AND retired_at IS NULL AND retirement IS NULL)"
            " OR (status = 'retired' AND retired_at IS NOT NULL AND retirement IS NOT NULL)",
            name="ck_certificates_retired",
        ),
        sa.CheckConstraint("generation > 0", name="ck_certificates_generation"),
        sa.CheckConstraint("octet_length(nonce) = 12", name="ck_certificates_nonce"),
    )
    op.create_index(
        "uq_certificates_active", "certificates", ["company_id"], unique=True, postgresql_where=sa.text("status = 'active'")
    )
    signatures = op.create_table(
        "signatures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("certificate_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("document_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("reference", sa.String(64), nullable=True),
        sa.Column("caller", sa.String(32), nullable=True),
        _moment("signed_at"),
        sa.ForeignKeyConstraint(
            ["company_id", "certificate_id"],
            ["certificates.company_id", "certificates.id"],
            name="fk_signatures_certificate",
        ),
        sa.CheckConstraint("purpose IN ('document', 'seed')", name="ck_signatures_purpose"),
        sa.CheckConstraint("octet_length(document_sha256) = 32", name="ck_signatures_digest"),
    )
    op.create_index("ix_signatures_signed_at", "signatures", ["company_id", "signed_at"])
    op.create_table(
        "expiry_watch",
        sa.Column("certificate_id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        _moment("not_after"),
        sa.Column("announced", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id", "certificate_id"],
            ["certificates.company_id", "certificates.id"],
            name="fk_expiry_watch_certificate",
        ),
        sa.CheckConstraint("announced IS NULL OR announced IN (30, 7, 1)", name="ck_expiry_watch_announced"),
    )
    op.create_index("ix_expiry_watch_due", "expiry_watch", ["not_after"])
    _apply(tenant_isolation(certificates))
    _apply(tenant_isolation(signatures))
    _apply((_REFUSE_CHANGE, *_TRIGGERS))


def downgrade() -> None:
    """Drop every table this revision created (their policies and triggers go with them), then the function."""
    for table in _NEWEST_FIRST:
        op.drop_table(table)
    op.execute("DROP FUNCTION vault_refuse_change()")
```

(The chassis DDL above must equal what Milestone 2's `r0001_identity._chassis` spells at the time this runs: compare the two and take identity's where they differ — `test_the_migrated_schema_is_exactly_the_models` fails on any difference with `CHASSIS_METADATA`. If Milestone 1's fix wave added an index on `outbox.stored_at` (its item 8), it is in identity's revision too.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/vault/tests/test_settings.py services/vault/tests/test_migrations.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/vault
git commit -m "feat(vault): settings that refuse an unsafe KMS, schema with an append-only audit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Vault's state in PostgreSQL: repositories, units, the outbox events

**Files:**
- Create: `services/vault/src/ledger_vault/adapters/postgres/repositories.py`, `adapters/postgres/units.py`, `adapters/events.py`
- Modify: `services/vault/pyproject.toml`, `services/vault/tests/conftest.py`
- Test: `services/vault/tests/test_repositories.py`

**Interfaces:**
- Consumes: the ports (Task 5), the records (Task 9), `Database.transaction(scope)`, `UnitOfWork.connection`, `UnitOfWork.scope` (M2 Task 4), `Outbox.add(unit, envelope, *, subject)`, `Envelope.wrap(message, *, source, tenant, clock)`, `StreamSpec`, the wire events (Task 1), the `Clock` alias (`ledger_kernel.clock`, as the fix wave names it).
- Produces: `ledger_vault.adapters.postgres.repositories`: `PgCertificates(connection, tenant)`, `PgSignatures(connection, tenant)`, `PgExpiryWatch(connection, tenant)`, `PgExpiryCandidates(database)`. `ledger_vault.adapters.postgres.units`: `bind(unit, outbox, *, clock=utc_now) -> VaultUnit` (`UnscopedUnitError` unless the unit's scope is a `Tenant`), `PgUnits(database, outbox, *, clock=utc_now)`. `ledger_vault.adapters.events`: `VAULT = StreamSpec(name="VAULT", subjects=("vault.>",))`, `SOURCE = "vault"`, `outgoing(event) -> tuple[Message, str]` (`vault.certificate.uploaded|rotated|retired|expiring`), `OutboxEvents(unit, outbox, *, clock)`. Fixture `outbox`.

Re-check before starting: `UnitOfWork.scope` and `Scope` (M2 Task 4), the fix wave's `Clock` alias, `Outbox`'s constructor as identity's conftest builds it (M2 Task 12).

- [ ] **Step 1: Dependencies**

`services/vault/pyproject.toml` — add `"ledger-contracts"` and `"protobuf>=7.36.2"` to `dependencies`, `ledger-contracts = { workspace = true }` to `[tool.uv.sources]`, and:

```toml
[tool.deptry.package_module_name_map]
"ledger-contracts" = ["ledger_contracts", "ledger"]
```

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

Append to `services/vault/tests/conftest.py`:

```python
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry

from ledger_platform.outbox import Outbox
from ledger_platform.tracing import Tracing


@pytest.fixture
def outbox() -> Outbox:
    """An outbox of the test's own: its own tracer provider and metrics registry."""
    return Outbox(tracing=Tracing(TracerProvider()), registry=CollectorRegistry())
```

`services/vault/tests/test_repositories.py`:

```python
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from ledger.events.vault.v1.events_pb2 import CertificateRotated as WireRotated
from ledger_contracts.envelope import Envelope
from ledger_kernel.access import SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.outbox import OutboxRecord
from ledger_vault.adapters.events import SOURCE, VAULT, outgoing
from ledger_vault.adapters.postgres.repositories import PgExpiryCandidates
from ledger_vault.adapters.postgres.tables import CertificateRecord, SignatureRow
from ledger_vault.adapters.postgres.units import PgUnits, UnscopedUnitError, bind
from ledger_vault.application.commands import CertificateUpload, VaultCommands, retire_company
from ledger_vault.domain.certificate import CertificateFacts, CertificateStatus, CustodyPolicy
from ledger_vault.domain.events import CertificateRotated
from ledger_vault.domain.holder import COMMON_NAME, SERIAL_NUMBER
from ledger_vault.domain.ids import CertificateId
from ledger_vault.domain.signing import SigningPurpose
lazy from ledger_platform.database import Database
lazy from ledger_platform.outbox import Outbox

from fakes import FakeCustody, FakeReader, FakeSigner

NOW: Final = datetime.now(UTC)
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
OTHER: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
RNC: Final = TaxId.parse("101850043")


def commands(database: Database, outbox: Outbox, reader: FakeReader | None = None) -> VaultCommands:
    return VaultCommands(
        units=PgUnits(database, outbox),
        reader=FakeReader() if reader is None else reader,
        custody=FakeCustody(),
        signer=FakeSigner(),
        policy=CustodyPolicy(),
        clock=lambda: NOW,
    )


def upload(tenant: Tenant = TENANT, bundle: bytes = b"bundle-1") -> CertificateUpload:
    return CertificateUpload(tenant=tenant, taxpayer=RNC, requested_by=SubjectId("2001"), bundle=bundle, password="p")


async def test_a_certificate_reads_back_as_it_was_installed(database: Database, outbox: Outbox) -> None:
    vault = commands(database, outbox)
    installed = await vault.upload(upload())
    async with PgUnits(database, outbox).unit(TENANT) as unit:
        found = await unit.certificates.get(installed.certificate.id)
        active = await unit.certificates.active()
    assert found == installed.certificate == active


async def test_a_company_sees_none_of_another_s_certificates(database: Database, outbox: Outbox) -> None:
    vault = commands(database, outbox)
    installed = await vault.upload(upload())
    async with PgUnits(database, outbox).unit(OTHER) as unit:
        assert await unit.certificates.get(installed.certificate.id) is None
        assert await unit.certificates.active() is None
        assert await unit.certificates.newest(10) == ()
        assert await unit.certificates.last_generation() == 0


async def test_two_uploads_at_once_install_one_certificate(database: Database, outbox: Outbox) -> None:
    vault = commands(database, outbox)
    results = await asyncio.gather(
        vault.upload(upload(bundle=b"one")), vault.upload(upload(bundle=b"two")), return_exceptions=True
    )
    assert sorted(type(result).__name__ for result in results) == ["CertificateAlreadyActiveError", "Installation"]
    async with database.transaction(TENANT) as unit:
        count = (await unit.connection.execute(select(func.count()).select_from(CertificateRecord))).scalar_one()
    assert count == 1


async def test_a_rotation_is_announced_through_the_outbox(database: Database, outbox: Outbox) -> None:
    vault = commands(database, outbox)
    first = await vault.upload(upload())
    rotated = await vault.rotate(upload(bundle=b"bundle-2"), replaces=1)
    async with database.transaction(None) as unit:
        rows = (await unit.connection.execute(select(OutboxRecord.subject, OutboxRecord.headers, OutboxRecord.data))).all()
    subjects = [subject for subject, _, _ in rows]
    assert sorted(subjects) == ["vault.certificate.rotated", "vault.certificate.uploaded"]
    _, headers, data = next(row for row in rows if row[0] == "vault.certificate.rotated")
    wire = Envelope.from_headers(headers, data).unwrap(WireRotated)
    assert (wire.certificate_id, wire.retired_certificate_id, wire.generation) == (
        str(rotated.certificate.id),
        str(first.certificate.id),
        2,
    )
    assert VAULT.subjects == ("vault.>",)


async def test_signatures_are_recorded_and_the_audit_is_append_only_in_postgresql_itself(
    database: Database, outbox: Outbox
) -> None:
    vault = commands(database, outbox)
    await vault.upload(upload())
    await vault.sign(TENANT, b"<ECF/>", SigningPurpose.DOCUMENT, reference="E310000000001", caller="fiscal-do")
    async with database.transaction(TENANT) as unit:
        row = (await unit.connection.execute(select(SignatureRow.reference, SignatureRow.caller))).one()
    assert tuple(row) == ("E310000000001", "fiscal-do")
    for statement in ("UPDATE signatures SET reference = NULL", "DELETE FROM signatures", "DELETE FROM certificates"):
        with pytest.raises(DBAPIError, match="InsufficientPrivilege"):
            async with database.transaction(TENANT) as unit:
                await unit.connection.execute(text(statement))


async def test_a_retired_company_keeps_its_certificate_and_stops_being_watched(
    database: Database, outbox: Outbox
) -> None:
    vault = commands(database, outbox)
    installed = await vault.upload(upload())
    async with PgUnits(database, outbox).unit(TENANT) as unit:
        assert await retire_company(unit) == installed.certificate.id
        kept = await unit.certificates.get(installed.certificate.id)
    assert kept is not None
    assert kept.status is CertificateStatus.RETIRED
    assert await PgExpiryCandidates(database).due(NOW + timedelta(days=400), 10) == ()


async def test_expiry_is_read_across_companies_and_claimed_once(database: Database, outbox: Outbox) -> None:
    reader = FakeReader()
    reader.facts = CertificateFacts(
        der=b"der",
        subject=((COMMON_NAME, "JUAN PEREZ"), (SERIAL_NUMBER, "IDCDO-00112345678")),
        issuer_name="CA",
        serial_number="1",
        fingerprint="f" * 64,
        not_before=NOW - timedelta(days=1),
        not_after=NOW + timedelta(days=5),
        key_algorithm="rsa",
        key_bits=2048,
        signs=True,
        trusted=True,
    )
    vault = commands(database, outbox, reader)
    await vault.upload(upload())
    await vault.upload(upload(OTHER))
    candidates = PgExpiryCandidates(database)
    assert len(await candidates.due(NOW + timedelta(days=30), 10)) == 2
    assert await vault.announce_expiring(candidates) == 2
    assert await vault.announce_expiring(candidates) == 0


async def test_a_unit_needs_a_company(database: Database, outbox: Outbox) -> None:
    async with database.transaction(None) as unit:
        with pytest.raises(UnscopedUnitError):
            bind(unit, outbox)


def test_every_event_has_its_subject() -> None:
    rotated = CertificateRotated(
        tenant=TENANT, certificate=CertificateId(new_id()), generation=2, not_after=NOW, retired=CertificateId(new_id())
    )
    message, subject = outgoing(rotated)
    assert subject == "vault.certificate.rotated"
    assert Envelope.wrap(message, source=SOURCE, tenant=TENANT).type == "ledger.events.vault.v1.CertificateRotated"
```

(The fakes this test shares with `test_application.py` (`FakeReader`, `FakeCustody`, `FakeSigner`) move in this task into `services/vault/tests/fakes.py`, a plain module both import; if pytest's importlib mode does not find it, add `"services/vault/tests"` to `[tool.pytest] pythonpath`, never copy them.)

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/vault/tests/test_repositories.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_vault.adapters.events'`.

- [ ] **Step 4: Implement**

`services/vault/src/ledger_vault/adapters/postgres/repositories.py`:

```python
"""vault's repositories: the ports of ``application.ports`` over one transaction (SQLAlchemy Core).

Every repository is bound to the unit's company and names its organization and company in every statement;
row-level security enforces the same scope again (§2). ``PgExpiryCandidates`` alone reads across companies, in a
transaction without scope, the ``expiry_watch`` table that row-level security does not cover (Decision 8).
"""

from typing import final, override
lazy from collections.abc import Sequence
lazy from datetime import datetime
lazy from uuid import UUID

from sqlalchemy import and_, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import ColumnElement, Row
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.access import SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant
from ledger_vault.adapters.postgres.tables import CertificateRecord, ExpiryWatchRow, SignatureRow
from ledger_vault.application.ports import Certificates, ExpiryCandidates, ExpiryWatch, Signatures, Watched
from ledger_vault.domain.certificate import Certificate, CertificateStatus, Retirement, SealedKey
from ledger_vault.domain.holder import Holder, HolderBinding, HolderKind
from ledger_vault.domain.ids import CertificateId
lazy from ledger_platform.database import Database
lazy from ledger_vault.domain.signing import SignatureRecord

_CERTIFICATE = select(
    CertificateRecord.id,
    CertificateRecord.generation,
    CertificateRecord.status,
    CertificateRecord.holder_kind,
    CertificateRecord.holder_name,
    CertificateRecord.holder_id,
    CertificateRecord.holder_tax_id,
    CertificateRecord.holder_binding,
    CertificateRecord.certificate,
    CertificateRecord.issuer_name,
    CertificateRecord.serial_number,
    CertificateRecord.fingerprint,
    CertificateRecord.not_before,
    CertificateRecord.not_after,
    CertificateRecord.key_bits,
    CertificateRecord.kms_provider,
    CertificateRecord.kms_key_id,
    CertificateRecord.wrapped_key,
    CertificateRecord.nonce,
    CertificateRecord.sealed_key,
    CertificateRecord.uploaded_at,
    CertificateRecord.uploaded_by,
    CertificateRecord.retired_at,
    CertificateRecord.retirement,
)
type _CertificateRow = Row[
    UUID, int, str, str, str, str, str | None, str, bytes, str, str, str, datetime, datetime, int,
    str, str, bytes, bytes, bytes, datetime, str, datetime | None, str | None,
]
"""A row of ``_CERTIFICATE``, its columns in order."""


def _certificate(tenant: Tenant, row: _CertificateRow) -> Certificate:
    (
        identifier, generation, status, holder_kind, holder_name, holder_id, holder_tax_id, binding, der,
        issuer_name, serial_number, fingerprint, not_before, not_after, key_bits, provider, key_id, wrapped,
        nonce, sealed, uploaded_at, uploaded_by, retired_at, retirement,
    ) = row
    return Certificate(
        id=CertificateId(identifier),
        tenant=tenant,
        generation=generation,
        status=CertificateStatus(status),
        holder=Holder(
            kind=HolderKind(holder_kind),
            name=holder_name,
            identifier=holder_id,
            tax_id=None if holder_tax_id is None else TaxId.parse(holder_tax_id),
        ),
        binding=HolderBinding(binding),
        der=der,
        issuer_name=issuer_name,
        serial_number=serial_number,
        fingerprint=fingerprint,
        not_before=not_before,
        not_after=not_after,
        key_bits=key_bits,
        sealed=SealedKey(provider=provider, key_id=key_id, wrapped_key=wrapped, nonce=nonce, ciphertext=sealed),
        uploaded_at=uploaded_at,
        uploaded_by=SubjectId(uploaded_by),
        retired_at=retired_at,
        retirement=None if retirement is None else Retirement(retirement),
    )


@final
class PgCertificates(Certificates):
    """The certificates of the unit's company, in PostgreSQL."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    def _of_company(self) -> ColumnElement[bool]:
        return and_(
            CertificateRecord.organization_id == self._tenant.organization,
            CertificateRecord.company_id == self._tenant.company,
        )

    async def _one(self, condition: ColumnElement[bool]) -> Certificate | None:
        row = (await self._connection.execute(_CERTIFICATE.where(self._of_company(), condition))).first()
        return None if row is None else _certificate(self._tenant, row)

    @override
    async def lock(self) -> None:
        key = func.hashtextextended(literal(str(self._tenant.company)), 0)
        await self._connection.execute(select(func.pg_advisory_xact_lock(key)))

    @override
    async def active(self) -> Certificate | None:
        return await self._one(CertificateRecord.status == CertificateStatus.ACTIVE.value)

    @override
    async def last_generation(self) -> int:
        statement = select(func.coalesce(func.max(CertificateRecord.generation), 0)).where(self._of_company())
        return (await self._connection.execute(statement)).scalar_one()

    @override
    async def get(self, certificate: CertificateId) -> Certificate | None:
        return await self._one(CertificateRecord.id == certificate)

    @override
    async def newest(self, limit: int) -> tuple[Certificate, ...]:
        newest = CertificateRecord.generation.desc()
        statement = _CERTIFICATE.where(self._of_company()).order_by(newest).limit(limit)
        return tuple(_certificate(self._tenant, row) for row in await self._connection.execute(statement))

    @override
    async def add(self, certificate: Certificate) -> None:
        holder, sealed = certificate.holder, certificate.sealed
        await self._connection.execute(
            insert(CertificateRecord).values(
                id=certificate.id,
                organization_id=certificate.tenant.organization,
                company_id=certificate.tenant.company,
                generation=certificate.generation,
                status=certificate.status.value,
                holder_kind=holder.kind.value,
                holder_name=holder.name,
                holder_id=holder.identifier,
                holder_tax_id=None if holder.tax_id is None else str(holder.tax_id),
                holder_binding=certificate.binding.value,
                certificate=certificate.der,
                issuer_name=certificate.issuer_name,
                serial_number=certificate.serial_number,
                fingerprint=certificate.fingerprint,
                not_before=certificate.not_before,
                not_after=certificate.not_after,
                key_bits=certificate.key_bits,
                kms_provider=sealed.provider,
                kms_key_id=sealed.key_id,
                wrapped_key=sealed.wrapped_key,
                nonce=sealed.nonce,
                sealed_key=sealed.ciphertext,
                uploaded_at=certificate.uploaded_at,
                uploaded_by=certificate.uploaded_by,
            )
        )

    @override
    async def retired(self, certificate: Certificate) -> None:
        await self._connection.execute(
            update(CertificateRecord)
            .where(self._of_company(), CertificateRecord.id == certificate.id)
            .values(
                status=certificate.status.value,
                retired_at=certificate.retired_at,
                retirement=None if certificate.retirement is None else certificate.retirement.value,
            )
        )


@final
class PgSignatures(Signatures):
    """The audit of the unit's company's signatures, in PostgreSQL (appended to only)."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    @override
    async def add(self, record: SignatureRecord) -> None:
        await self._connection.execute(
            insert(SignatureRow).values(
                id=record.id,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                certificate_id=record.certificate,
                purpose=record.purpose.value,
                document_sha256=record.document_sha256,
                reference=record.reference,
                caller=record.caller,
                signed_at=record.signed_at,
            )
        )


@final
class PgExpiryWatch(ExpiryWatch):
    """The unit's company's watched expiries, in PostgreSQL."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    @override
    async def watch(self, certificate: Certificate) -> None:
        await self._connection.execute(
            insert(ExpiryWatchRow).values(
                certificate_id=certificate.id,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                generation=certificate.generation,
                not_after=certificate.not_after,
                announced=None,
            )
        )

    @override
    async def forget(self, certificate: CertificateId) -> None:
        await self._connection.execute(
            delete(ExpiryWatchRow).where(
                ExpiryWatchRow.company_id == self._tenant.company, ExpiryWatchRow.certificate_id == certificate
            )
        )

    @override
    async def claim(self, certificate: CertificateId, days: int) -> bool:
        claimed = await self._connection.execute(
            update(ExpiryWatchRow)
            .where(
                ExpiryWatchRow.company_id == self._tenant.company,
                ExpiryWatchRow.certificate_id == certificate,
                or_(ExpiryWatchRow.announced.is_(None), ExpiryWatchRow.announced > days),
            )
            .values(announced=days)
            .returning(ExpiryWatchRow.certificate_id)
        )
        return claimed.first() is not None


@final
class PgExpiryCandidates(ExpiryCandidates):
    """Every company's watched expiries, read without a scope (``expiry_watch`` is outside row-level security)."""

    __slots__ = ("_database",)

    def __init__(self, database: Database) -> None:
        self._database = database

    @override
    async def due(self, until: datetime, limit: int) -> Sequence[Watched]:
        statement = (
            select(
                ExpiryWatchRow.certificate_id,
                ExpiryWatchRow.organization_id,
                ExpiryWatchRow.company_id,
                ExpiryWatchRow.generation,
                ExpiryWatchRow.not_after,
                ExpiryWatchRow.announced,
            )
            .where(ExpiryWatchRow.not_after <= until)
            .order_by(ExpiryWatchRow.not_after)
            .limit(limit)
        )
        async with self._database.transaction(None) as unit:
            rows = (await unit.connection.execute(statement)).all()
        return tuple(_watched(*row) for row in rows)


def _watched(
    certificate: UUID, organization: UUID, company: UUID, generation: int, not_after: datetime, announced: int | None
) -> Watched:
    return Watched(
        certificate=CertificateId(certificate),
        tenant=Tenant(OrganizationId(organization), CompanyId(company)),
        generation=generation,
        not_after=not_after,
        announced=announced,
    )
```

(The row's 24 columns are unpacked into typed names, as Milestone 2's repositories unpack theirs; `ruff format` lays the tuples out one name per line.)

`services/vault/src/ledger_vault/adapters/events.py`:

```python
"""What vault announces on the wire (§4.1): protobuf messages in envelopes, through the outbox.

Stream ``VAULT`` holds every subject ``vault.certificate.<event>``; each event is scoped to its company and
organization and carries the certificate's generation.
"""

from typing import Final, final, override

from google.protobuf.timestamp_pb2 import Timestamp
lazy from google.protobuf.message import Message

from ledger.events.vault.v1 import events_pb2 as wire
from ledger_contracts.envelope import Envelope
from ledger_kernel.clock import utc_now
from ledger_platform.streams import StreamSpec
from ledger_vault.application.ports import Events
from ledger_vault.domain.events import CertificateExpiring, CertificateRetired, CertificateRotated
lazy from datetime import datetime
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.outbox import Outbox
lazy from ledger_vault.domain.events import CertificateUploaded, VaultEvent

VAULT: Final = StreamSpec(name="VAULT", subjects=("vault.>",))
"""The stream of vault's events, declared by vault at start."""
SOURCE: Final = "vault"


def timestamp(moment: datetime) -> Timestamp:
    """``moment`` on the wire."""
    stamp = Timestamp()
    stamp.FromDatetime(moment)
    return stamp


def _uploaded(event: CertificateUploaded) -> wire.CertificateUploaded:
    return wire.CertificateUploaded(
        certificate_id=str(event.certificate),
        organization_id=str(event.tenant.organization),
        company_id=str(event.tenant.company),
        generation=event.generation,
        not_after=timestamp(event.not_after),
    )


def outgoing(event: VaultEvent) -> tuple[Message, str]:
    """The payload of ``event`` and the subject it is published on."""
    company, organization = str(event.tenant.company), str(event.tenant.organization)
    if isinstance(event, CertificateRotated):
        rotated = wire.CertificateRotated(
            certificate_id=str(event.certificate),
            organization_id=organization,
            company_id=company,
            generation=event.generation,
            not_after=timestamp(event.not_after),
            retired_certificate_id=str(event.retired),
        )
        return rotated, "vault.certificate.rotated"
    if isinstance(event, CertificateRetired):
        retired = wire.CertificateRetired(
            certificate_id=str(event.certificate),
            organization_id=organization,
            company_id=company,
            generation=event.generation,
        )
        return retired, "vault.certificate.retired"
    if isinstance(event, CertificateExpiring):
        expiring = wire.CertificateExpiring(
            certificate_id=str(event.certificate),
            organization_id=organization,
            company_id=company,
            generation=event.generation,
            not_after=timestamp(event.not_after),
            days_left=event.days_left,
        )
        return expiring, "vault.certificate.expiring"
    return _uploaded(event), "vault.certificate.uploaded"


@final
class OutboxEvents(Events):
    """Announces vault's events through the outbox of the unit's transaction."""

    __slots__ = ("_clock", "_outbox", "_unit")

    def __init__(self, unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> None:
        self._unit = unit
        self._outbox = outbox
        self._clock = clock

    @override
    async def publish(self, event: VaultEvent) -> None:
        message, subject = outgoing(event)
        envelope = Envelope.wrap(message, source=SOURCE, tenant=event.tenant, clock=self._clock)
        await self._outbox.add(self._unit, envelope, subject=subject)
```

`services/vault/src/ledger_vault/adapters/postgres/units.py`:

```python
"""vault's unit of work over one chassis transaction scoped to a company: its repositories and its outbox."""

from contextlib import asynccontextmanager
from typing import ClassVar, final, override
lazy from collections.abc import AsyncGenerator

from ledger_kernel.clock import utc_now
from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import Tenant
from ledger_vault.adapters.events import OutboxEvents
from ledger_vault.adapters.postgres.repositories import PgCertificates, PgExpiryWatch, PgSignatures
from ledger_vault.application.ports import Units, VaultUnit
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.outbox import Outbox


class UnscopedUnitError(LedgerError):
    """vault's state was to be read in a transaction scoped to no company: a defect of the caller."""

    code: ClassVar[str] = "unscoped_unit"

    def __init__(self) -> None:
        super().__init__("vault's state is read and changed in a transaction scoped to one company")


def bind(unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> VaultUnit:
    """The vault view of the transaction ``unit``, which must be scoped to a company; ``clock`` is read once."""
    tenant = unit.scope
    if not isinstance(tenant, Tenant):
        raise UnscopedUnitError
    now = clock()
    connection = unit.connection
    return VaultUnit(
        tenant=tenant,
        certificates=PgCertificates(connection, tenant),
        signatures=PgSignatures(connection, tenant),
        watch=PgExpiryWatch(connection, tenant),
        events=OutboxEvents(unit, outbox, clock=lambda: now),
        now=now,
    )


@final
class PgUnits(Units):
    """Units of work over vault's database."""

    __slots__ = ("_clock", "_database", "_outbox")

    def __init__(self, database: Database, outbox: Outbox, *, clock: Clock = utc_now) -> None:
        self._database = database
        self._outbox = outbox
        self._clock = clock

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[VaultUnit]:
        async with self._database.transaction(tenant) as transaction:
            yield bind(transaction, self._outbox, clock=self._clock)
```

Root `pyproject.toml` "Vault's domain" and "use cases" forbidden lists already hold `ledger_contracts`; the adapters may import it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/vault -q --no-cov`
Expected: PASS (the concurrent upload waits on the advisory lock; the loser's error is `CertificateAlreadyActiveError`).

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (every `outgoing` branch: uploaded and rotated through the outbox test, retired through the retirement test's outbox row, expiring through the expiry test).

- [ ] **Step 7: Commit**

```bash
git add uv.lock services/vault
git commit -m "feat(vault): repositories bound to the company, an advisory lock per company, outbox events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The vault service over gRPC

**Files:**
- Create: `services/vault/src/ledger_vault/api/__init__.py`, `api/rpc.py`, `services/vault/src/ledger_vault/main.py`
- Modify: `services/vault/pyproject.toml`, `services/vault/tests/conftest.py`, `pyproject.toml` (root)
- Test: `services/vault/tests/test_rpc.py`

**Interfaces:**
- Consumes: `VaultServiceServicer`, `add_VaultServiceServicer_to_server` and the messages (Task 1); `VaultCommands`, `VaultQueries`, `CertificateUpload` (Task 5); every adapter (Tasks 6-10); `Service`, `Interfaces`, `Events`, `Runtime`, `GrpcServices`, `checks=`, `resources=` (M1 and its fix wave); `current_caller`, `Channel`, `call_unary`, `RemoteError` and kinds (Task 3, M2 Task 8); `parse_id`, `parse_subject`, `TaxId.parse`; `Authority`, `bundle`, `person`, `pem` (Task 2); `localstack_kms`, `nats_server` (Task 2, M2).
- Produces: `ledger_vault.api.rpc`: `VaultRpc(commands, queries)` (the servicer), `wire_certificate(certificate) -> Certificate`, `services(build: Callable[[Runtime[VaultSettings]], tuple[VaultCommands, VaultQueries]]) -> GrpcServices[VaultSettings]`. `ledger_vault.main`: `VERSION`, `Wiring` (what `build` assembles once the runtime exists), `build(settings, *, session=None) -> Service[VaultSettings]` (resources: the signing pool, the KMS, the custody; check `migrations`), `create_app() -> FastAPI`. Fixtures: `kms_endpoint` (session: LocalStack with the key `alias/ledger-vault-test`), `authority` (session: a test root and intermediate), `trust_store` (a PEM file of the root), `nats_url` (session), `settings_for`, `service` (vault started), `channel` (to its gRPC port), `vault` (a `VaultServiceStub`).

Re-check before starting: `Service`'s final signature for `checks=` and `resources=` (M2 Task 13 is written against the same assumption: `checks={name: async (Runtime[S]) -> None}`, `resources=(factory,)` where a factory returns an async context manager entered at start); if the fix wave's hook hands resources to routes in another way, follow it and say so in the report.

- [ ] **Step 1: Dependencies and layering**

`services/vault/pyproject.toml` — add `"grpcio>=1.84.0"` and `"granian[uvloop]>=2.8.4"` to `dependencies`, and:

```toml
[tool.deptry.per_rule_ignores]
DEP002 = ["granian"]  # the ASGI server the image runs the app with; no module imports it
```

Root `pyproject.toml`: "Vault is hexagonal" `layers = ["main", "api | adapters", "settings", "application", "domain"]`. Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

Append to `services/vault/tests/conftest.py` (imports merged at the top):

```python
from pathlib import Path
from typing import Final, cast
lazy from collections.abc import Callable

import boto3
import grpc
from pydantic import SecretStr
lazy from types_boto3_kms.client import KMSClient

from ledger.vault.v1.vault_pb2_grpc import VaultServiceStub
from ledger_testing.containers import localstack_address, localstack_kms, nats_address, nats_server
from ledger_testing.pki import Authority, pem
from ledger_vault.main import build
from ledger_vault.settings import KmsProvider, VaultSettings
lazy from ledger_platform.service import Service

KMS_ALIAS: Final = "alias/ledger-vault-test"
KMS_REGION: Final = "us-east-1"


def aws_session() -> boto3.session.Session:
    """LocalStack takes any credentials; these never leave the tests."""
    return boto3.session.Session(aws_access_key_id="test", aws_secret_access_key="test", region_name=KMS_REGION)


@pytest.fixture(scope="session")
def kms_endpoint() -> Generator[str]:
    """LocalStack's KMS with vault's key behind its alias."""
    with localstack_kms() as container:
        address = localstack_address(container)
        admin = cast("KMSClient", aws_session().client("kms", endpoint_url=address))
        key = admin.create_key(KeySpec="SYMMETRIC_DEFAULT", KeyUsage="ENCRYPT_DECRYPT")["KeyMetadata"]["KeyId"]
        admin.create_alias(AliasName=KMS_ALIAS, TargetKeyId=key)
        yield address


@pytest.fixture(scope="session")
def authority() -> tuple[Authority, Authority]:
    """A root of the tests' own and the intermediate that issues every certificate a test uploads."""
    root = Authority.root()
    return root, root.intermediate()


@pytest.fixture
def trust_store(tmp_path: Path, authority: tuple[Authority, Authority]) -> Path:
    path = tmp_path / "roots.pem"
    path.write_bytes(pem(authority[0].certificate))
    return path


@pytest.fixture(scope="session")
def nats_url() -> Generator[str]:
    with nats_server() as container:
        yield nats_address(container)


@pytest.fixture
def settings_for(nats_url: str, kms_endpoint: str, trust_store: Path) -> Callable[[str], VaultSettings]:
    def settings(database_url: str) -> VaultSettings:
        return VaultSettings(
            environment="test",
            database_url=SecretStr(database_url),
            nats_url=SecretStr(nats_url),
            grpc_port=0,
            kms_provider=KmsProvider.AWS,
            kms_key_id=KMS_ALIAS,
            kms_region=KMS_REGION,
            kms_endpoint_url=kms_endpoint,
            trusted_cas_path=trust_store,
        )

    return settings


@pytest.fixture
async def service(
    database: Database, database_url: str, settings_for: Callable[[str], VaultSettings]
) -> AsyncGenerator[Service[VaultSettings]]:
    """vault, started on the tests' PostgreSQL, NATS and LocalStack; ``database`` empties the tables after it."""
    del database
    built = build(settings_for(database_url), session=aws_session())
    async with built.app.router.lifespan_context(built.app):
        yield built


@pytest.fixture
async def vault(service: Service[VaultSettings]) -> AsyncGenerator[VaultServiceStub]:
    assert service.grpc_port is not None
    async with grpc.aio.insecure_channel(f"127.0.0.1:{service.grpc_port}") as channel:
        yield VaultServiceStub(channel)
```

`services/vault/tests/test_rpc.py`:

```python
import asyncio
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import grpc
import pytest
from sqlalchemy import select

from ledger.vault.v1.vault_pb2 import (
    CERTIFICATE_STATUS_ACTIVE,
    CERTIFICATE_STATUS_RETIRED,
    HOLDER_BINDING_REPRESENTATIVE,
    RETIREMENT_REASON_ROTATED,
    GetCertificateRequest,
    ListCertificatesRequest,
    RotateCertificateRequest,
    SignSeedRequest,
    SignXmlRequest,
    UploadCertificateRequest,
)
from ledger_contracts.vault import (
    LIST_CERTIFICATES,
    ROTATE_CERTIFICATE,
    SIGN_SEED,
    SIGN_XML,
    UPLOAD_CERTIFICATE,
    CertificateRefusal,
)
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.grpc_client import RemoteConflictError, RemoteDefectError, call_unary
from ledger_testing.pki import OPENING_PHRASE, Authority, bundle, ec_key, organization, person, pem
from ledger_testing.xmlsec import Xmlsec
from ledger_vault.adapters.postgres.tables import SignatureRow
lazy from ledger.vault.v1.vault_pb2_grpc import VaultServiceStub
lazy from ledger_platform.database import Database

TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
RNC: Final = "101850043"
ECF: Final = (
    b'<?xml version="1.0" encoding="utf-8"?><ECF xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    b"<Encabezado><eNCF>E310000000001</eNCF></Encabezado></ECF>"
)
SEED: Final = b"<SemillaModel><valor>abc</valor><fecha>2026-10-12T09:00:00-04:00</fecha></SemillaModel>"


def upload_request(data: bytes, password: str = OPENING_PHRASE, tenant: Tenant = TENANT) -> UploadCertificateRequest:
    return UploadCertificateRequest(
        organization_id=str(tenant.organization),
        company_id=str(tenant.company),
        tax_id=RNC,
        requested_by="2001",
        bundle=data,
        password=password,
    )


def holder_bundle(authority: tuple[Authority, Authority], cedula: str = "00112345678") -> bytes:
    return bundle(authority[1].issue(person(cedula)), chain=[authority[1].certificate])


async def test_a_certificate_is_uploaded_listed_and_signs_what_xmlsec1_verifies(
    vault: VaultServiceStub, authority: tuple[Authority, Authority], database: Database
) -> None:
    uploaded = await call_unary(vault.UploadCertificate, upload_request(holder_bundle(authority)), rpc=UPLOAD_CERTIFICATE)
    certificate = uploaded.certificate
    assert uploaded.created
    assert (certificate.generation, certificate.status, certificate.holder_binding) == (
        1,
        CERTIFICATE_STATUS_ACTIVE,
        HOLDER_BINDING_REPRESENTATIVE,
    )
    assert (certificate.holder_name, certificate.holder_id_hint) == ("JUAN PEREZ", "********678")
    signed = await call_unary(
        vault.SignXml,
        SignXmlRequest(
            organization_id=str(TENANT.organization), company_id=str(TENANT.company), document=ECF, reference="E310000000001"
        ),
        rpc=SIGN_XML,
    )
    seed = await call_unary(
        vault.SignSeed,
        SignSeedRequest(organization_id=str(TENANT.organization), company_id=str(TENANT.company), seed=SEED),
        rpc=SIGN_SEED,
    )
    with Xmlsec.started() as xmlsec:
        for document in (signed.signed_document, seed.signed_seed):
            verdict = xmlsec.verify(
                document,
                trusted=pem(authority[0].certificate),
                untrusted=pem(authority[1].certificate),
                at=datetime.now(UTC),
            )
            assert verdict.verified, verdict.output
    assert signed.certificate_id == seed.certificate_id == certificate.id
    async with database.transaction(TENANT) as unit:
        purposes = (await unit.connection.execute(select(SignatureRow.purpose).order_by(SignatureRow.signed_at))).scalars()
        assert list(purposes) == ["document", "seed"]
    listed = await call_unary(
        vault.ListCertificates,
        ListCertificatesRequest(organization_id=str(TENANT.organization), company_id=str(TENANT.company)),
        rpc=LIST_CERTIFICATES,
    )
    assert [item.id for item in listed.certificates] == [certificate.id]


async def test_a_retried_upload_is_harmless_and_another_needs_a_rotation(
    vault: VaultServiceStub, authority: tuple[Authority, Authority]
) -> None:
    data = holder_bundle(authority)
    first = await call_unary(vault.UploadCertificate, upload_request(data), rpc=UPLOAD_CERTIFICATE)
    again = await call_unary(vault.UploadCertificate, upload_request(data), rpc=UPLOAD_CERTIFICATE)
    assert (again.created, again.certificate.id) == (False, first.certificate.id)
    with pytest.raises(RemoteConflictError) as caught:
        await call_unary(vault.UploadCertificate, upload_request(holder_bundle(authority)), rpc=UPLOAD_CERTIFICATE)
    assert caught.value.remote_code == "certificate_already_active"
    rotate = RotateCertificateRequest(
        organization_id=str(TENANT.organization),
        company_id=str(TENANT.company),
        tax_id=RNC,
        requested_by="2001",
        bundle=holder_bundle(authority, "40212345678"),
        password=OPENING_PHRASE,
        replaces_generation=1,
    )
    rotated = await call_unary(vault.RotateCertificate, rotate, rpc=ROTATE_CERTIFICATE)
    assert (rotated.certificate.generation, rotated.retired_certificate_id) == (2, first.certificate.id)
    retired = await vault.GetCertificate(
        GetCertificateRequest(
            organization_id=str(TENANT.organization), company_id=str(TENANT.company), certificate_id=first.certificate.id
        )
    )
    assert (retired.certificate.status, retired.certificate.retirement_reason) == (
        CERTIFICATE_STATUS_RETIRED,
        RETIREMENT_REASON_ROTATED,
    )
    assert retired.certificate.HasField("retired_at")
    retried = await call_unary(vault.RotateCertificate, rotate, rpc=ROTATE_CERTIFICATE)
    assert (retried.certificate.id, retried.retired_certificate_id) == (rotated.certificate.id, "")
    stale = RotateCertificateRequest()
    stale.CopyFrom(rotate)
    stale.bundle = holder_bundle(authority, "40299999995")
    with pytest.raises(RemoteConflictError) as changed:
        await call_unary(vault.RotateCertificate, stale, rpc=ROTATE_CERTIFICATE)
    assert changed.value.remote_code == "certificate_changed"


@pytest.mark.parametrize(
    ("refusal", "data", "password"),
    [
        (CertificateRefusal.UNREADABLE, b"not a bundle", OPENING_PHRASE),
        (CertificateRefusal.WRONG_PASSWORD, None, "wrong"),
        (CertificateRefusal.KEY_UNSUPPORTED, "ec", OPENING_PHRASE),
        (CertificateRefusal.HOLDER_MISMATCH, "seal", OPENING_PHRASE),
        (CertificateRefusal.UNTRUSTED, "stranger", OPENING_PHRASE),
    ],
)
async def test_a_refused_certificate_answers_its_code_and_nothing_of_the_input(
    vault: VaultServiceStub,
    authority: tuple[Authority, Authority],
    refusal: CertificateRefusal,
    data: bytes | str | None,
    password: str,
) -> None:
    issuer = authority[1]
    match data:
        case bytes():
            content = data
        case "ec":
            content = bundle(issuer.issue(person(), key=ec_key()))
        case "seal":
            content = bundle(issuer.issue(organization("130000012")))
        case "stranger":
            content = bundle(Authority.root("STRANGER").issue(person()))
        case _:
            content = holder_bundle(authority)
    with pytest.raises(RemoteDefectError) as caught:
        await call_unary(vault.UploadCertificate, upload_request(content, password), rpc=UPLOAD_CERTIFICATE)
    assert (caught.value.status, caught.value.remote_code) == (grpc.StatusCode.INVALID_ARGUMENT, refusal.value)
    assert "wrong" not in str(caught.value)


async def test_nothing_signs_without_a_certificate_and_a_company_signs_with_its_own_only(
    vault: VaultServiceStub, authority: tuple[Authority, Authority]
) -> None:
    await call_unary(vault.UploadCertificate, upload_request(holder_bundle(authority)), rpc=UPLOAD_CERTIFICATE)
    stranger = Tenant(TENANT.organization, CompanyId(new_id()))
    with pytest.raises(RemoteConflictError) as caught:
        await call_unary(
            vault.SignXml,
            SignXmlRequest(organization_id=str(stranger.organization), company_id=str(stranger.company), document=ECF),
            rpc=SIGN_XML,
        )
    assert caught.value.remote_code == "no_active_certificate"


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (b"<a>", "document_malformed"),
        (b'<!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', "document_doctype_refused"),
        (b"x" * (1024 * 1024 + 1), "document_too_large"),
    ],
    ids=["malformed", "doctype", "too-large"],
)
async def test_a_document_that_cannot_be_signed_is_an_invalid_argument(
    vault: VaultServiceStub, authority: tuple[Authority, Authority], document: bytes, code: str
) -> None:
    await call_unary(vault.UploadCertificate, upload_request(holder_bundle(authority)), rpc=UPLOAD_CERTIFICATE)
    request = SignXmlRequest(organization_id=str(TENANT.organization), company_id=str(TENANT.company), document=document)
    with pytest.raises(RemoteDefectError) as caught:
        await call_unary(vault.SignXml, request, rpc=SIGN_XML)
    assert caught.value.remote_code == code


async def test_many_signatures_at_once_are_each_audited(
    vault: VaultServiceStub, authority: tuple[Authority, Authority], database: Database
) -> None:
    await call_unary(vault.UploadCertificate, upload_request(holder_bundle(authority)), rpc=UPLOAD_CERTIFICATE)
    request = SignXmlRequest(organization_id=str(TENANT.organization), company_id=str(TENANT.company), document=ECF)
    answers = await asyncio.gather(*(call_unary(vault.SignXml, request, rpc=SIGN_XML) for _ in range(20)))
    async with database.transaction(TENANT) as unit:
        recorded = set((await unit.connection.execute(select(SignatureRow.id))).scalars())
    assert recorded == {UUID(answer.signature_id) for answer in answers}
```

Append to `services/vault/tests/test_settings.py` (Granian's factory, and the local KMS's branch of `build`):

```python
def test_granians_factory_builds_the_app_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_DATABASE_URL", DATABASE.get_secret_value())
    monkeypatch.setenv("LEDGER_KMS_PROVIDER", "local")
    monkeypatch.setenv("LEDGER_LOCAL_KMS_KEY", LOCAL_KEY.get_secret_value())
    app = create_app()
    assert (app.title, app.version) == ("vault", VERSION)
```

(with `from ledger_vault.main import VERSION, create_app`).


- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/vault/tests/test_rpc.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_vault.main'`.

- [ ] **Step 4: Implement**

`services/vault/src/ledger_vault/api/__init__.py`:

```python
"""vault's interfaces: the gRPC service, the consumer of identity's events, the expiry worker."""
```

`services/vault/src/ledger_vault/api/rpc.py`:

```python
"""``ledger.vault.v1.VaultService``: custody and signing for other services (§3), thin over the use cases.

Every request names its organization and company, read as any client's input (``INVALID_ARGUMENT`` with
``invalid_id`` otherwise), and is answered inside that company. Nothing here authenticates a caller until the
mesh does (Decision 6): the port is never published outside the services' network. A caller's name, from its
user agent, goes into the audit of a signature. No answer ever holds a key.
"""

from typing import Final, final, override
lazy from collections.abc import Callable, Mapping

lazy import grpc

from ledger.vault.v1 import vault_pb2 as vault
from ledger.vault.v1.vault_pb2_grpc import VaultServiceServicer, add_VaultServiceServicer_to_server
from ledger_kernel.access import parse_subject
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, parse_id
from ledger_platform.grpc_client import current_caller
from ledger_vault.adapters.events import timestamp
from ledger_vault.application.commands import CertificateUpload
from ledger_vault.domain.certificate import CertificateStatus, Retirement
from ledger_vault.domain.holder import HolderBinding, HolderKind
from ledger_vault.domain.ids import CertificateId
from ledger_vault.domain.signing import SigningPurpose, parse_reference
lazy from ledger_platform.service import GrpcServices, Runtime
lazy from ledger_vault.application.commands import VaultCommands
lazy from ledger_vault.application.queries import VaultQueries
lazy from ledger_vault.domain.certificate import Certificate
lazy from ledger_vault.settings import VaultSettings

_STATUS: Final[Mapping[CertificateStatus, vault.CertificateStatus.ValueType]] = {
    CertificateStatus.ACTIVE: vault.CERTIFICATE_STATUS_ACTIVE,
    CertificateStatus.RETIRED: vault.CERTIFICATE_STATUS_RETIRED,
}
_KIND: Final[Mapping[HolderKind, vault.HolderKind.ValueType]] = {
    HolderKind.PERSON: vault.HOLDER_KIND_PERSON,
    HolderKind.ORGANIZATION: vault.HOLDER_KIND_ORGANIZATION,
}
_BINDING: Final[Mapping[HolderBinding, vault.HolderBinding.ValueType]] = {
    HolderBinding.TAXPAYER: vault.HOLDER_BINDING_TAXPAYER,
    HolderBinding.REPRESENTATIVE: vault.HOLDER_BINDING_REPRESENTATIVE,
}
_RETIREMENT: Final[Mapping[Retirement, vault.RetirementReason.ValueType]] = {
    Retirement.ROTATED: vault.RETIREMENT_REASON_ROTATED,
    Retirement.COMPANY_DEACTIVATED: vault.RETIREMENT_REASON_COMPANY_DEACTIVATED,
}

type _Context[Request, Response] = grpc.aio.ServicerContext[Request, Response]


def _tenant(organization: str, company: str) -> Tenant:
    return Tenant(OrganizationId(parse_id(organization)), CompanyId(parse_id(company)))


def wire_certificate(certificate: Certificate) -> vault.Certificate:
    """``certificate`` as other services receive it: never its key, its holder's identifier masked."""
    return vault.Certificate(
        id=str(certificate.id),
        organization_id=str(certificate.tenant.organization),
        company_id=str(certificate.tenant.company),
        generation=certificate.generation,
        status=_STATUS[certificate.status],
        holder_name=certificate.holder.name,
        holder_kind=_KIND[certificate.holder.kind],
        holder_id_hint=certificate.holder.hint,
        holder_binding=_BINDING[certificate.binding],
        issuer_name=certificate.issuer_name,
        serial_number=certificate.serial_number,
        fingerprint_sha256=certificate.fingerprint,
        not_before=timestamp(certificate.not_before),
        not_after=timestamp(certificate.not_after),
        key_bits=certificate.key_bits,
        uploaded_at=timestamp(certificate.uploaded_at),
        uploaded_by=certificate.uploaded_by,
        retired_at=None if certificate.retired_at is None else timestamp(certificate.retired_at),
        retirement_reason=(
            vault.RETIREMENT_REASON_UNSPECIFIED if certificate.retirement is None else _RETIREMENT[certificate.retirement]
        ),
    )


def _upload(request: vault.UploadCertificateRequest | vault.RotateCertificateRequest) -> CertificateUpload:
    return CertificateUpload(
        tenant=_tenant(request.organization_id, request.company_id),
        taxpayer=TaxId.parse(request.tax_id),
        requested_by=parse_subject(request.requested_by),
        bundle=request.bundle,
        password=request.password,
    )


@final
class VaultRpc(VaultServiceServicer):
    """vault's answers to other services."""

    def __init__(self, commands: VaultCommands, queries: VaultQueries) -> None:
        self._commands = commands
        self._queries = queries

    @override
    async def UploadCertificate(
        self,
        request: vault.UploadCertificateRequest,
        context: _Context[vault.UploadCertificateRequest, vault.UploadCertificateResponse],
    ) -> vault.UploadCertificateResponse:
        del context
        installed = await self._commands.upload(_upload(request))
        return vault.UploadCertificateResponse(
            certificate=wire_certificate(installed.certificate), created=installed.changed
        )

    @override
    async def RotateCertificate(
        self,
        request: vault.RotateCertificateRequest,
        context: _Context[vault.RotateCertificateRequest, vault.RotateCertificateResponse],
    ) -> vault.RotateCertificateResponse:
        del context
        installed = await self._commands.rotate(_upload(request), replaces=request.replaces_generation)
        retired = "" if installed.retired is None else str(installed.retired.id)
        return vault.RotateCertificateResponse(
            certificate=wire_certificate(installed.certificate), retired_certificate_id=retired
        )

    @override
    async def GetCertificate(
        self,
        request: vault.GetCertificateRequest,
        context: _Context[vault.GetCertificateRequest, vault.GetCertificateResponse],
    ) -> vault.GetCertificateResponse:
        del context
        found = await self._queries.certificate(
            _tenant(request.organization_id, request.company_id), CertificateId(parse_id(request.certificate_id))
        )
        return vault.GetCertificateResponse(certificate=wire_certificate(found))

    @override
    async def ListCertificates(
        self,
        request: vault.ListCertificatesRequest,
        context: _Context[vault.ListCertificatesRequest, vault.ListCertificatesResponse],
    ) -> vault.ListCertificatesResponse:
        del context
        found = await self._queries.certificates(_tenant(request.organization_id, request.company_id))
        return vault.ListCertificatesResponse(certificates=[wire_certificate(certificate) for certificate in found])

    @override
    async def SignXml(
        self, request: vault.SignXmlRequest, context: _Context[vault.SignXmlRequest, vault.SignXmlResponse]
    ) -> vault.SignXmlResponse:
        del context
        signed = await self._commands.sign(
            _tenant(request.organization_id, request.company_id),
            request.document,
            SigningPurpose.DOCUMENT,
            reference=parse_reference(request.reference),
            caller=current_caller(),
        )
        return vault.SignXmlResponse(
            signed_document=signed.content,
            certificate_id=str(signed.certificate),
            signature_id=str(signed.signature),
            document_sha256=signed.document_sha256,
        )

    @override
    async def SignSeed(
        self, request: vault.SignSeedRequest, context: _Context[vault.SignSeedRequest, vault.SignSeedResponse]
    ) -> vault.SignSeedResponse:
        del context
        signed = await self._commands.sign(
            _tenant(request.organization_id, request.company_id),
            request.seed,
            SigningPurpose.SEED,
            reference=None,
            caller=current_caller(),
        )
        return vault.SignSeedResponse(
            signed_seed=signed.content, certificate_id=str(signed.certificate), signature_id=str(signed.signature)
        )


type Assemble = Callable[[Runtime[VaultSettings]], tuple[VaultCommands, VaultQueries]]
"""Makes the use cases once the service runs (they need its database and outbox)."""


def services(assemble: Assemble) -> GrpcServices[VaultSettings]:
    """What vault's gRPC server serves."""

    def register(runtime: Runtime[VaultSettings], server: grpc.aio.Server) -> None:
        add_VaultServiceServicer_to_server(VaultRpc(*assemble(runtime)), server)

    return register
```


`services/vault/src/ledger_vault/main.py`:

```python
"""vault's composition root: settings, adapters and interfaces wired into one chassis ``Service``."""

from importlib.metadata import version
from typing import Final

from ledger_kernel.clock import utc_now
from ledger_platform.service import Events, Interfaces, Service
from ledger_vault.adapters.bundle import Pkcs12Reader, TrustStore
from ledger_vault.adapters.custody import DataKeyCache, EnvelopeCustody
from ledger_vault.adapters.events import VAULT
from ledger_vault.adapters.kms.aws import AwsKms
from ledger_vault.adapters.kms.local import LocalKms
from ledger_vault.adapters.pool import SigningPool
from ledger_vault.adapters.postgres.migrate import head, require_head
from ledger_vault.adapters.postgres.units import PgUnits
from ledger_vault.adapters.xml import DgiiXmlSigner
from ledger_vault.api.rpc import services
from ledger_vault.application.commands import VaultCommands
from ledger_vault.application.queries import VaultQueries
from ledger_vault.domain.certificate import CustodyPolicy
from ledger_vault.settings import KmsProvider, VaultSettings
lazy import boto3
lazy from fastapi import FastAPI
lazy from ledger_platform.service import Runtime
lazy from ledger_vault.adapters.kms import KeyManagementService

VERSION: Final = version("ledger-vault")


def _kms(settings: VaultSettings, session: boto3.session.Session | None) -> KeyManagementService:
    if settings.kms_provider is KmsProvider.LOCAL:
        return LocalKms(settings.local_key())
    return AwsKms(
        settings.kms_key_id or "", region=settings.kms_region, endpoint=settings.kms_endpoint_url, session=session
    )


def build(settings: VaultSettings, *, session: boto3.session.Session | None = None) -> Service[VaultSettings]:
    """The vault service ``settings`` describe: built, not started (its app's lifespan starts it).

    Its resources, opened in this order at start and closed in the other at stop: the signing pool, the KMS
    (the start fails on a key that cannot wrap), the custody (its cached data keys wiped at stop). Its own
    readiness check, ``migrations``. ``session`` is boto3's, for tests that name LocalStack's credentials.
    """
    pool = SigningPool(settings.signing_threads)
    kms = _kms(settings, session)
    custody = EnvelopeCustody(kms, DataKeyCache(settings.data_key_cache_seconds))
    reader = Pkcs12Reader(pool, TrustStore.at(settings.trusted_cas_path), limit=settings.max_bundle_bytes)
    signer = DgiiXmlSigner(pool, limit=settings.max_document_bytes)
    policy = CustodyPolicy(representatives=settings.accept_representatives)
    expected = head()

    def assemble(runtime: Runtime[VaultSettings]) -> tuple[VaultCommands, VaultQueries]:
        units = PgUnits(runtime.database, runtime.outbox)
        commands = VaultCommands(
            units=units, reader=reader, custody=custody, signer=signer, policy=policy, clock=utc_now
        )
        return commands, VaultQueries(units)

    async def migrations_at_head(runtime: Runtime[VaultSettings]) -> None:
        await require_head(runtime.database, expected)

    return Service(
        settings=settings,
        version=VERSION,
        interfaces=Interfaces(grpc=services(assemble)),
        events=Events(streams=(VAULT,)),
        checks={"migrations": migrations_at_head},
        resources=(pool.opened, kms.opened, custody.opened),
    )


def create_app() -> FastAPI:
    """Granian's factory (``--factory``): the app of the service the ``LEDGER_*`` environment configures."""
    return build(VaultSettings()).app
```

(`settings.kms_key_id or ""`: the settings' validator refuses `aws` without a key id, so the empty string is never used; the `or` narrows the type without a branch no test could reach.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/vault -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`_kms`'s local branch through `test_granians_factory_builds_the_app_from_the_environment`, below).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/vault
git commit -m "feat(vault): VaultService over gRPC: upload, rotate, read, sign documents and seeds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Retirement on company deactivation, and the expiry announcements

**Files:**
- Create: `services/vault/src/ledger_vault/api/deactivations.py`, `services/vault/src/ledger_vault/api/expiry.py`
- Modify: `services/vault/src/ledger_vault/main.py`
- Test: `services/vault/tests/test_deactivations.py`, `services/vault/tests/test_expiry.py`

**Interfaces:**
- Consumes: `retire_company`, `VaultCommands.announce_expiring` (Task 5); `bind`, `PgExpiryCandidates` (Task 10); `ConsumerSpec`, `Handler` (`ledger_platform.consumer`), `Envelope.unwrap`, `ensure_stream`, `StreamSpec`; `CompanyDeactivated` (`ledger.events.identity.v1`, M2 Task 7); `Events(workers=...)` and the supervisor's `Work` (fix wave); `MonotonicClock`/`Sleep` aliases.
- Produces: `ledger_vault.api.deactivations`: `CONSUMER = "vault-company-deactivations"`, `SUBJECT = "identity.company.deactivated"`, `deactivations(outbox) -> ConsumerSpec`. `ledger_vault.api.expiry`: `ExpiryGauge(registry)` (`ledger_vault_active_certificates_expiring{within_days}`), `expiry_worker(commands, candidates, gauge, *, interval, sleep=asyncio.sleep) -> Work`.

Re-check before starting: `Events(workers=...)`'s shape in the fix wave (assumed here: `workers: Callable[[Runtime[S]], Mapping[str, Work]]`, like `consumers`); `ConsumerSpec`'s fields (M1: `name, stream, subject, handler, max_deliver, backoff, ack_wait`).

- [ ] **Step 1: Write the failing tests**

`services/vault/tests/test_deactivations.py`:

```python
from typing import Final

from nats.js import JetStreamContext

from ledger.events.identity.v1.events_pb2 import CompanyDeactivated
from ledger.vault.v1.vault_pb2 import CERTIFICATE_STATUS_RETIRED, ListCertificatesRequest
from ledger_contracts.envelope import Envelope
from ledger_contracts.vault import LIST_CERTIFICATES, UPLOAD_CERTIFICATE
from ledger_platform.grpc_client import call_unary
from ledger_platform.streams import StreamSpec, ensure_stream
from ledger_testing.waiting import eventually
lazy from ledger.vault.v1.vault_pb2_grpc import VaultServiceStub
lazy from ledger_platform.service import Service
lazy from ledger_testing.pki import Authority
lazy from ledger_vault.settings import VaultSettings

from support import TENANT, holder_bundle, upload_request

IDENTITY: Final = StreamSpec(name="IDENTITY", subjects=("identity.>",))
"""identity's stream, as identity declares it (vault's tests cannot import identity)."""


async def test_a_deactivated_company_s_certificate_is_retired(
    service: Service[VaultSettings], vault: VaultServiceStub, authority: tuple[Authority, Authority]
) -> None:
    await call_unary(vault.UploadCertificate, upload_request(holder_bundle(authority)), rpc=UPLOAD_CERTIFICATE)
    jetstream: JetStreamContext = service.runtime().jetstream
    await ensure_stream(jetstream, IDENTITY)
    event = CompanyDeactivated(company_id=str(TENANT.company), organization_id=str(TENANT.organization), version=2)
    envelope = Envelope.wrap(event, source="identity", tenant=TENANT)
    await jetstream.publish("identity.company.deactivated", envelope.data, headers=envelope.headers())
    request = ListCertificatesRequest(organization_id=str(TENANT.organization), company_id=str(TENANT.company))

    async def retired() -> bool:
        listed = await call_unary(vault.ListCertificates, request, rpc=LIST_CERTIFICATES)
        return [certificate.status for certificate in listed.certificates] == [CERTIFICATE_STATUS_RETIRED]

    await eventually(retired, within=15)
```

(`holder_bundle`, `upload_request` and `TENANT` move from `test_rpc.py` into `services/vault/tests/support.py`, which both import; the consumer binds when the `IDENTITY` stream exists — the supervisor retries it after `consumer.stream_missing` until then, which is why the wait allows 15 s.)

`services/vault/tests/test_expiry.py`:

```python
import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from prometheus_client import CollectorRegistry

from ledger_vault.api.expiry import ExpiryGauge, expiry_worker

from fakes import FACTS, NOW, FakeWatch, Vault, upload


async def test_the_worker_announces_at_once_then_every_interval_and_measures() -> None:
    vault = Vault()
    vault.reader.facts = replace(FACTS, not_after=NOW + timedelta(days=5))
    await vault.commands.upload(upload())
    registry = CollectorRegistry()
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    work = expiry_worker(vault.commands, FakeWatch(vault.state), ExpiryGauge(registry), interval=3600, sleep=sleep)
    with pytest.raises(asyncio.CancelledError):
        await work()
    assert sleeps == [3600, 3600]
    expiring = [
        registry.get_sample_value("ledger_vault_active_certificates_expiring", {"within_days": days})
        for days in ("30", "7", "1")
    ]
    assert expiring == [1, 1, 0]
```

(`fakes.py` is where Task 10 moved `test_application.py`'s fakes, `FACTS`, `NOW`, `TENANT`, `Vault` and `upload`; the worker hands the commands' clock to the gauge, so the fakes' fixed time decides both.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/vault/tests/test_deactivations.py services/vault/tests/test_expiry.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_vault.api.expiry'`.

- [ ] **Step 3: Implement**

`services/vault/src/ledger_vault/api/deactivations.py`:

```python
"""vault follows identity: a deactivated company's certificate is retired, and signs no more (Decision 15).

A durable consumer of ``identity.company.deactivated`` only (no wildcard): every event it gets is that one,
and anything else is a defect of the stream's producer (``UnexpectedEventError``, kind ``INVALID``: it goes to
the dead letters at once). The handler runs in the event's company transaction, which the chassis opens.
"""

from typing import Final

from ledger.events.identity.v1.events_pb2 import CompanyDeactivated
from ledger_platform.consumer import ConsumerSpec
from ledger_vault.adapters.postgres.units import bind
from ledger_vault.application.commands import retire_company
lazy from ledger_contracts.envelope import Envelope
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.outbox import Outbox

CONSUMER: Final = "vault-company-deactivations"
STREAM: Final = "IDENTITY"
SUBJECT: Final = "identity.company.deactivated"


def deactivations(outbox: Outbox) -> ConsumerSpec:
    """The consumer that retires the certificate of each company identity deactivates."""

    async def retire(envelope: Envelope, unit: UnitOfWork) -> None:
        envelope.unwrap(CompanyDeactivated)
        await retire_company(bind(unit, outbox))

    return ConsumerSpec(name=CONSUMER, stream=STREAM, subject=SUBJECT, handler=retire)
```

`services/vault/src/ledger_vault/api/expiry.py`:

```python
"""The supervised worker that announces expiring certificates and measures them (Decision 8, §11).

A pass at start, then one every ``interval`` seconds: each due announcement is made once, whichever replica
makes it (``ExpiryWatch.claim``). After each pass the gauge says how many active certificates expire within 30,
7 and 1 days, read from the watch table, the same in every replica: aggregate it with ``max``.
"""

import asyncio
from datetime import timedelta
from typing import Final, Never, final
lazy from collections.abc import Callable, Coroutine

from prometheus_client import Gauge
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.clock import utc_now
from ledger_vault.domain.expiry import HORIZON, THRESHOLDS
lazy from ledger_kernel.clock import Clock, Sleep
lazy from ledger_vault.application.commands import VaultCommands
lazy from ledger_vault.application.ports import ExpiryCandidates

_MEASURED: Final = 10_000
"""The most watched certificates one pass reads for the gauge: far more than expire in any month."""

type Work = Callable[[], Coroutine[object, object, Never]]


@final
class ExpiryGauge:
    """``ledger_vault_active_certificates_expiring{within_days}``: active certificates expiring that soon."""

    __slots__ = ("_gauge",)

    def __init__(self, registry: CollectorRegistry) -> None:
        self._gauge = Gauge(
            "ledger_vault_active_certificates_expiring",
            "Active certificates that expire within the given number of days.",
            ["within_days"],
            registry=registry,
        )
        for days in THRESHOLDS:
            self._gauge.labels(str(days)).set(0)

    async def measure(self, candidates: ExpiryCandidates, clock: Clock = utc_now) -> None:
        """Count the watched certificates expiring within each threshold."""
        now = clock()
        watched = await candidates.due(now + HORIZON, _MEASURED)
        for days in THRESHOLDS:
            self._gauge.labels(str(days)).set(sum(w.not_after - now <= timedelta(days=days) for w in watched))


def expiry_worker(
    commands: VaultCommands,
    candidates: ExpiryCandidates,
    gauge: ExpiryGauge,
    *,
    interval: float,
    sleep: Sleep = asyncio.sleep,
) -> Work:
    """The worker (see the module); the supervisor starts it again after a failure."""

    async def watch() -> Never:
        while True:
            await commands.announce_expiring(candidates)
            await gauge.measure(candidates, commands.clock)
            await sleep(interval)

    return watch
```

`services/vault/src/ledger_vault/main.py` — `build` gains the consumer and the worker (imports: `from ledger_vault.adapters.postgres.repositories import PgExpiryCandidates`, `from ledger_vault.api.deactivations import deactivations`, `from ledger_vault.api.expiry import ExpiryGauge, expiry_worker`, `lazy from collections.abc import Mapping, Sequence`, `lazy from ledger_platform.consumer import ConsumerSpec`, `lazy from ledger_vault.api.expiry import Work`):

```python
    def consumers(runtime: Runtime[VaultSettings]) -> Sequence[ConsumerSpec]:
        return (deactivations(runtime.outbox),)

    def workers(runtime: Runtime[VaultSettings]) -> Mapping[str, Work]:
        commands, _ = assemble(runtime)
        gauge = ExpiryGauge(runtime.registry)
        candidates = PgExpiryCandidates(runtime.database)
        return {"certificate expiry": expiry_worker(commands, candidates, gauge, interval=settings.expiry_check_seconds)}
```

and `events=Events(streams=(VAULT,), consumers=consumers, workers=workers)`. (If the fix wave typed `Work` in `ledger_platform.supervision` publicly, import it from there and drop the alias in `api/expiry.py`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest services/vault -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add services/vault
git commit -m "feat(vault): retire a deactivated company's certificate; announce and measure expiries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Identity's certificate route, through vault

**Files:**
- Create: `services/identity/src/ledger_identity/application/certificates.py`, `services/identity/src/ledger_identity/adapters/vault.py`, `services/identity/src/ledger_identity/api/certificates.py`
- Modify: `services/identity/src/ledger_identity/settings.py`, `services/identity/src/ledger_identity/main.py`, `services/identity/src/ledger_identity/api/models.py`, `services/identity/src/ledger_identity/api/rest.py`, `services/identity/src/ledger_identity/openapi.py`, `services/identity/openapi.json` (regenerated), `services/identity/pyproject.toml`, `services/identity/tests/conftest.py`, `services/identity/tests/test_openapi.py`, `services/identity/tests/test_migrations.py`, `deploy/local/identity.env`
- Test: `services/identity/tests/test_certificates_api.py`

**Interfaces:**
- Consumes: `VaultServiceStub`, the vault messages, `ledger_contracts.vault` (Task 1); `Channel`, `call_unary`, `RemoteConflictError`, `RemoteDefectError`, `RemoteError` (Task 3, M2 Task 8); identity's `ApiContext`, `problems`, `stored`-style answers, `queries.company`, `Company.require_active`, `IdentityUnit` (M2 Tasks 10, 13); `authenticated`, `company_in_path`, `idempotency_key`, `attempt_of`, `json_response`, `answer`, `etag`, `parse_if_match`, `IfMatch` (M2 Task 6); `Permission.CERTIFICATE_MANAGE`, `COMPANY_READ`; `ledger_testing.pki` (Task 2).
- Produces: `IdentitySettings.vault_address: str` (required; `host:port` of vault's gRPC). `ledger_identity.application.certificates`: `CertificateBundle(bundle, password)`, `CustodiedCertificate` (the fields the API shows), `Installed(certificate, created)`, port `CertificateCustody` (`upload(company, bundle, *, actor)`, `rotate(company, bundle, *, actor, replaces)`, `active(tenant)`), errors `CertificateRefusedError` (base, `INVALID`) with one subclass per `CertificateRefusal` code, `CertificateExistsError` (`certificate_already_active`), `CertificateChangedError` (`certificate_changed`), `CertificateNotFoundError` (`certificate_not_found`, 404), use cases `install_certificate(unit, custody, company_id, bundle, *, actor, condition)` and `company_certificate(unit, custody, company_id)`. `ledger_identity.adapters.vault`: `UPLOAD_DEADLINE = 10.0`, `VaultCustody(channel: Channel, tracing: Tracing | None = None)`. `ledger_identity.api.certificates.router(context, custody)`; routes `PUT /v1/companies/{company_id}/certificate` (`Idempotency-Key`; `If-Match` optional: absent uploads, present rotates; 201 or 200; `certificate.manage`) and `GET /v1/companies/{company_id}/certificate` (`company.read`; `ETag`). Models `CertificateUpload` (`bundle`: base64, `password`: `SecretStr`), `CertificateView`.

Re-check before starting: identity as Milestone 2 left it — `ApiContext.build`'s parameters, `rest.routes`' signature, `main.build`'s `Service(...)` call (resources, checks), the conftest's `settings_for` and `service`, `openapi.document()`'s placeholder settings, and the `migrations` command-line test's environment. Each gains the vault address; nothing else of Milestone 2 changes meaning.

- [ ] **Step 1: Dependencies, settings, layering**

`services/identity/pyproject.toml` — nothing new (identity already depends on `ledger-contracts` and `grpcio`). `IdentitySettings` gains:

```python
    vault_address: str = Field(default=..., min_length=3, max_length=255, pattern=r"^[A-Za-z0-9.-]+:[0-9]{1,5}$")
    """vault's gRPC, ``host:port`` (``vault:50051`` in the local stack): reached in the services' network only."""
```

Every place Milestone 2 builds `IdentitySettings` without the environment adds `vault_address`: `openapi.document()` (`vault_address="vault.invalid:50051"`, nothing connects), the conftest's `settings_for` (`vault_address=fake_vault`), `test_openapi.py`'s factory test and `test_migrations.py`'s command-line test (`LEDGER_VAULT_ADDRESS=127.0.0.1:1`). `deploy/local/identity.env` gains `LEDGER_VAULT_ADDRESS=vault:50051`.

Root `pyproject.toml`: the contract "Identity is hexagonal" is unchanged (`application/certificates.py` and `adapters/vault.py` sit in existing layers); `ledger_identity.application` may not import `ledger_contracts` (its forbidden list): the codes it mirrors are written out, and a test compares them with the contract.

- [ ] **Step 2: Write the failing tests**

`services/identity/tests/vault_fake.py` (the class) and `services/identity/tests/conftest.py` (the fixture, appended):

```python
import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from ledger.vault.v1 import vault_pb2 as vault
from ledger.vault.v1.vault_pb2_grpc import VaultServiceServicer, add_VaultServiceServicer_to_server
from ledger_contracts.vault import CERTIFICATE_ALREADY_ACTIVE, CERTIFICATE_CHANGED, CertificateRefusal
from ledger_platform.grpc_client import ERROR_CODE_KEY
from ledger_platform.grpc_server import serve_grpc

type VaultContext[Request, Response] = grpc.aio.ServicerContext[Request, Response]


@final
class FakeVault(VaultServiceServicer):
    """vault as identity's adapter sees it: a certificate per company, refusals by password."""

    def __init__(self) -> None:
        self.certificates: dict[str, vault.Certificate] = {}
        self.uploads: list[vault.UploadCertificateRequest] = []

    def _installed(self, company: str, organization: str, generation: int) -> vault.Certificate:
        now = Timestamp()
        now.GetCurrentTime()
        certificate = vault.Certificate(
            id=str(new_id()),
            organization_id=organization,
            company_id=company,
            generation=generation,
            status=vault.CERTIFICATE_STATUS_ACTIVE,
            holder_name="JUAN PEREZ",
            holder_kind=vault.HOLDER_KIND_PERSON,
            holder_id_hint="********678",
            holder_binding=vault.HOLDER_BINDING_REPRESENTATIVE,
            issuer_name="LEDGER TEST CA",
            serial_number="1a",
            fingerprint_sha256="f" * 64,
            not_before=now,
            not_after=now,
            key_bits=2048,
            uploaded_at=now,
            uploaded_by="2001",
        )
        self.certificates[company] = certificate
        return certificate

    async def _refuse(self, password: str, context: VaultContext[object, object]) -> None:
        for refusal in CertificateRefusal:
            if password == refusal.value:
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, "refused", trailing_metadata=((ERROR_CODE_KEY, refusal.value),)
                )

    @override
    async def UploadCertificate(
        self,
        request: vault.UploadCertificateRequest,
        context: VaultContext[vault.UploadCertificateRequest, vault.UploadCertificateResponse],
    ) -> vault.UploadCertificateResponse:
        self.uploads.append(request)
        await self._refuse(request.password, context)
        if request.company_id in self.certificates:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION, "active", trailing_metadata=((ERROR_CODE_KEY, CERTIFICATE_ALREADY_ACTIVE),)
            )
        certificate = self._installed(request.company_id, request.organization_id, 1)
        return vault.UploadCertificateResponse(certificate=certificate, created=True)

    @override
    async def RotateCertificate(
        self,
        request: vault.RotateCertificateRequest,
        context: VaultContext[vault.RotateCertificateRequest, vault.RotateCertificateResponse],
    ) -> vault.RotateCertificateResponse:
        current = self.certificates.get(request.company_id)
        if current is None or current.generation != request.replaces_generation:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION, "changed", trailing_metadata=((ERROR_CODE_KEY, CERTIFICATE_CHANGED),)
            )
        assert current is not None
        certificate = self._installed(request.company_id, request.organization_id, current.generation + 1)
        return vault.RotateCertificateResponse(certificate=certificate, retired_certificate_id=current.id)

    @override
    async def ListCertificates(
        self,
        request: vault.ListCertificatesRequest,
        context: VaultContext[vault.ListCertificatesRequest, vault.ListCertificatesResponse],
    ) -> vault.ListCertificatesResponse:
        del context
        found = self.certificates.get(request.company_id)
        return vault.ListCertificatesResponse(certificates=[] if found is None else [found])


@pytest.fixture
async def fake_vault() -> AsyncGenerator[tuple[FakeVault, str]]:
    """A fake vault on a free port, and its address for identity's settings."""
    fake = FakeVault()

    def register(server: grpc.aio.Server) -> None:
        add_VaultServiceServicer_to_server(fake, server)

    async with serve_grpc(port=0, register=register) as port:
        yield fake, f"127.0.0.1:{port}"
```

and `settings_for` depends on `fake_vault` and passes `vault_address=fake_vault[1]`.

`services/identity/tests/test_certificates_api.py`:

```python
import base64
from typing import Final

import pytest
lazy import httpx

from ledger_contracts.vault import CertificateRefusal
from ledger_identity.application.certificates import CertificateRefusedError
from ledger_identity.main import build
from ledger_testing.api import Caller, document
lazy from collections.abc import Callable
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_testing.issuer import LocalIssuer

from vault_fake import FakeVault

BUNDLE: Final = base64.b64encode(b"\x30\x82 a PKCS#12 bundle").decode()


def body(password: str = "the password") -> dict[str, str]:
    return {"bundle": BUNDLE, "password": password}


def path(company: str) -> str:
    return f"/v1/companies/{company}/certificate"


async def test_a_certificate_is_uploaded_once_and_read_back_with_its_generation(
    client: httpx.AsyncClient, owner: Caller, company: str, fake_vault: tuple[FakeVault, str]
) -> None:
    created = await client.put(path(company), json=body(), headers=owner.posting("c-1"))
    assert (created.status_code, created.headers["etag"], created.headers["location"]) == (201, '"1"', path(company))
    assert document(created)["holder_id_hint"] == "********678"
    replayed = await client.put(path(company), json=body(), headers=owner.posting("c-1"))
    assert (replayed.content, replayed.headers["idempotent-replayed"]) == (created.content, "true")
    assert len(fake_vault[0].uploads) == 1
    read = await client.get(path(company), headers=owner.bearer())
    assert (read.status_code, read.headers["etag"], document(read)["generation"]) == (200, '"1"', 1)
    (upload,) = fake_vault[0].uploads
    assert (upload.tax_id, upload.requested_by, upload.password) == ("101850043", "2001", "the password")


async def test_replacing_a_certificate_needs_the_version_it_replaces(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    await client.put(path(company), json=body(), headers=owner.posting("c-1"))
    unconditional = await client.put(path(company), json=body(), headers=owner.posting("c-2"))
    assert (unconditional.status_code, document(unconditional)["code"]) == (428, "precondition_required")
    stale = await client.put(path(company), json=body(), headers=owner.posting("c-3") | {"If-Match": '"7"'})
    assert stale.status_code == 412
    rotated = await client.put(path(company), json=body(), headers=owner.posting("c-4") | {"If-Match": '"1"'})
    assert (rotated.status_code, rotated.headers["etag"]) == (200, '"2"')


async def test_a_company_without_a_certificate_has_none_to_show_or_replace(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    missing = await client.get(path(company), headers=owner.bearer())
    assert (missing.status_code, document(missing)["code"]) == (404, "certificate_not_found")
    conditional = await client.put(path(company), json=body(), headers=owner.posting("c-1") | {"If-Match": "*"})
    assert conditional.status_code == 412


@pytest.mark.parametrize("refusal", list(CertificateRefusal), ids=[refusal.value for refusal in CertificateRefusal])
async def test_vault_s_refusal_is_answered_with_its_own_code(
    client: httpx.AsyncClient, owner: Caller, company: str, refusal: CertificateRefusal
) -> None:
    response = await client.put(path(company), json=body(refusal.value), headers=owner.posting(f"r-{refusal.value}"))
    assert (response.status_code, document(response)["code"]) == (422, refusal.value)
    assert refusal.value not in str(document(response).get("detail"))


def test_identity_knows_every_refusal_vault_can_answer() -> None:
    assert {error.code for error in CertificateRefusedError.__subclasses__()} == {r.value for r in CertificateRefusal}


async def test_only_who_manages_certificates_uploads_and_nothing_is_echoed(
    client: httpx.AsyncClient, issuer: LocalIssuer, owner: Caller, company: str
) -> None:
    stranger = Caller(issuer, subject="9999")
    hidden = await client.put(path(company), json=body(), headers=stranger.posting("s-1"))
    assert (hidden.status_code, document(hidden)["code"]) == (404, "company_not_found")
    invalid = await client.put(path(company), json={"bundle": "not base64!", "password": "x"}, headers=owner.posting("s-2"))
    assert (invalid.status_code, document(invalid)["code"]) == (422, "invalid_request")
    assert "not base64" not in invalid.text


async def test_vault_down_is_a_503(
    owner: Caller, company: str, database_url: str, settings_for: Callable[[str], IdentitySettings]
) -> None:
    built = build(settings_for(database_url).model_copy(update={"vault_address": "127.0.0.1:1"}))
    async with built.app.router.lifespan_context(built.app):
        transport = httpx.ASGITransport(app=built.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://identity.test") as client:
            response = await client.put(path(company), json=body(), headers=owner.posting("d-1"))
    assert (response.status_code, document(response)["code"]) == (503, "remote_unavailable")
```

(`FakeVault` lives in `services/identity/tests/vault_fake.py`, which the conftest's `fake_vault` fixture and this test import. A viewer, granted as `test_a_viewer_reads_but_does_not_change` in Milestone 2's companies tests grants one, gets 403 `permission_denied` on `PUT` and 200 on `GET`: that case belongs here too.)

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/identity/tests/test_certificates_api.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_identity.application.certificates'`.

- [ ] **Step 4: Implement**

`services/identity/src/ledger_identity/application/certificates.py`:

```python
"""A company's signing certificate, which vault keeps: what identity's API asks of vault, and its answers.

identity never holds the bundle or the password beyond the request that carries them: they go to vault
(``CertificateCustody``) and are gone. vault refuses a certificate with a code (``ledger_contracts.vault``)
that identity answers its client with, through one error class per code below (a test keeps the two lists
equal). Installing is idempotent at vault (the same certificate again changes nothing), so a request retried
after identity failed to commit is harmless.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Protocol, final
lazy from collections.abc import Callable
lazy from datetime import datetime

from ledger_identity.application.queries import company
from ledger_kernel.errors import ConflictError, InvalidInputError, NotFoundError
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.domain.company import Company
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.ids import CompanyId, Tenant


class CertificateRefusedError(InvalidInputError):
    """vault refused the certificate; the subclass's code is vault's."""

    code: ClassVar[str] = "certificate_refused"

    def __init__(self) -> None:
        super().__init__(f"the certificate was refused: {self.code}")


class CertificateTooLargeError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_too_large"


class CertificateUnreadableError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_unreadable"


class CertificatePasswordError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_password_wrong"


class CertificateIncompleteError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_bundle_incomplete"


class CertificateKeyError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_key_unsupported"


class CertificateUsageError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_not_for_signing"


class CertificateNotYetValidError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_not_yet_valid"


class CertificateExpiredError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_expired"


class CertificateUntrustedError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_untrusted"


class CertificateHolderUnknownError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_holder_unknown"


class CertificateHolderMismatchError(CertificateRefusedError):
    code: ClassVar[str] = "certificate_holder_mismatch"


class CertificateExistsError(ConflictError):
    """The company has a certificate: replacing it names the version it replaces (``If-Match``)."""

    code: ClassVar[str] = "certificate_already_active"

    def __init__(self) -> None:
        super().__init__("the company has a certificate: replace it with If-Match")


class CertificateChangedError(ConflictError):
    """The company's certificate is not at a version ``If-Match`` names, or it changed meanwhile."""

    code: ClassVar[str] = "certificate_changed"

    def __init__(self) -> None:
        super().__init__("the company's certificate is not at the version the request names")


class CertificateNotFoundError(NotFoundError):
    """The company has no certificate."""

    code: ClassVar[str] = "certificate_not_found"

    def __init__(self) -> None:
        super().__init__("the company has no certificate")


@final
@dataclass(frozen=True, slots=True)
class CertificateBundle:
    """A PKCS#12 file and its password, as a client sent them: never in a ``repr``."""

    bundle: bytes = field(repr=False)
    password: str = field(repr=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CustodiedCertificate:
    """A company's certificate as vault describes it."""

    id: str
    generation: int
    holder_name: str = field(repr=False)
    holder_kind: str
    holder_id_hint: str
    holder_binding: str
    issuer_name: str
    serial_number: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime
    uploaded_at: datetime
    uploaded_by: str


@final
@dataclass(frozen=True, slots=True)
class Installed:
    """A certificate installed, and whether it is the company's first (``False``: a rotation or a no-op)."""

    certificate: CustodiedCertificate
    created: bool


class CertificateCustody(Protocol, metaclass=ABCMeta):
    """vault's custody of a company's certificate, as identity's use cases need it."""

    @abstractmethod
    async def upload(self, company: Company, bundle: CertificateBundle, *, actor: SubjectId) -> Installed:
        """Install the company's first certificate; ``CertificateExistsError`` when it has one."""

    @abstractmethod
    async def rotate(
        self, company: Company, bundle: CertificateBundle, *, actor: SubjectId, replaces: int
    ) -> Installed:
        """Replace the company's certificate of generation ``replaces``; ``CertificateChangedError`` otherwise."""

    @abstractmethod
    async def active(self, tenant: Tenant) -> CustodiedCertificate | None:
        """The company's active certificate, or ``None``."""


async def install_certificate(
    unit: IdentityUnit,
    custody: CertificateCustody,
    company_id: CompanyId,
    bundle: CertificateBundle,
    *,
    actor: SubjectId,
    condition: Callable[[int], bool] | None,
) -> Installed:
    """Install the company's certificate: an upload without ``condition``, a rotation of the version it admits."""
    found = await company(unit, company_id)
    found.require_active()
    if condition is None:
        return await custody.upload(found, bundle, actor=actor)
    current = await custody.active(found.tenant)
    if current is None or not condition(current.generation):
        raise CertificateChangedError
    return await custody.rotate(found, bundle, actor=actor, replaces=current.generation)


async def company_certificate(unit: IdentityUnit, custody: CertificateCustody, company_id: CompanyId) -> CustodiedCertificate:
    """The company's certificate; ``CertificateNotFoundError`` without one."""
    found = await company(unit, company_id)
    current = await custody.active(found.tenant)
    if current is None:
        raise CertificateNotFoundError
    return current
```

`services/identity/src/ledger_identity/adapters/vault.py`:

```python
"""identity's client of vault: a company's certificate uploaded, rotated and read over gRPC (Task 13).

The bundle and its password go to vault in the request and nowhere else. vault's refusals of a certificate
(``INVALID_ARGUMENT``, which the platform's client reads as a defect of the caller) are a client's mistake here:
each of vault's codes becomes identity's error of the same code. ``certificate_already_active`` and
``certificate_changed`` become identity's conflicts, which the route answers 428 and 412. vault unreachable or
failing is ``RemoteUnavailableError``: 503.
"""

from datetime import UTC
from types import MappingProxyType
from typing import Final, final, override
lazy from collections.abc import Mapping

from ledger.vault.v1 import vault_pb2 as vault
from ledger.vault.v1.vault_pb2_grpc import VaultServiceStub
from ledger_contracts.vault import (
    CERTIFICATE_ALREADY_ACTIVE,
    CERTIFICATE_CHANGED,
    LIST_CERTIFICATES,
    ROTATE_CERTIFICATE,
    UPLOAD_CERTIFICATE,
)
from ledger_identity.application import certificates as custody
from ledger_platform.grpc_client import RemoteConflictError, RemoteDefectError, call_unary
lazy from ledger_identity.domain.company import Company
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.errors import LedgerError
lazy from ledger_kernel.ids import Tenant
lazy from ledger_platform.grpc_client import Channel, RemoteError
lazy from ledger_platform.tracing import Tracing

UPLOAD_DEADLINE: Final = 10.0
"""Seconds an upload or a rotation may take: vault opens the bundle and asks its KMS for a data key."""
_REFUSALS: Final[Mapping[str, type[custody.CertificateRefusedError]]] = MappingProxyType(
    {error.code: error for error in custody.CertificateRefusedError.__subclasses__()}
)
_HOLDER_KINDS: Final = {vault.HOLDER_KIND_PERSON: "person", vault.HOLDER_KIND_ORGANIZATION: "organization"}
_BINDINGS: Final = {vault.HOLDER_BINDING_TAXPAYER: "taxpayer", vault.HOLDER_BINDING_REPRESENTATIVE: "representative"}


def _translated(error: RemoteError) -> LedgerError:
    """identity's own error for one of vault's custody codes; any other remote error stays what it is."""
    if isinstance(error, RemoteDefectError) and error.remote_code in _REFUSALS:
        return _REFUSALS[error.remote_code]()
    if isinstance(error, RemoteConflictError) and error.remote_code == CERTIFICATE_ALREADY_ACTIVE:
        return custody.CertificateExistsError()
    if isinstance(error, RemoteConflictError) and error.remote_code == CERTIFICATE_CHANGED:
        return custody.CertificateChangedError()
    return error


def _custodied(certificate: vault.Certificate) -> custody.CustodiedCertificate:
    return custody.CustodiedCertificate(
        id=certificate.id,
        generation=certificate.generation,
        holder_name=certificate.holder_name,
        holder_kind=_HOLDER_KINDS.get(certificate.holder_kind, "unknown"),
        holder_id_hint=certificate.holder_id_hint,
        holder_binding=_BINDINGS.get(certificate.holder_binding, "unknown"),
        issuer_name=certificate.issuer_name,
        serial_number=certificate.serial_number,
        fingerprint_sha256=certificate.fingerprint_sha256,
        not_before=certificate.not_before.ToDatetime(tzinfo=UTC),
        not_after=certificate.not_after.ToDatetime(tzinfo=UTC),
        uploaded_at=certificate.uploaded_at.ToDatetime(tzinfo=UTC),
        uploaded_by=certificate.uploaded_by,
    )


@final
class VaultCustody(custody.CertificateCustody):
    """``CertificateCustody`` over vault's gRPC (see the module)."""

    __slots__ = ("_channel", "_tracing")

    def __init__(self, channel: Channel, tracing: Tracing | None = None) -> None:
        self._channel = channel
        self._tracing = tracing

    @override
    async def upload(
        self, company: Company, bundle: custody.CertificateBundle, *, actor: SubjectId
    ) -> custody.Installed:
        request = vault.UploadCertificateRequest(
            organization_id=str(company.organization),
            company_id=str(company.id),
            tax_id=str(company.tax_id),
            requested_by=actor,
            bundle=bundle.bundle,
            password=bundle.password,
        )
        try:
            answer = await call_unary(
                VaultServiceStub(self._channel.current()).UploadCertificate,
                request,
                rpc=UPLOAD_CERTIFICATE,
                deadline=UPLOAD_DEADLINE,
                tracing=self._tracing,
            )
        except (RemoteDefectError, RemoteConflictError) as error:
            raise _translated(error) from error
        return custody.Installed(_custodied(answer.certificate), created=answer.created)

    @override
    async def rotate(
        self, company: Company, bundle: custody.CertificateBundle, *, actor: SubjectId, replaces: int
    ) -> custody.Installed:
        request = vault.RotateCertificateRequest(
            organization_id=str(company.organization),
            company_id=str(company.id),
            tax_id=str(company.tax_id),
            requested_by=actor,
            bundle=bundle.bundle,
            password=bundle.password,
            replaces_generation=replaces,
        )
        try:
            answer = await call_unary(
                VaultServiceStub(self._channel.current()).RotateCertificate,
                request,
                rpc=ROTATE_CERTIFICATE,
                deadline=UPLOAD_DEADLINE,
                tracing=self._tracing,
            )
        except (RemoteDefectError, RemoteConflictError) as error:
            raise _translated(error) from error
        return custody.Installed(_custodied(answer.certificate), created=False)

    @override
    async def active(self, tenant: Tenant) -> custody.CustodiedCertificate | None:
        request = vault.ListCertificatesRequest(organization_id=str(tenant.organization), company_id=str(tenant.company))
        answer = await call_unary(
            VaultServiceStub(self._channel.current()).ListCertificates,
            request,
            rpc=LIST_CERTIFICATES,
            tracing=self._tracing,
        )
        active = [c for c in answer.certificates if c.status == vault.CERTIFICATE_STATUS_ACTIVE]
        return _custodied(active[0]) if active else None
```

(When `_translated` returns the remote error itself, `raise ... from error` makes it its own cause, which Python accepts and logs once; the chassis answers it by its kind: an unknown `INVALID_ARGUMENT` code from vault stays a defect, 500.)

`services/identity/src/ledger_identity/api/models.py` — append:

```python
class CertificateUpload(_Shape):
    """A signing certificate to install: the PKCS#12 file the certification authority issued, and its password."""

    bundle: str = Field(
        min_length=4,
        max_length=87_384,
        pattern=r"^[A-Za-z0-9+/]+={0,2}$",
        description="The .p12 or .pfx file, base64 (standard alphabet). At most 64 KiB once decoded.",
    )
    password: SecretStr = Field(min_length=1, max_length=128, description="The file's password. Never stored.")


class CertificateView(_Shape):
    """The company's signing certificate, at its generation (its ``ETag``). Its key never leaves vault."""

    id: str
    generation: int
    holder_name: str
    holder_kind: str
    holder_id_hint: str = Field(description="The holder's cédula, RNC or passport, all but its last three masked.")
    holder_binding: str = Field(description="taxpayer, or representative: a person acting for the company.")
    issuer_name: str
    serial_number: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime
    uploaded_at: datetime
    uploaded_by: str

    @classmethod
    def of(cls, certificate: CustodiedCertificate) -> Self:
        """The view of ``certificate``."""
        return cls(
            id=certificate.id,
            generation=certificate.generation,
            holder_name=certificate.holder_name,
            holder_kind=certificate.holder_kind,
            holder_id_hint=certificate.holder_id_hint,
            holder_binding=certificate.holder_binding,
            issuer_name=certificate.issuer_name,
            serial_number=certificate.serial_number,
            fingerprint_sha256=certificate.fingerprint_sha256,
            not_before=certificate.not_before,
            not_after=certificate.not_after,
            uploaded_at=certificate.uploaded_at,
            uploaded_by=certificate.uploaded_by,
        )
```

(with `from pydantic import SecretStr` and `lazy from ledger_identity.application.certificates import CustodiedCertificate`.) The idempotency fingerprint of a `PUT` reads `body.model_dump_json()`, which writes a `SecretStr` as `**********`: the password is never part of it, nor stored.

`services/identity/src/ledger_identity/api/certificates.py`:

```python
"""``/v1/companies/{company_id}/certificate``: the company's signing certificate, which vault keeps.

``PUT`` installs one: without ``If-Match`` the company's first (201; 428 when it has one), with ``If-Match`` a
replacement of the version it names (200; 412 when stale). It takes an ``Idempotency-Key``: a retry answers
the stored response. ``GET`` reads it, with its generation as ``ETag``.
"""

import base64
from contextlib import contextmanager
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header
from starlette.exceptions import HTTPException
lazy from collections.abc import Generator
lazy from fastapi import Request, Response

from ledger_identity.api.context import problems
from ledger_identity.api.models import CertificateUpload, CertificateView
from ledger_identity.application.certificates import (
    CertificateBundle,
    CertificateChangedError,
    CertificateExistsError,
    company_certificate,
    install_certificate,
)
from ledger_kernel.access import Permission
from ledger_platform.routes import answer, attempt_of, etag, idempotency_key, json_response, parse_if_match
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.application.certificates import CertificateCustody, Installed
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.service import Runtime

_MAX_BUNDLE: Final = 64 * 1024


@contextmanager
def preconditions() -> Generator[None]:
    """vault's custody conflicts as HTTP preconditions: an existing certificate 428, a stale one 412."""
    try:
        yield
    except CertificateExistsError as error:
        raise HTTPException(HTTPStatus.PRECONDITION_REQUIRED) from error
    except CertificateChangedError as error:
        raise HTTPException(HTTPStatus.PRECONDITION_FAILED) from error


def _bundle(upload: CertificateUpload) -> CertificateBundle:
    """The bundle's bytes: the model's pattern admits base64 only, so decoding cannot fail."""
    data = base64.b64decode(upload.bundle)
    if len(data) > _MAX_BUNDLE:
        raise HTTPException(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    return CertificateBundle(data, upload.password.get_secret_value())


def _stored(company: str, installed: Installed) -> StoredResponse:
    status = HTTPStatus.CREATED if installed.created else HTTPStatus.OK
    headers = {"Location": f"/v1/companies/{company}/certificate", "ETag": etag(installed.certificate.generation)}
    return json_response(status, CertificateView.of(installed.certificate), headers)


def router(context: ApiContext, custody: CertificateCustody) -> APIRouter:
    """The certificate's routes."""
    certificates = APIRouter(prefix="/v1/companies/{company_id}/certificate", tags=["certificates"])
    in_company, runtime = context.in_company, context.runtime

    @certificates.put(
        "",
        status_code=HTTPStatus.OK,
        response_model=CertificateView,
        responses={HTTPStatus.CREATED: {"model": CertificateView}} | problems(401, 403, 404, 409, 412, 413, 422, 428, 503),
    )
    async def install(
        body: CertificateUpload,
        request: Request,
        access: Annotated[CompanyAccess, Depends(in_company)],
        key: Annotated[str, Depends(idempotency_key)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
        if_match: Annotated[str | None, Header(alias="If-Match", max_length=1024)] = None,
    ) -> Response:
        """Install the company's signing certificate, or replace it (``If-Match``)."""
        access.require(Permission.CERTIFICATE_MANAGE)
        bundle = _bundle(body)
        condition = None if if_match is None else parse_if_match(if_match).admits
        attempt = attempt_of(request, access.principal, key, body)
        with preconditions():
            async with current.database.transaction(access.tenant) as transaction:

                async def work() -> StoredResponse:
                    installed = await install_certificate(
                        context.bind(transaction, current.outbox),
                        custody,
                        access.tenant.company,
                        bundle,
                        actor=access.principal.subject,
                        condition=condition,
                    )
                    return _stored(str(access.tenant.company), installed)

                outcome = await context.idempotency.once(transaction, attempt, work)
        return answer(outcome)

    @certificates.get("", response_model=CertificateView, responses=problems(401, 403, 404, 422, 503))
    async def read(
        access: Annotated[CompanyAccess, Depends(in_company)],
        response: Response,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> CertificateView:
        """The company's signing certificate, with its generation as ``ETag``."""
        access.require(Permission.COMPANY_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await company_certificate(context.bind(transaction, current.outbox), custody, access.tenant.company)
        response.headers["ETag"] = etag(found.generation)
        return CertificateView.of(found)

    return certificates
```


`services/identity/src/ledger_identity/api/rest.py` — `routes(context: ApiContext, custody: CertificateCustody)` returns the existing routers plus `certificates.router(context, custody)`.

`services/identity/src/ledger_identity/main.py` — `build` opens a channel to vault as a resource and wires the custody:

```python
    channel = Channel(settings.vault_address, caller=settings.service_name, version=VERSION, idempotent=IDEMPOTENT_METHODS)
    custody = VaultCustody(channel)
```

`routes(context, custody)` in `http(runtime)`, and `resources=(keys.opened, channel.opened)` (imports: `from ledger_contracts.vault import IDEMPOTENT_METHODS`, `from ledger_identity.adapters.vault import VaultCustody`, `from ledger_platform.grpc_client import Channel`). The custody's client spans need the runtime's `Tracing`, which exists only once the service runs: construct `VaultCustody(channel)` without it here, and pass `runtime.tracing` where M2's `ApiContext.build` hands the runtime to dependencies if the fix wave exposes resources to routes; without it the call still carries the active trace (`traceparent`), only the client span is missing.

Then regenerate the document: `uv run poe openapi`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run poe openapi && uv run pytest services/identity -q --no-cov`
Expected: PASS (the published OpenAPI document gains the two operations; every error documented as `application/problem+json`).

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add deploy/local/identity.env services/identity
git commit -m "feat(identity): a company's signing certificate installed and read through vault

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: The signing benchmark

**Files:**
- Create: `services/vault/benchmarks/sign_latency.py`
- Modify: `pyproject.toml` (root: a `poe` task, outside `check`)

**Interfaces:**
- Consumes: `build`, `VaultSettings`, `KmsProvider` (Tasks 9, 11), `upgrade` (Task 9), `Postgres`, `nats_server`, `localstack_kms` (M2, Task 2), `pki` (Task 2), `call_unary`.
- Produces: `uv run poe bench-vault`: prints p50, p95 and p99 of `SignXml` over gRPC, one call at a time (latency) and 16 at once (throughput), for a 60-line e-CF (about 12.5 KB), with PostgreSQL, NATS and LocalStack's KMS in containers; exits 1 when the serial p95 is 50 ms or more (Decision 9).

Not part of `poe check`: timing assertions do not belong in a suite that runs on shared CI machines. Run it before and after any change of the signing path, and in the performance pass after Milestone 2.

- [ ] **Step 1: The script**

`services/vault/benchmarks/sign_latency.py`:

```python
"""How long vault takes to sign a typical e-CF, over gRPC, with its database and KMS (Decision 9).

Starts PostgreSQL, NATS and LocalStack's KMS in containers, migrates vault's database, runs vault in this
process, uploads a certificate of a test authority, then signs a 60-line e-CF: 50 calls to warm up, 500 one at
a time (the latency a caller sees), and 2000 sixteen at a time (the throughput of one replica). Exits 1 when the
serial p95 reaches ``TARGET_P95_MS``.
"""

import asyncio
import statistics
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, cast

import boto3
import grpc
from pydantic import SecretStr
from types_boto3_kms.client import KMSClient

from ledger.vault.v1.vault_pb2 import SignXmlRequest, UploadCertificateRequest
from ledger.vault.v1.vault_pb2_grpc import VaultServiceStub
from ledger_contracts.vault import SIGN_XML, UPLOAD_CERTIFICATE
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.grpc_client import call_unary
from ledger_testing.containers import Postgres, localstack_address, localstack_kms, nats_address, nats_server
from ledger_testing.pki import OPENING_PHRASE, Authority, bundle, person
from ledger_vault.adapters.postgres.migrate import upgrade
from ledger_vault.main import build
from ledger_vault.settings import KmsProvider, VaultSettings

TARGET_P95_MS: Final = 50.0
WARM_UP: Final = 50
SERIAL: Final = 500
CONCURRENT: Final = 2000
IN_FLIGHT: Final = 16
REGION: Final = "us-east-1"
ALIAS: Final = "alias/ledger-vault-bench"
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))


def typical_ecf(lines: int = 60) -> bytes:
    """An e-CF of type 31 with ``lines`` items: the size of a typical invoice."""
    items = "".join(
        f"<Item><NumeroLinea>{line}</NumeroLinea><NombreItem>Martillo número {line}</NombreItem>"
        "<CantidadItem>2.00</CantidadItem><PrecioUnitarioItem>150.00</PrecioUnitarioItem>"
        "<MontoItem>300.00</MontoItem></Item>"
        for line in range(1, lines + 1)
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?><ECF xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"><Encabezado><Version>1.0</Version>'
        "<IdDoc><TipoeCF>31</TipoeCF><eNCF>E310000000001</eNCF></IdDoc></Encabezado>"
        f"<DetallesItems>{items}</DetallesItems><FechaHoraFirma>12-10-2026 09:00:00</FechaHoraFirma></ECF>"
    ).encode()


def percentile(samples: list[float], share: float) -> float:
    return statistics.quantiles(samples, n=100)[round(share * 100) - 1]


async def signed_in(vault: VaultServiceStub, request: SignXmlRequest) -> float:
    started = time.perf_counter()
    await call_unary(vault.SignXml, request, rpc=SIGN_XML)
    return (time.perf_counter() - started) * 1000


async def measure(database_url: str, nats_url: str, kms_url: str) -> float:
    session = boto3.session.Session(aws_access_key_id="test", aws_secret_access_key="test", region_name=REGION)
    admin = cast("KMSClient", session.client("kms", endpoint_url=kms_url))
    admin.create_alias(AliasName=ALIAS, TargetKeyId=admin.create_key()["KeyMetadata"]["KeyId"])
    root = Authority.root()
    intermediate = root.intermediate()
    folder = TemporaryDirectory()
    roots = Path(folder.name) / "roots.pem"
    roots.write_bytes(root.issued.pem())
    settings = VaultSettings(
        environment="test",
        database_url=SecretStr(database_url),
        nats_url=SecretStr(nats_url),
        grpc_port=0,
        kms_provider=KmsProvider.AWS,
        kms_key_id=ALIAS,
        kms_region=REGION,
        kms_endpoint_url=kms_url,
        trusted_cas_path=roots,
    )
    service = build(settings, session=session)
    async with service.app.router.lifespan_context(service.app):
        async with grpc.aio.insecure_channel(f"127.0.0.1:{service.grpc_port}") as channel:
            vault = VaultServiceStub(channel)
            upload = UploadCertificateRequest(
                organization_id=str(TENANT.organization),
                company_id=str(TENANT.company),
                tax_id="101850043",
                requested_by="bench",
                bundle=bundle(intermediate.issue(person()), chain=[intermediate.certificate]),
                password=OPENING_PHRASE,
            )
            await call_unary(vault.UploadCertificate, upload, rpc=UPLOAD_CERTIFICATE, deadline=10.0)
            request = SignXmlRequest(
                organization_id=str(TENANT.organization), company_id=str(TENANT.company), document=typical_ecf()
            )
            for _ in range(WARM_UP):
                await signed_in(vault, request)
            serial = [await signed_in(vault, request) for _ in range(SERIAL)]
            gate = asyncio.Semaphore(IN_FLIGHT)

            async def bounded() -> float:
                async with gate:
                    return await signed_in(vault, request)

            started = time.perf_counter()
            concurrent = await asyncio.gather(*(bounded() for _ in range(CONCURRENT)))
            elapsed = time.perf_counter() - started
    folder.cleanup()
    p95 = percentile(serial, 0.95)
    sys.stdout.write(
        f"document {len(typical_ecf())} bytes\n"
        f"serial      p50 {percentile(serial, 0.50):6.2f} ms  p95 {p95:6.2f} ms  p99 {percentile(serial, 0.99):6.2f} ms\n"
        f"{IN_FLIGHT} at once  p50 {percentile(concurrent, 0.50):6.2f} ms  p95 {percentile(concurrent, 0.95):6.2f} ms"
        f"  {CONCURRENT / elapsed:7.1f} signatures/s\n"
    )
    return p95


def main() -> int:
    with Postgres.started() as postgres, nats_server() as nats, localstack_kms() as kms:
        url = postgres.database("vault_bench", role="vault_bench", password="bench-password")
        upgrade(url)
        loop = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
        p95 = asyncio.run(measure(url, nats_address(nats), localstack_address(kms)), loop_factory=loop)
    return 0 if p95 < TARGET_P95_MS else 1


if __name__ == "__main__":
    sys.exit(main())
```

(psycopg's asynchronous driver needs a selector event loop on Windows, as the root `conftest.py` gives the tests.)

Root `pyproject.toml`:

```toml
bench-vault = { cmd = "python services/vault/benchmarks/sign_latency.py", help = "vault's signing latency (not part of check)" }
```

`services/vault/benchmarks` is linted and type-checked with the rest (`basedpyright` includes `services`, `mypy services/vault` too); it is outside `src`, so coverage and deptry do not read it.

- [ ] **Step 2: Run it**

Run: `uv run poe bench-vault`
Expected: exit 0, serial p95 under 50 ms. Record the three lines in the task report (machine, OS, CPU). Measured components on the plan's probe machine: signing alone p95 0.81 ms; LocalStack's KMS `Decrypt` adds a few milliseconds per call (a cloud KMS in-region 5 to 15 ms): if p95 fails the target, measure with `LEDGER_DATA_KEY_CACHE_SECONDS=60` before anything else, and report both.

- [ ] **Step 3: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml services/vault/benchmarks
git commit -m "test(vault): signing latency benchmark over gRPC with PostgreSQL and the KMS

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: The image, the local stack and CI

**Files:**
- Create: `services/vault/Dockerfile`, `deploy/local/vault.env`, `deploy/local/localstack/init-kms.sh`
- Modify: `deploy/local/compose.yaml`, `deploy/local/postgres/init.sql`, `Tiltfile`, `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `create_app` and `ledger-vault-migrate` (Tasks 9, 11); identity's image and compose services (M2 Task 17); `ledger-probe` (M2 Task 17).
- Produces: image `ledger-vault:local` (distroless, `nonroot`, Granian on uvloop, health check on `/readyz`); compose services `localstack` (4.14.0 pinned by digest, KMS only, the key `alias/ledger-vault-local` created at its start), `vault-migrate` (one-shot) and `vault` (HTTP `127.0.0.1:8001` for its probes and metrics; gRPC 50051 inside the network only); database `vault` owned by role `vault`; identity configured with `LEDGER_VAULT_ADDRESS=vault:50051`.

Re-check before starting: identity's `Dockerfile`, compose services and CI step as Milestone 2 left them; vault's follow them exactly but for the package and the ports.

- [ ] **Step 1: The image**

`services/vault/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# The vault service. Built from the workspace's lockfile with uv; run by Granian on uvloop, as an unprivileged
# user, from a file system it never writes to. Build from the repository root:
#   docker build -f services/vault/Dockerfile -t ledger-vault:local .

FROM ghcr.io/astral-sh/uv:0.13.0-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv python install 3.15.0
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package ledger-vault

# Distroless: glibc, CA certificates and time zones, no shell; user nonroot (65532). lxml and cryptography
# bring their own libxml2 and OpenSSL in their wheels.
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
LABEL org.opencontainers.image.title="ledger-vault" \
      org.opencontainers.image.description="Custody of signing certificates and XML signatures for DGII" \
      org.opencontainers.image.source="https://github.com/balegre0/ledger"
COPY --from=build /python /python
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER nonroot
EXPOSE 8000 50051
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["/app/.venv/bin/ledger-probe", "http://127.0.0.1:8000/readyz"]
ENTRYPOINT ["/app/.venv/bin/granian", "--interface", "asgi", "--factory", "--host", "0.0.0.0", "--port", "8000", \
            "--loop", "uvloop", "--workers", "1", "--no-access-log", "--respawn-failed-workers", \
            "ledger_vault.main:create_app"]
```

Run: `docker build -f services/vault/Dockerfile -t ledger-vault:local .`
Expected: the image is tagged; `docker image inspect ledger-vault:local --format '{{.Config.User}}'` prints `nonroot`.

- [ ] **Step 2: The local stack**

`deploy/local/postgres/init.sql` — append:

```sql
-- The vault service's own database and role, on the same terms as identity's.
CREATE ROLE vault LOGIN PASSWORD 'vault' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE vault OWNER vault;
```

`deploy/local/localstack/init-kms.sh`:

```sh
#!/bin/sh
# Runs inside LocalStack once its services are ready (/etc/localstack/init/ready.d): the KMS key vault wraps its
# data keys with, behind the alias deploy/local/vault.env names. LocalStack's state is not persisted, so the key
# is new at every start: a laptop's sealed keys do not survive `docker compose down`; upload again.
set -eu
key=$(awslocal kms create-key --description "ledger vault (local)" --query KeyMetadata.KeyId --output text)
awslocal kms create-alias --alias-name alias/ledger-vault-local --target-key-id "$key"
```

(committed with the executable bit: `git update-index --chmod=+x deploy/local/localstack/init-kms.sh`.)

`deploy/local/vault.env`:

```
# The vault service in the local stack: AWS KMS as LocalStack serves it (any credentials do), no authority
# trusted until a test hierarchy's root is mounted at LEDGER_TRUSTED_CAS_PATH (uploads answer
# certificate_untrusted until then).
LEDGER_ENVIRONMENT=local
LEDGER_LOG_FORMAT=json
LEDGER_DATABASE_URL=postgresql+psycopg://vault:vault@postgres:5432/vault
LEDGER_NATS_URL=nats://nats:4222
LEDGER_KMS_PROVIDER=aws
LEDGER_KMS_KEY_ID=alias/ledger-vault-local
LEDGER_KMS_REGION=us-east-1
LEDGER_KMS_ENDPOINT_URL=http://localstack:4566
AWS_ACCESS_KEY_ID=local
AWS_SECRET_ACCESS_KEY=local
```

`deploy/local/compose.yaml` — services after `identity`:

```yaml
  localstack:
    # AWS KMS for laptops and CI. Pinned to 4.14.0, the last LocalStack that starts without an account
    # (Decision 12 of milestone 3); its init hook creates vault's key.
    image: localstack/localstack:4.14.0@sha256:3ebc37595918b8accb852f8048fef2aff047d465167edd655528065b07bc364a
    environment:
      SERVICES: kms
    volumes:
      - ./localstack/init-kms.sh:/etc/localstack/init/ready.d/init-kms.sh:ro
    healthcheck:
      # Ready once the init hook has run, not merely once LocalStack answers.
      test: ["CMD-SHELL", "curl -fs http://127.0.0.1:4566/_localstack/init/ready | grep -q '\"completed\": true'"]
      interval: 5s
      timeout: 3s
      retries: 20

  vault-migrate:
    image: ledger-vault:local
    build:
      context: ../..
      dockerfile: services/vault/Dockerfile
    entrypoint: ["/app/.venv/bin/ledger-vault-migrate"]
    env_file: [vault.env]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    restart: "no"
    depends_on:
      postgres: {condition: service_healthy}

  vault:
    image: ledger-vault:local
    env_file: [vault.env]
    # HTTP for probes and metrics only (vault has no public API); gRPC 50051 stays inside this network: it
    # authenticates no caller until the mesh does.
    ports: ["127.0.0.1:8001:8000"]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    depends_on:
      vault-migrate: {condition: service_completed_successfully}
      nats: {condition: service_healthy}
      localstack: {condition: service_healthy}
```

and `identity`'s `depends_on` gains `vault: {condition: service_healthy}` (its upload route needs vault; its other routes do not, but a local stack starts whole).

Confirm the hook's readiness answer on the pinned image first: `docker run --rm -d -p 4566:4566 -e SERVICES=kms <image>` then `curl -s localhost:4566/_localstack/init/ready` (it answers `{"completed": true, ...}` once every `ready.d` script ran); if 4.14.0 words it otherwise, match what it prints.

`Tiltfile` — beside identity's:

```python
docker_build(
    "ledger-vault:local",
    ".",
    dockerfile="services/vault/Dockerfile",
    only=["pyproject.toml", "uv.lock", ".python-version", "libs", "services"],
    ignore=["**/tests", "**/benchmarks", "**/__pycache__", "**/.venv"],
)

dc_resource("localstack", labels=["infrastructure"])
dc_resource("vault-migrate", labels=["vault"])
dc_resource("vault", labels=["vault"], links=[link("http://localhost:8001/readyz", "vault readiness")])
```

Run:

```bash
docker compose -f deploy/local/compose.yaml down -v
docker compose -f deploy/local/compose.yaml up -d --wait --build
curl --fail --silent --show-error http://127.0.0.1:8001/readyz
docker compose -f deploy/local/compose.yaml run --rm vault-migrate
docker compose -f deploy/local/compose.yaml down -v
```

Expected: every service healthy (vault's `/readyz` lists `database`, `nats`, `workers`, the chassis's tables and `migrations`, all `true`); the second migration exits 0.

- [ ] **Step 3: CI**

In `.github/workflows/ci.yml`, the local-stack step builds both images and checks vault too:

```yaml
      - name: Local stack starts healthy, identity and vault from their production images
        run: |
          compose='docker compose -p ledger-ci -f deploy/local/compose.yaml'
          trap '$compose down -v' EXIT
          $compose build identity-migrate vault-migrate
          $compose up -d --wait || { $compose logs; exit 1; }
          curl --fail --silent --show-error http://127.0.0.1:8000/readyz
          curl --fail --silent --show-error http://127.0.0.1:8001/readyz
          test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8000/v1/companies)" = 401
          for image in ledger-identity:local ledger-vault:local; do
            test "$(docker image inspect "$image" --format '{{.Config.User}}')" = nonroot
          done
          $compose run --rm identity-migrate
          $compose run --rm vault-migrate
```

(The job's `timeout-minutes` stays 30 unless the run shows otherwise; the xmlsec1 test image is built by the test step itself and cached by the runner's Docker only within the job.)

- [ ] **Step 4: Gate**

Run: `uv run poe fmt && uv run poe check && docker compose -f deploy/local/compose.yaml config --quiet`
Expected: every step passes; the compose file is valid.

- [ ] **Step 5: Commit**

```bash
git add Tiltfile .github deploy services/vault/Dockerfile
git commit -m "build(vault): distroless image, local stack with LocalStack's KMS, CI smoke test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: README and the spec brought in line

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-10-11-ledger-platform-design.md`

**Interfaces:**
- Consumes: everything above.
- Produces: no code. The spec states what this milestone built and records Decisions 1, 6, 8, 10, 12, 13, 15 and 16; the README gains a section on vault and updates "Not done yet".

Re-check before starting: the spec's sentences as Milestone 2's Task 18 left them (they are quoted below as Milestone 2 writes them; where the fix wave reworded one, make the same change of substance).

- [ ] **Step 1: The spec**

Make these edits:

1. §3, the `vault` row becomes:

```text
| `vault` | certificates (encrypted, envelope with KMS), their metadata (holder, validity, generation), signing audit log (append-only), expiry watch | gRPC `VaultService` (upload, rotate, get, list, sign XML, sign seed); no public API: uploads come through `identity` (`PUT /v1/companies/{id}/certificate`); events `vault.certificate.uploaded/rotated/retired/expiring` | `identity.company.deactivated` (retires the company's certificate) |
```

2. §4.2, replace Milestone 2's sentence on deadlines (`Deadlines always set by the caller: a fixed deadline per call, at most 30 s (milestone 2). Deriving it from the caller's own remaining deadline, and retries only on idempotent methods (declared in the service config, hedging off), come with the first service-to-service caller (milestone 3).`) with:

```text
- Deadlines always set by the caller, at most 30 s, and never longer than what is left of the call being
  answered (a call with no time left is not made). Retries only on the methods a channel declares idempotent
  (gRPC's service config: three attempts on UNAVAILABLE, hedging off). A caller names its service in the user
  agent; the trace travels in `traceparent`, with a client and a server span per call. A service opens its
  channels to others as resources of its own.
```

and to the accepted-risk bullet add: `vault records the calling service's own name in its signing audit as declared, unauthenticated until the mesh.`

3. §5.1, step b: `Vault.Sign (XMLDSig enveloped, inclusive C14N, RSA-SHA256, Reference URI="")` becomes `VaultService.SignXml (XMLDSig enveloped, inclusive C14N 1.0, RSA-SHA256, SHA-256 digest, Reference URI="", the certificate in KeyInfo, the Signature element in DGII's shape)`; step e gains `(the semilla is signed by VaultService.SignSeed)`.

4. §9, the rows:

```text
| Certificate custody | `.p12` uploaded once through identity, opened in `vault`, private key (PKCS#8) encrypted with AES-256-GCM under a per-certificate data key, bound to organization, company and certificate (associated data and KMS encryption context); the data key wrapped by the cloud KMS (envelope). The KMS key never leaves the KMS; the local KMS of a laptop is refused in production. Retired certificates are kept with their sealed key |
| Key use | Only `VaultService.Sign*` uses keys, unsealed for one operation and wiped after it (best effort: Python's limits are stated in `ledger_vault.domain.keys`); a data-key cache exists, off by default (at most 300 s, private keys never cached). Every signature is recorded before it is returned (who, company, certificate, document SHA-256, purpose, reference), never the document |
| Certificate checks | RSA of at least 2048 bits; a key usage that allows signing; within its validity; a chain to a configured trust store of accepted Dominican authorities (none built in); a holder read from the subject as the Dominican authorities write it (`serialNumber` `IDCDO-<cédula>`/`PAS..`, `organizationIdentifier` `VATDO-<RNC>`, or a bare cédula) that is the company itself or — DGII's model for its *certificado para procedimiento tributario* — a natural person acting for it (`LEDGER_ACCEPT_REPRESENTATIVES=false` demands the taxpayer itself); an organization certificate of another RNC is refused. Revocation (OCSP/CRL) is checked from the certification milestone |
| Audit | Append-only audit per service for security-relevant actions: vault's signatures table (PostgreSQL refuses its updates and deletes) and its certificates (never deleted, retirements recorded); identity's memberships keep who changed them last, and every change is an event; export to the log store with the deployment milestone |
```

5. §11, Traces: replace `the instrumentation of SQLAlchemy and gRPC arrives with the first call between services (milestone 3).` with `gRPC calls are traced from milestone 3 (a client and a server span, the trace carried in metadata); SQL statements are traced from the performance pass after milestone 2.` Metrics: after `certificates expiring` add `(`ledger_vault_active_certificates_expiring{within_days}`, read from the watch table: aggregate with max)`.

6. §12, the table: the XML row becomes `lxml, xmlschema (signxml as a test verifier) | 6.1 / 4.3 (5.1) | C14N and XMLDSig written to DGII's shape, XSD validation`; add rows `| AWS KMS client | boto3 (+ types-boto3-lite) | 1.43 | the KMS adapter; credentials from boto3's chain |` and `| Local AWS | LocalStack | 4.14.0 | the last release usable without an account |`.

7. §14, the XML row: `golden files validated against DGII's XSDs; signatures verified by an independent verifier (xmlsec1)` becomes `golden files validated against DGII's XSDs; signatures verified by an independent verifier: xmlsec1 1.2.41 in a container, with the certificate chain, on fixed shapes and on generated documents (and by signxml)`.

8. §15, Local development: add `LocalStack (KMS) for vault,` before `and, from milestone 2`.

Run: `uv run poe spell`
Expected: no typos.

- [ ] **Step 2: The README**

Add a section after identity's:

```text
## The vault service

- Keeps each company's signing certificate: uploaded once through identity (`PUT /v1/companies/{id}/certificate`,
  `Idempotency-Key`, `If-Match` to replace it), opened and checked in vault, its private key sealed under a
  per-certificate data key the KMS wraps (AWS KMS; LocalStack locally). A rotation retires the old certificate
  and keeps it.
- Signs for other services over gRPC (`ledger.vault.v1.VaultService`): `SignXml` for documents DGII receives,
  `SignSeed` for DGII's authentication seed, both enveloped XMLDSig as DGII verifies it, proven in the tests by
  xmlsec1. Every signature is in an append-only audit before it is returned; documents are not kept.
- Holders are read from the subject as the Dominican authorities write them; a person may sign for a company
  as its representative unless `LEDGER_ACCEPT_REPRESENTATIVES=false`.
- Retires a company's certificate when identity deactivates it; announces expiries 30, 7 and 1 days ahead
  (`vault.certificate.expiring`) and exports how many are close.
- `uv run poe bench-vault` measures `SignXml` (target p95 < 50 ms on a laptop).
- Its gRPC port authenticates no caller until the mesh: never publish it.
```

and in "Not done yet": remove the items this milestone did (tracing of gRPC calls, deadline derivation and retries, the audit log's first part), add: `SQL statement tracing (performance pass)`, `revocation checks of certificates (certification milestone)`, `re-wrapping data keys under a new KMS key (operations)`, `export of the audit to the log store (deployment milestone)`, and under "Operations, before production": `vault's trust store (LEDGER_TRUSTED_CAS_PATH) holds the roots of Viafirma, Digifirma and Novofirma as INDOTEL publishes them, reviewed when one rotates its root`.

- [ ] **Step 3: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes.

- [ ] **Step 4: Commit**

```bash
git add README.md docs
git commit -m "docs: vault in the README and the spec, with the milestone's decisions and deferrals

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review (for the plan author; already applied)

**Spec coverage.** §16's milestone 3 ("certificate upload, envelope encryption, signing over gRPC, audit") and the owner's eight decisions:

| Requirement | Where |
|---|---|
| Owner 1: `services/vault`, hexagonal, gRPC only, `ledger.vault.v1.VaultService` with upload, describe (get, list), sign XML (DGII profile), sign seed, rotate; no key export | Tasks 1, 4, 5, 11 (no message carries a key; `KeyMaterial` never leaves the use case) |
| Owner 2: envelope encryption, AES-256-GCM, 96-bit nonce, AD binding company and certificate; `KeyManagementService` port; AWS KMS tested on LocalStack (pinned, ≥ 2 weeks old, verified to start); local adapter refused in production; plaintext only for one operation, zeroization with Python's limits documented | Tasks 4 (`KeyContext`, `KeyMaterial`), 6, 7, 9 (settings), Decision 12 |
| Owner 3: PKCS#12 with password (wrong password typed, never echoed), one key and its certificate (+ chain), RSA ≥ 2048, validity, holder per the Dominican convention (researched, sourced, strategies, tested), chain to a configurable trust store (none built in; tests make their own hierarchy) | Tasks 2, 4, 6; Decision 1 (and its open question for the owner) |
| Owner 4: independent verification by xmlsec1 (container) and signxml; golden C14N and signature shape; property tests of round trip; hardened parsing (no entities, no network, no DTD, size first) | Tasks 2, 8; Decision 13 |
| Owner 5: append-only `signatures` (who, company, certificate, SHA-256, purpose, time; no content), `certificates` metadata, both under RLS; events uploaded/rotated/expiring through the outbox; a supervised worker for 30/7/1 days | Tasks 5, 9, 10, 12; Decision 8 |
| Owner 6: identity's `PUT` (JSON base64 with password, `company.manage` → `certificate.manage` by Decision 2, `Idempotency-Key`) and `GET`; vault answers by the scope in the request, unauthenticated until the mesh | Task 13; Decisions 2, 6 |
| Owner 7: Dockerfile like identity's, compose with its database, role and LocalStack, CI smoke | Task 15 |
| Owner 8: signing off the event loop on a bounded executor sized by a setting; benchmark with a target; data-key cache justified and off by default; plaintext keys never cached | Tasks 6, 11, 14; Decisions 9, 10 |
| §2 rule 7 | no RPC returns key material; the only way out of `KeyMaterial` is a signature |
| §2 rule 8, tenancy twice | repositories bound to the unit's company (Task 10), RLS on the company's tables (Task 9), composite foreign keys (Task 9), a test across companies (Task 10) |
| §4.1 aggregate versions | every vault event carries the generation |
| §4.2 deadlines, retries, identity in metadata | Task 3 (M2's deferral to this milestone is done) |
| §9 "nothing a client sent leaves in an error" | refusals name codes, limits and dates (Task 4 tests), the bundle and password are `repr=False` everywhere, identity's fingerprint omits the password |
| §11 traces across services, certificate-expiry metric | Tasks 3, 12 |
| §14 XML signatures by an independent verifier | Task 8 |

Deferred on purpose, recorded in Decision 16 and Task 16: SQL tracing, revocation checks, KMS-key rotation (re-wrapping), the audit's export to the log store, gRPC caller authentication.

**Placeholder scan.** No "TBD", "TODO" or "similar to Task N". Every code step of Tasks 2, 4, 5, 6, 7 and 8 was run as written in a scratch workspace on 2026-10-12 (CPython 3.15.0, ruff 0.17, basedpyright 1.40.2 `all`, mypy 2.4 `--strict`, pytest with branch coverage: 103 tests, 100 % of the vault code they cover, xmlsec1 and LocalStack in containers). Tasks 3 and 9 to 15 build on Milestone 2's code and the fix wave's hooks, which do not exist yet: they are written against the Milestone 2 plan's declared interfaces, say what to re-check, and in a few places give the executor a precise instruction (a test to add, a form to prefer) where the code's final shape depends on what lands — each such note names the exact change.

**Type consistency.** Checked across tasks: `KeyMaterial`, `KeyContext`, `SealedKey(provider, key_id, wrapped_key, nonce, ciphertext)`, `CertificateFacts` (eleven fields), `Certificate` (twenty fields), `Candidate`, `Installation(certificate, retired, event)`, `upload(current, candidate, *, last_generation, now)`, `rotate(current, candidate, *, replaces, now)`, `admit(facts, *, taxpayer, now, policy)`, `VaultCommands(units, reader, custody, signer, policy, clock)` and its `upload`, `rotate(request, *, replaces)`, `sign(tenant, document, purpose, *, reference, caller)`, `announce_expiring(candidates, *, limit)`, `retire_company(unit)`, `VaultQueries.certificate/certificates`, `OpenedBundle(facts, key)`, `SignedDocument(content, document_sha256)`, `Pkcs12Reader(pool, trust, *, limit)`, `TrustStore.from_pem/at/trusts`, `EnvelopeCustody(kms, cache)`, `DataKeyCache(seconds, *, capacity, clock)`, `AwsKms(key_id, *, region, endpoint, session)`, `LocalKms(key)`, `DgiiXmlSigner(pool, *, limit)`, `sign_enveloped(document, key, certificate, purpose)`, `PgUnits(database, outbox, *, clock)`, `bind(unit, outbox, *, clock)`, `outgoing(event)`, `VaultRpc(commands, queries)`, `build(settings, *, session)`, `Channel(target, *, caller, version, idempotent)`, `call_unary(..., tracing=)`, `current_caller()`, `Migrations(scripts, *, owner)`, the refusal codes (`CertificateRefusal`, vault's domain errors and identity's mirrors, each pair held equal by a test).

**Review focus.** Each of the five lines has its tests where it says: Task 8 (signatures and xmlsec1), Tasks 4, 6, 8, 11 (hostile input), Tasks 4, 6, 7, 9 (custody), Tasks 9, 10, 11 (tenancy and concurrency), Task 13 (identity's route).

**What could not be verified** (the executor checks these first if a step fails): every task on Milestone 2's code (identity, `Scope`, `routes`, `Idempotency`, `ledger_testing`'s M2 modules) and on the fix wave's `checks=`, `resources=`, `Events(workers=...)`; gRPC retries through the service config against a real flaky server (the test is written, not run); Alembic's autogenerate comparison of the partial unique index `uq_certificates_active` (if it reports a difference, give the model's `Index` the same `postgresql_where` text the revision writes, `status = 'active'`, and re-run); PostgreSQL row triggers on statements that touch no row (Task 9 notes it); LocalStack 4.14.0's `/_localstack/init/ready` wording for compose's health check; DGII's own acceptance of a vault signature (only their `TesteCF`, milestone 4, proves it; xmlsec1 and .NET follow the same C14N and XMLDSig rules); the published profiles of Digifirma and Novofirma.

---

## Changelog

- 2026-10-12: first version.
