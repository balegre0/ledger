"""signer's tests: the worker's database migrated as production migrates it; signer's schema its login's.

signer is the module under test of ``ledger_testing``'s module fixtures; the template holds the platform's
schema, identity's (the module its manifest depends on, for its events) and signer's, each brought to head by
its revisions. signer is isolated in the signer's role: its schema is ``ledger_signer``'s alone, so
``database`` and ``module_database`` log in as ``ledger_signer``, and ``app_database`` is the login every
other app uses, which must reach none of it. What is signer's own is here: who its use cases act as, and the
in-memory stand-ins of the application's ports its use cases are tested over.

The stand-ins are fixtures (the tests directory is not importable): a test types each by the port it stands
for, and reads what a recording one recorded through a small Protocol of its own. They behave as the adapters
must: a unit commits what it recorded when its block ends, and keeps nothing when it raises; the store refuses
two active certificates serving one mode, as the partial unique indexes do; the KMS binds its context; custody
seals keys and credentials under data keys it unwraps for every signature; the entity sees digests only.
"""

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final, Literal, assert_type, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Mapping, Sequence
lazy from uuid import UUID

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from ledger_fiscal_kit.xml import dsig
from ledger_kernel.errors import LedgerError
from ledger_kernel.tenancy import Tenant
from ledger_platform.data.audit import Actor, acting_as, current_actor
from ledger_platform.data.codec import canonical_json, encode, payload_of
from ledger_platform.http.idempotency import IdempotencyKeyReusedError
from ledger_signer._manifest import MANIFEST
from ledger_signer.adapters.postgres.schema import MIGRATIONS
from ledger_signer.application.ports import (
    ActiveCertificateTakenError,
    Admitted,
    Audit,
    BundleIncompleteError,
    BundleUnreadableError,
    Certificates,
    CertificationEntity,
    Custody,
    DataKey,
    EntityCertificate,
    EntityRefusedError,
    EntityUnavailableError,
    Events,
    ImportKeys,
    ImportOutcome,
    Jobs,
    KeyManagement,
    KmsRefusedError,
    KmsUnavailableError,
    SealedMaterial,
    SignerUnit,
    SignerUnits,
)
from ledger_signer.domain.certificates import BackendKind, CertificateFacts
from ledger_testing.modules import ModuleUnderTest
from ledger_testing.pki import Authority, distinguished_name, rsa_key
lazy from ledger_fiscal_kit.xml.dsig import Prepared
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_signer.api import CertificateView
lazy from ledger_signer.application.ports import AuditAction, ImportClaim, JobKind, SealContext, Secret
lazy from ledger_signer.domain.certificates import Certificate, CertificateId
lazy from ledger_signer.events import CertificateEvent
lazy from ledger_testing.pki import Issued
lazy from ledger_testing.postgres import Template

UNDER_TEST: Final = ModuleUnderTest(MANIFEST, MIGRATIONS)
TEST_ACTOR: Final = Actor("system:test", "signer-tests")
"""Who the use cases a test calls itself act as; a request through the app acts as its caller instead."""
NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
"""Where the stand-in clock starts, and around which the test certificates are valid."""
_NONCE_BYTES: Final = 12
_DATA_KEY_BITS: Final = 256
_SIGNATURE_TAKES: Final = 0.01
"""Seconds a stand-in signature takes: long enough for others to start meanwhile, up to the bound."""


def pytest_ledger_template(template: Template) -> None:
    """The roles and the schemas ``platform``, ``identity`` and ``signer``, each at head, relay granted."""
    UNDER_TEST.prepare(template)


@pytest.fixture
def module_under_test() -> ModuleUnderTest:
    """signer, for the module fixtures of ``ledger_testing``."""
    return UNDER_TEST


@pytest.fixture(autouse=True)
def acting() -> Iterator[None]:
    """Every test acts as ``TEST_ACTOR``: what its own calls of the use cases audit needs an actor."""
    with acting_as(TEST_ACTOR):
        yield


@final
class MovableClock:
    """The signer's clock in a test: it reads ``now``, which the test sets."""

    __slots__ = ("now",)

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        """The time the test set."""
        return self.now


@pytest.fixture
def clock() -> MovableClock:
    """The signer's clock, at ``NOW`` until the test moves it."""
    return MovableClock(NOW)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Audited:
    """An audit row as a stand-in unit kept it: before and after as the canonical JSON the platform hashes."""

    scope: Tenant
    actor: Actor
    """The acting call when the row was recorded, as ``uow.audit`` reads it."""
    action: AuditAction
    subject: UUID
    before: bytes | None
    after: bytes | None
    detail: Mapping[str, str | int]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Enqueued:
    """A job as a stand-in unit enqueued it."""

    scope: Tenant
    kind: JobKind
    key: str
    run_at: datetime
    payload: Mapping[str, str | int]


class SimulatedWriteError(LedgerError):
    """The stand-in database refused the unit's audit rows: its commit fails."""

    code: ClassVar[str] = "test_audit_write_refused"

    def __init__(self) -> None:
        super().__init__("the audit rows could not be written")


def _hashed(value: object) -> bytes | None:
    """What ``uow.audit`` hashes of a before or an after: its canonical JSON (so it must have one)."""
    return None if value is None else canonical_json(encode(value))


@final
class _Pending:
    """What one stand-in unit changed and recorded, kept apart until it commits."""

    __slots__ = ("audits", "claims", "events", "jobs", "keys", "materials", "rows")

    def __init__(self, store: MemoryUnits) -> None:
        self.rows = dict(store.rows)
        self.materials = dict(store.materials)
        self.keys = dict(store.keys)
        self.events: list[CertificateEvent] = []
        self.audits: list[Audited] = []
        self.jobs: list[Enqueued] = []
        self.claims: list[ImportClaim] = []


@final
class _MemoryCertificates(Certificates):
    """The certificates a stand-in unit sees: its organization's, each statement naming the company."""

    __slots__ = ("_pending", "_store", "_tenant")

    def __init__(self, store: MemoryUnits, pending: _Pending, tenant: Tenant) -> None:
        self._store = store
        self._pending = pending
        self._tenant = tenant

    def _of(self, company: CompanyId) -> list[Certificate]:
        owner = Tenant(self._tenant.organization, company, None)
        return [row for row in self._pending.rows.values() if row.tenant == owner]

    @override
    async def active(self, company: CompanyId, mode: Mode) -> Certificate | None:
        return next((row for row in self._of(company) if row.serves(mode)), None)

    @override
    async def active_of_company(self, company: CompanyId, *, for_update: bool) -> Sequence[Certificate]:
        self._store.locked.append(for_update)
        return [row for row in self._of(company) if row.is_active]

    @override
    async def get(
        self, company: CompanyId, certificate: CertificateId, *, for_update: bool = False
    ) -> Certificate | None:
        self._store.locked.append(for_update)
        return next((row for row in self._of(company) if row.id == certificate), None)

    @override
    async def listed(self, company: CompanyId, mode: Mode, *, include_retired: bool) -> Sequence[Certificate]:
        return [row for row in self._of(company) if mode in row.modes and (row.is_active or include_retired)]

    def _require_one_per_mode(self, certificate: Certificate) -> None:
        """The partial unique indexes: one active certificate per company and mode."""
        serving = [
            row
            for row in self._of(certificate.tenant.company)
            if row.id != certificate.id and row.is_active and not row.modes.isdisjoint(certificate.modes)
        ]
        if certificate.is_active and serving:
            raise ActiveCertificateTakenError

    @override
    async def add(self, certificate: Certificate, material: SealedMaterial) -> None:
        rival = self._store.rival
        if rival is not None:  # another unit installed one for the mode, and committed first
            self._store.rival = None
            self._store.seed(rival, material)
            raise ActiveCertificateTakenError
        self._require_one_per_mode(certificate)
        self._pending.rows[certificate.id] = certificate
        self._pending.materials[certificate.id] = material

    @override
    async def save(self, certificate: Certificate) -> None:
        self._require_one_per_mode(certificate)
        self._pending.rows[certificate.id] = certificate
        if not certificate.is_active:
            del self._pending.materials[certificate.id]

    @override
    async def material(self, certificate: Certificate) -> SealedMaterial:
        return self._pending.materials[certificate.id]


@final
class _MemoryEvents(Events):
    __slots__ = ("_pending",)

    def __init__(self, pending: _Pending) -> None:
        self._pending = pending

    @override
    def publish(self, event: CertificateEvent) -> None:
        _ = payload_of(event)  # the outbox's codec must take it
        self._pending.events.append(event)


@final
class _MemoryAudit(Audit):
    __slots__ = ("_pending", "_tenant")

    def __init__(self, pending: _Pending, tenant: Tenant) -> None:
        self._pending = pending
        self._tenant = tenant

    @override
    def record(
        self,
        action: AuditAction,
        subject: UUID,
        *,
        detail: Mapping[str, str | int],
        before: object = None,
        after: object = None,
    ) -> None:
        _ = encode(dict(detail))
        audited = Audited(
            scope=self._tenant,
            actor=current_actor(),
            action=action,
            subject=subject,
            before=_hashed(before),
            after=_hashed(after),
            detail=dict(detail),
        )
        self._pending.audits.append(audited)


@final
class _MemoryJobs(Jobs):
    __slots__ = ("_pending", "_tenant")

    def __init__(self, pending: _Pending, tenant: Tenant) -> None:
        self._pending = pending
        self._tenant = tenant

    @override
    def enqueue(self, kind: JobKind, key: str, *, run_at: datetime, payload: Mapping[str, str | int]) -> None:
        _ = encode(dict(payload))
        job = Enqueued(scope=self._tenant, kind=kind, key=key, run_at=run_at, payload=dict(payload))
        self._pending.jobs.append(job)


@final
class _MemoryImportKeys(ImportKeys):
    """``Idempotency.once`` as an adapter wraps it: a key per organization, mode, principal and key."""

    __slots__ = ("_pending", "_tenant")

    def __init__(self, pending: _Pending, tenant: Tenant) -> None:
        self._pending = pending
        self._tenant = tenant

    @override
    async def once(self, claim: ImportClaim, work: Callable[[], Awaitable[CertificateView]]) -> ImportOutcome:
        self._pending.claims.append(claim)
        identity = (self._tenant.organization, str(self._tenant.mode), claim.principal, claim.key)
        kept = self._pending.keys.get(identity)
        if kept is not None:
            request_hash, view = kept
            if request_hash != claim.request_hash:
                raise IdempotencyKeyReusedError
            return ImportOutcome(view=view, replayed=True)
        view = await work()
        self._pending.keys[identity] = (claim.request_hash, view)
        return ImportOutcome(view=view, replayed=False)


@final
class MemoryUnits(SignerUnits):
    """signer's state in memory, in units that commit whole or not at all (see the module)."""

    __slots__ = (
        "_clock",
        "audits",
        "claims",
        "events",
        "jobs",
        "keys",
        "locked",
        "materials",
        "opened",
        "refuse_audit",
        "rival",
        "rows",
    )

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self.rows: dict[CertificateId, Certificate] = {}
        self.materials: dict[CertificateId, SealedMaterial] = {}
        self.keys: dict[tuple[UUID, str, str, str], tuple[bytes, CertificateView]] = {}
        self.events: list[CertificateEvent] = []
        self.audits: list[Audited] = []
        self.jobs: list[Enqueued] = []
        self.claims: list[ImportClaim] = []
        self.opened: list[Tenant] = []
        self.locked: list[bool] = []
        self.refuse_audit = False
        """Fail the commit of a unit that recorded an audit row (the database refused the insert)."""
        self.rival: Certificate | None = None
        """Committed by another unit when the next certificate is added: that add fails the unique index."""

    def seed(self, certificate: Certificate, material: SealedMaterial) -> None:
        """Keep ``certificate`` as if a unit had committed it."""
        self.rows[certificate.id] = certificate
        if certificate.is_active:
            self.materials[certificate.id] = material

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[SignerUnit]:
        self.opened.append(tenant)
        pending = _Pending(self)
        yield SignerUnit(
            certificates=_MemoryCertificates(self, pending, tenant),
            events=_MemoryEvents(pending),
            audit=_MemoryAudit(pending, tenant),
            jobs=_MemoryJobs(pending, tenant),
            import_keys=_MemoryImportKeys(pending, tenant),
            now=self._clock(),
        )
        if self.refuse_audit and pending.audits:
            raise SimulatedWriteError
        self.rows, self.materials, self.keys = pending.rows, pending.materials, pending.keys
        self.events.extend(pending.events)
        self.audits.extend(pending.audits)
        self.jobs.extend(pending.jobs)
        self.claims.extend(pending.claims)


@pytest.fixture
def units(clock: MovableClock) -> MemoryUnits:
    """signer's units over an empty in-memory store, their time the test's clock."""
    return MemoryUnits(clock)


def _context_bytes(context: Mapping[str, str]) -> bytes:
    return canonical_json(encode(dict(context)))


@final
class MemoryKms(KeyManagement):
    """A KMS of one master key that binds each data key to its context; it counts its unwraps."""

    __slots__ = ("_master", "available", "unwraps")

    KEY_ID: ClassVar[str] = "memory:1"

    def __init__(self) -> None:
        self._master = AESGCM(AESGCM.generate_key(bit_length=_DATA_KEY_BITS))
        self.available = True
        self.unwraps = 0

    def _require_available(self) -> None:
        if not self.available:
            raise KmsUnavailableError

    @override
    async def new_data_key(self, context: Mapping[str, str]) -> DataKey:
        self._require_available()
        plaintext = AESGCM.generate_key(bit_length=_DATA_KEY_BITS)
        nonce = os.urandom(_NONCE_BYTES)
        wrapped = nonce + self._master.encrypt(nonce, plaintext, _context_bytes(context))
        return DataKey(plaintext=plaintext, wrapped=wrapped, key_id=self.KEY_ID)

    @override
    async def unwrap(self, wrapped: bytes, *, key_id: str, context: Mapping[str, str]) -> bytes:
        self._require_available()
        self.unwraps += 1
        if key_id != self.KEY_ID:
            raise KmsRefusedError
        return self._master.decrypt(wrapped[:_NONCE_BYTES], wrapped[_NONCE_BYTES:], _context_bytes(context))

    @override
    async def ready(self) -> None:
        self._require_available()


@pytest.fixture
def kms() -> MemoryKms:
    """An available KMS."""
    return MemoryKms()


@final
class MemoryEntity(CertificationEntity):
    """A certification entity that signs digests with its holder's key; it records each digest it signed."""

    __slots__ = ("_holder", "available", "digests", "refusing")

    def __init__(self, holder: Issued) -> None:
        self._holder = holder
        self.available = True
        self.refusing = False
        self.digests: list[bytes] = []

    def _require_serving(self) -> None:
        if not self.available:
            raise EntityUnavailableError
        if self.refusing:
            raise EntityRefusedError

    @override
    async def certificate(self, credential: bytes) -> EntityCertificate:
        self._require_serving()
        return EntityCertificate(chain=(self._holder.certificate.public_bytes(serialization.Encoding.DER),))

    @override
    async def sign_digest(self, credential: bytes, digest: bytes) -> bytes:
        self._require_serving()
        self.digests.append(digest)
        key = self._holder.key
        assert isinstance(key, rsa.RSAPrivateKey)
        return key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))


def _facts(certificate: x509.Certificate, *, matches_key: bool) -> CertificateFacts:
    """What custody reads of a certificate, as far as the stand-in needs (Task 10's ``facts_of``)."""
    public = certificate.public_key()
    return CertificateFacts(
        der=certificate.public_bytes(serialization.Encoding.DER),
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()),
        serial_number=format(certificate.serial_number, "x"),
        subject=certificate.subject.rfc4514_string(),
        issuer=certificate.issuer.rfc4514_string(),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
        rsa_bits=public.key_size if isinstance(public, rsa.RSAPublicKey) else None,
        may_sign=dsig.signing_problem(certificate) is None,
        is_authority=certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca,
        matches_key=matches_key,
    )


@final
class MemoryCustody(Custody):
    """Custody over the stand-in KMS and entity: keys and credentials sealed, unwrapped for each signature.

    ``most_in_flight`` is the most signatures it made at once; ``damaged`` makes it sign with a key that is
    not the certificate's (custody broken, as a swapped row would be).
    """

    __slots__ = ("_entity", "_in_flight", "_kms", "damaged", "most_in_flight")

    def __init__(self, kms: MemoryKms, entity: MemoryEntity) -> None:
        self._kms = kms
        self._entity = entity
        self._in_flight = 0
        self.damaged = False
        self.most_in_flight = 0

    async def _seal(self, plaintext: bytes, seal_for: SealContext) -> SealedMaterial:
        data_key = await self._kms.new_data_key(seal_for.context())
        nonce = os.urandom(_NONCE_BYTES)
        associated = _context_bytes(seal_for.context())
        sealed = nonce + AESGCM(data_key.plaintext).encrypt(nonce, plaintext, associated)
        return SealedMaterial(sealed=sealed, wrapped_data_key=data_key.wrapped, kms_key_id=data_key.key_id)

    async def _unseal(self, material: SealedMaterial, seal_for: SealContext) -> bytes:
        context = seal_for.context()
        data_key = await self._kms.unwrap(
            material.wrapped_data_key, key_id=material.kms_key_id, context=context
        )
        sealed = material.sealed
        return AESGCM(data_key).decrypt(sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:], _context_bytes(context))

    @override
    async def admit_local(self, bundle: bytes, password: Secret, *, seal_for: SealContext) -> Admitted:
        try:
            key, certificate, _ = pkcs12.load_key_and_certificates(bundle, password.reveal().encode())
        except ValueError:
            raise BundleUnreadableError from None
        if not isinstance(key, rsa.RSAPrivateKey) or certificate is None:
            raise BundleIncompleteError
        own = certificate.public_key()
        matches = (
            isinstance(own, rsa.RSAPublicKey) and own.public_numbers() == key.public_key().public_numbers()
        )
        plain = key.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        return Admitted(
            facts=_facts(certificate, matches_key=matches), material=await self._seal(plain, seal_for)
        )

    @override
    async def admit_remote(self, credential: Secret, *, seal_for: SealContext) -> Admitted:
        plain = credential.reveal().encode()
        chain = (await self._entity.certificate(plain)).chain
        certificate = x509.load_der_x509_certificate(chain[0])
        return Admitted(
            facts=_facts(certificate, matches_key=True), material=await self._seal(plain, seal_for)
        )

    @override
    async def sign(
        self,
        material: SealedMaterial,
        *,
        backend: BackendKind,
        prepared: Prepared,
        certificate_der: bytes,
        seal_for: SealContext,
    ) -> bytes:
        self._in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self._in_flight)
        try:
            plain = await self._unseal(material, seal_for)
            await asyncio.sleep(_SIGNATURE_TAKES)
            match backend:
                case BackendKind.LOCAL:
                    key = (
                        rsa_key()
                        if self.damaged
                        else serialization.load_der_private_key(plain, password=None)
                    )
                    assert isinstance(key, rsa.RSAPrivateKey)
                    value = key.sign(prepared.to_sign, padding.PKCS1v15(), hashes.SHA256())
                case _:
                    assert_type(backend, Literal[BackendKind.REMOTE])
                    value = await self._entity.sign_digest(plain, hashlib.sha256(prepared.to_sign).digest())
            return prepared.complete(value, x509.load_der_x509_certificate(certificate_der))
        finally:
            self._in_flight -= 1


@pytest.fixture(scope="session")
def authority() -> Authority:
    """The authority that issued the test certificates."""
    return Authority.root(now=NOW)


@pytest.fixture(scope="session")
def holder(authority: Authority) -> Issued:
    """A company's signing certificate and its key, valid from a day before ``NOW`` for a year."""
    return authority.issue(
        distinguished_name((NameOID.SERIAL_NUMBER, "HOLDER-SENTINEL"), (NameOID.COMMON_NAME, "Holder")),
        not_before=NOW - timedelta(days=1),
        not_after=NOW + timedelta(days=365),
    )


@pytest.fixture(scope="session")
def entity_holder(authority: Authority) -> Issued:
    """The certificate and key a certification entity keeps for a company, valid as ``holder`` is."""
    return authority.issue(
        distinguished_name((NameOID.COMMON_NAME, "Entity Holder")),
        not_before=NOW - timedelta(days=1),
        not_after=NOW + timedelta(days=365),
    )


@pytest.fixture
def entity(entity_holder: Issued) -> MemoryEntity:
    """An available certification entity that holds ``entity_holder``."""
    return MemoryEntity(entity_holder)


@pytest.fixture
def custody(kms: MemoryKms, entity: MemoryEntity) -> MemoryCustody:
    """Custody over ``kms`` and ``entity``."""
    return MemoryCustody(kms, entity)
