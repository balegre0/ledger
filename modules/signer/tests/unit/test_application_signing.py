"""Signing over in-memory ports, and the facade that serves it in the signer's own process.

A signature is made outside any unit, bounded by the signing pool, and answered only once its audit row has
committed; the window and the clock are checked before custody is asked; a key that does not match its
certificate is custody damaged, never the document's fault (plan Decisions 4, 5 and 18; Review Focus 3, 4, 6).
"""

import asyncio
import hashlib
from datetime import timedelta
from typing import Final, Literal, Protocol, assert_type, final, override
lazy from collections.abc import Mapping
lazy from datetime import datetime
lazy from uuid import UUID

import pytest
from structlog.testing import capture_logs

from ledger_fiscal_kit.ports import (
    CertificateValidity,
    SeedToSign,
    SignatureProfile,
    SignatureRefusedError,
    SignerUnavailableError,
    SigningCertificateMissingError,
    SigningCertificateNotValidError,
    SigningClockSkewError,
    XmlToSign,
)
from ledger_fiscal_kit.xml import dsig
from ledger_fiscal_kit.xml.hardened import MalformedDocumentError
from ledger_kernel.errors import ErrorKind, InvalidValueError, LedgerError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.data.codec import payload_of
from ledger_platform.runtime.calls import SystemCall
from ledger_platform.runtime.facade import require_facades
from ledger_signer.application.certificates import Describing, Retiring
from ledger_signer.application.facade import IN_PROCESS, SignerFacade, port_error
from ledger_signer.application.importing import Importing, ImportRequest
from ledger_signer.application.ports import Custody, Secret, SignerRules, SignerUnits
from ledger_signer.application.signing import CustodyDamagedError, Signing
from ledger_signer.domain.certificates import BackendKind
from ledger_signer.domain.errors import ClockSkewError, NotCoveredError
from ledger_testing.pki import bundle
lazy from ledger_fiscal_kit.ports import Signed
lazy from ledger_fiscal_kit.xml.dsig import Prepared
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.data.audit import Actor
lazy from ledger_signer.api import CertificateView
lazy from ledger_signer.application.ports import Admitted, SealContext, SealedMaterial
lazy from ledger_signer.events import CertificateEvent
lazy from ledger_testing.pki import Issued

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
OWNER: Final = Tenant(ORGANIZATION, COMPANY, None)
IN_TEST: Final = Tenant(ORGANIZATION, COMPANY, Mode.TEST)
IN_LIVE: Final = Tenant(ORGANIZATION, COMPANY, Mode.LIVE)
SKEW: Final = timedelta(seconds=300)
PROFILE: Final = SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256
DOCUMENT: Final = b"<ECF><Encabezado><Nombre>DOCUMENT-SENTINEL</Nombre></Encabezado></ECF>"
SEED: Final = b"<SemillaModel><valor>SEED-SENTINEL</valor></SemillaModel>"
REFERENCE: Final = "E310000000001"
AUDIENCE: Final = "ecf.dgii.gov.do"
PASSWORD: Final = "PASSWORD-SENTINEL"
CALL: Final = SystemCall(reason="test-signing", scope=IN_TEST)


class Audited(Protocol):
    """An audit row the stand-in units kept."""

    @property
    def scope(self) -> Tenant: ...
    @property
    def actor(self) -> Actor: ...
    @property
    def action(self) -> str: ...
    @property
    def subject(self) -> UUID: ...
    @property
    def before(self) -> bytes | None: ...
    @property
    def after(self) -> bytes | None: ...
    @property
    def detail(self) -> Mapping[str, str | int]: ...


class Store(SignerUnits, Protocol):
    """The stand-in units, and what they committed."""

    audits: list[Audited]
    events: list[CertificateEvent]
    opened: list[Tenant]
    refuse_audit: bool


class Kms(Protocol):
    """How often the stand-in KMS unwrapped a data key, and its switch."""

    unwraps: int
    available: bool


class Entity(Protocol):
    """The digests the stand-in entity signed."""

    digests: list[bytes]


class Pool(Custody, Protocol):
    """The stand-in custody, and what it saw of the signing pool."""

    most_in_flight: int
    damaged: bool


class Moving(Protocol):
    """The stand-in clock."""

    now: datetime

    def __call__(self) -> datetime: ...


def rules(concurrency: int = 4) -> SignerRules:
    """Rules offering both backends, the default skew, ``concurrency`` signatures at once."""
    return SignerRules(
        backends=frozenset(BackendKind),
        skew=SKEW,
        concurrency=concurrency,
        import_concurrency=2,
        notice_days=(30, 14, 7, 1, 0),
    )


def signing(units: SignerUnits, custody: Custody, clock: Clock, *, concurrency: int = 4) -> Signing:
    """The signing use case over the stand-ins."""
    return Signing(units=units, custody=custody, rules=rules(concurrency), clock=clock)


async def install(
    units: SignerUnits,
    custody: Custody,
    clock: Clock,
    holder: Issued,
    *,
    backend: BackendKind = BackendKind.LOCAL,
) -> CertificateView:
    """A certificate installed for test mode through the import use case."""
    use_case = Importing(units=units, custody=custody, rules=rules(), clock=clock)
    request = ImportRequest(tenant=IN_TEST, modes=frozenset({Mode.TEST}), idempotency_key=str(new_id()))
    match backend:
        case BackendKind.LOCAL:
            outcome = await use_case.local(request, bundle(holder, password=PASSWORD), Secret(PASSWORD))
        case _:
            assert_type(backend, Literal[BackendKind.REMOTE])
            outcome = await use_case.remote(request, Secret("CREDENTIAL-SENTINEL"))
    return outcome.view


def document(clock: Clock, *, signed_at: datetime | None = None, body: bytes = DOCUMENT) -> XmlToSign:
    """``DOCUMENT`` to sign in test mode, stating ``signed_at`` (the clock's time by default)."""
    return XmlToSign(
        tenant=IN_TEST,
        profile=PROFILE,
        document=body,
        signed_at=clock() if signed_at is None else signed_at,
        reference=REFERENCE,
    )


def seed() -> SeedToSign:
    """``SEED`` to sign in test mode, for DGII's host."""
    return SeedToSign(tenant=IN_TEST, profile=PROFILE, seed=SEED, audience=AUDIENCE)


def assert_signed(signed: Signed, view: CertificateView) -> None:
    """``signed`` holds an enveloped signature made with ``view``'s certificate."""
    assert signed.certificate == view.id
    assert b"<Signature xmlns=" in signed.document
    with pytest.raises(dsig.DocumentSignedError):
        _ = dsig.prepare(signed.document)


# ── signing ─────────────────────────────────────────────────────────────────────────────────────────────────


async def test_a_document_is_signed_then_audited_then_answered(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    view = await install(units, custody, clock, holder)
    before = len(units.audits)

    signed = await signing(units, custody, clock).sign_xml("worker", document(clock))

    assert_signed(signed, view)
    assert units.opened[2:] == [IN_TEST, IN_TEST]  # after the import's two
    [audited] = units.audits[before:]
    assert (audited.scope, audited.actor.name, audited.action, audited.subject) == (
        IN_TEST,
        "system:test",
        "certificate.sign_xml",
        view.id,
    )
    assert (audited.before, audited.after) == (None, None)
    assert audited.detail == {
        "caller": "worker",
        "mode": "test",
        "profile": "xmldsig-enveloped-rsa-sha256",
        "document_sha256": hashlib.sha256(DOCUMENT).hexdigest(),
        "reference": REFERENCE,
        "backend": "local",
    }


async def test_a_seed_is_signed_and_audited_with_its_audience_and_no_reference(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    view = await install(units, custody, clock, holder)

    signed = await signing(units, custody, clock).sign_seed("receiver", seed())

    assert_signed(signed, view)
    audited = units.audits[-1]
    assert (audited.action, audited.subject) == ("certificate.sign_seed", view.id)
    assert audited.detail == {
        "caller": "receiver",
        "mode": "test",
        "profile": "xmldsig-enveloped-rsa-sha256",
        "document_sha256": hashlib.sha256(SEED).hexdigest(),
        "audience": AUDIENCE,
        "backend": "local",
    }


async def test_a_seed_is_signed_at_the_signer_s_own_time(
    units: Store, custody: Custody, clock: Moving, holder: Issued
) -> None:
    view = await install(units, custody, clock, holder)
    clock.now = view.not_after - SKEW / 2  # inside the validity, but no seed signed now would be covered

    with pytest.raises(NotCoveredError):
        await signing(units, custody, clock).sign_seed("worker", seed())


async def test_a_remote_certificate_signs_through_the_entity_which_sees_a_digest(
    units: Store, custody: Custody, clock: Clock, entity_holder: Issued, entity: Entity
) -> None:
    view = await install(units, custody, clock, entity_holder, backend=BackendKind.REMOTE)

    signed = await signing(units, custody, clock).sign_xml("worker", document(clock))

    assert_signed(signed, view)
    [digest] = entity.digests
    assert len(digest) == hashlib.sha256().digest_size
    assert units.audits[-1].detail["backend"] == "remote"


async def test_without_an_active_certificate_for_the_mode_nothing_is_signed(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    _ = await install(units, custody, clock, holder)
    before = len(units.audits)
    in_live = XmlToSign(
        tenant=IN_LIVE, profile=PROFILE, document=DOCUMENT, signed_at=clock(), reference=REFERENCE
    )

    with pytest.raises(SigningCertificateMissingError):
        await signing(units, custody, clock).sign_xml("worker", in_live)

    assert len(units.audits) == before
    assert units.opened[-1] == IN_LIVE


async def test_the_window_and_the_clock_are_checked_before_custody_is_asked(
    units: Store, custody: Custody, clock: Moving, holder: Issued, kms: Kms
) -> None:
    view = await install(units, custody, clock, holder)
    use_case = signing(units, custody, clock)
    unwraps, audits = kms.unwraps, len(units.audits)

    with pytest.raises(ClockSkewError):
        await use_case.sign_xml("worker", document(clock, signed_at=clock() - SKEW - timedelta(seconds=1)))
    clock.now = view.not_after - SKEW / 2
    with pytest.raises(NotCoveredError):
        await use_case.sign_xml("worker", document(clock))

    assert (kms.unwraps, len(units.audits)) == (unwraps, audits)


async def test_a_document_that_cannot_be_signed_is_refused_with_its_code(
    units: Store, custody: Custody, clock: Clock, holder: Issued, kms: Kms
) -> None:
    _ = await install(units, custody, clock, holder)
    unwraps, audits = kms.unwraps, len(units.audits)

    with pytest.raises(MalformedDocumentError) as refused:
        await signing(units, custody, clock).sign_xml(
            "worker", document(clock, body=b"<ECF><DOCUMENT-SENTINEL")
        )

    assert refused.value.kind is ErrorKind.INVALID
    assert (kms.unwraps, len(units.audits)) == (unwraps, audits)


async def test_every_signature_unwraps_its_data_key(
    units: Store, custody: Custody, clock: Clock, holder: Issued, kms: Kms
) -> None:
    _ = await install(units, custody, clock, holder)
    use_case = signing(units, custody, clock)
    unwraps = kms.unwraps

    for _ in range(3):
        _ = await use_case.sign_xml("worker", document(clock))

    assert kms.unwraps == unwraps + 3


async def test_the_signing_pool_bounds_the_signatures_made_at_once(
    units: Store, custody: Pool, clock: Clock, holder: Issued
) -> None:
    _ = await install(units, custody, clock, holder)
    use_case = signing(units, custody, clock, concurrency=2)
    before = len(units.audits)

    signed = await asyncio.gather(*(use_case.sign_xml("worker", document(clock)) for _ in range(10)))

    assert len(signed) == 10
    assert custody.most_in_flight == 2
    assert len(units.audits) == before + 10


async def test_a_signature_whose_audit_cannot_be_written_is_never_answered(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    _ = await install(units, custody, clock, holder)
    units.refuse_audit = True
    answered: list[Signed] = []

    with pytest.raises(LedgerError) as failed:
        answered.append(await signing(units, custody, clock).sign_xml("worker", document(clock)))

    assert failed.value.code == "test_audit_write_refused"
    assert answered == []


async def test_a_key_that_is_not_its_certificate_s_is_custody_damaged(
    units: Store, custody: Pool, clock: Clock, holder: Issued
) -> None:
    _ = await install(units, custody, clock, holder)
    custody.damaged = True
    audits = len(units.audits)

    with pytest.raises(CustodyDamagedError) as damaged:
        await signing(units, custody, clock).sign_xml("worker", document(clock))

    assert (damaged.value.kind, damaged.value.code) == (ErrorKind.INTERNAL, "signer_custody_damaged")
    assert isinstance(damaged.value.__cause__, dsig.SigningPairError)
    assert len(units.audits) == audits


@final
class RetiringMidway(Custody):
    """Custody that sees the certificate retired while it signs (a retirement committed meanwhile)."""

    __slots__ = ("_custody", "_retire")

    def __init__(self, custody: Custody, retire: Retiring, certificate: CertificateView) -> None:
        self._custody = custody
        self._retire = (retire, certificate)

    @override
    async def admit_local(self, bundle: bytes, password: Secret, *, seal_for: SealContext) -> Admitted:
        return await self._custody.admit_local(bundle, password, seal_for=seal_for)

    @override
    async def admit_remote(self, credential: Secret, *, seal_for: SealContext) -> Admitted:
        return await self._custody.admit_remote(credential, seal_for=seal_for)

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
        retire, certificate = self._retire
        signed = await self._custody.sign(
            material, backend=backend, prepared=prepared, certificate_der=certificate_der, seal_for=seal_for
        )
        _ = await retire.retire(OWNER, certificate.id)
        return signed


async def test_a_certificate_retired_while_it_signs_still_audits_its_signature(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    view = await install(units, custody, clock, holder)
    midway = RetiringMidway(custody, Retiring(units), view)

    signed = await signing(units, midway, clock).sign_xml("worker", document(clock))

    assert_signed(signed, view)
    assert [audited.action for audited in units.audits[-2:]] == ["certificate.retire", "certificate.sign_xml"]


async def test_no_document_reaches_an_error_a_log_an_audit_row_or_an_event(
    units: Store, custody: Custody, clock: Moving, holder: Issued
) -> None:
    view = await install(units, custody, clock, holder)
    use_case = signing(units, custody, clock)
    shown: list[str] = []
    with capture_logs() as logs:
        signed = await use_case.sign_xml("worker", document(clock))
        _ = await use_case.sign_seed("worker", seed())
        for refused in (
            document(clock, body=b"<ECF><DOCUMENT-SENTINEL"),
            document(clock, signed_at=view.not_after),
        ):
            try:
                await use_case.sign_xml("worker", refused)
            except LedgerError as error:
                shown.extend((str(error), repr(error), repr(error.__cause__), repr(error.__context__)))
    shown.extend((repr(signed), repr(logs), repr(document(clock)), repr(seed())))
    shown.extend(repr(audited) for audited in units.audits)
    shown.extend(repr(payload_of(event)) for event in units.events)

    assert len(shown) > len(units.audits)
    for text in shown:
        assert "DOCUMENT-SENTINEL" not in text
        assert "SEED-SENTINEL" not in text
        assert PASSWORD not in text


# ── the facade ──────────────────────────────────────────────────────────────────────────────────────────────


def facade(units: SignerUnits, custody: Custody, clock: Clock) -> SignerFacade:
    """The facade over the stand-ins."""
    return SignerFacade(signing=signing(units, custody, clock), describing=Describing(units))


def test_every_entry_point_of_the_facade_is_guarded(units: Store, custody: Custody, clock: Clock) -> None:
    require_facades(facade(units, custody, clock))


async def test_the_facade_signs_in_process_as_the_call_s_actor(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    view = await install(units, custody, clock, holder)
    signer = facade(units, custody, clock)

    assert_signed(await signer.sign_xml(CALL, document(clock)), view)
    assert_signed(await signer.sign_seed(CALL, seed()), view)

    assert [(audited.actor.name, audited.actor.call_id) for audited in units.audits[-2:]] == [
        ("system:test-signing", CALL.call_id)
    ] * 2
    assert {audited.detail["caller"] for audited in units.audits[-2:]} == {IN_PROCESS}


async def test_the_facade_answers_the_port_s_errors(
    units: Store, custody: Custody, clock: Moving, holder: Issued
) -> None:
    signer = facade(units, custody, clock)
    with pytest.raises(SigningCertificateMissingError):
        await signer.sign_xml(CALL, document(clock))
    view = await install(units, custody, clock, holder)
    with pytest.raises(SignatureRefusedError) as refused:
        await signer.sign_xml(CALL, document(clock, body=b"<ECF><broken"))
    assert refused.value.reason == "xml_document_malformed"
    with pytest.raises(SigningClockSkewError):
        await signer.sign_xml(CALL, document(clock, signed_at=clock() + SKEW * 2))
    clock.now = view.not_after - SKEW / 2
    with pytest.raises(SigningCertificateNotValidError):
        await signer.sign_seed(CALL, seed())


async def test_the_facade_answers_a_kms_that_cannot_be_reached_as_the_signer_unavailable(
    units: Store, custody: Custody, clock: Clock, holder: Issued, kms: Kms
) -> None:
    _ = await install(units, custody, clock, holder)
    kms.available = False

    with pytest.raises(SignerUnavailableError):
        await facade(units, custody, clock).sign_xml(CALL, document(clock))


async def test_the_facade_lets_a_defect_pass_as_it_is(
    units: Store, custody: Pool, clock: Clock, holder: Issued
) -> None:
    _ = await install(units, custody, clock, holder)
    custody.damaged = True

    with pytest.raises(CustodyDamagedError):
        await facade(units, custody, clock).sign_xml(CALL, document(clock))


async def test_the_facade_tells_the_validity_of_the_certificate_serving_a_tenant(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    signer = facade(units, custody, clock)
    view = await install(units, custody, clock, holder)

    assert await signer.certificate_validity(CALL, IN_TEST) == CertificateValidity(
        certificate=view.id, not_before=view.not_before, not_after=view.not_after
    )
    assert await signer.certificate_validity(CALL, IN_LIVE) is None
    with pytest.raises(InvalidValueError):
        _ = await signer.certificate_validity(CALL, OWNER)


async def test_the_facade_lists_the_certificates_serving_a_mode_for_the_call_s_organization(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    signer = facade(units, custody, clock)
    view = await install(units, custody, clock, holder)

    assert await signer.certificates(CALL, COMPANY, Mode.TEST) == (view,)
    assert await signer.certificates(CALL, COMPANY, Mode.LIVE) == ()
    other = SystemCall(reason="test", scope=OrganizationScope(OrganizationId(new_id()), None))
    assert await signer.certificates(other, COMPANY, Mode.TEST) == ()
    with pytest.raises(InvalidValueError):
        _ = await signer.certificates(SystemCall(reason="test", scope=None), COMPANY, Mode.TEST)


@pytest.mark.parametrize(
    ("code", "kind", "expected"),
    [
        ("signer_window_not_covered", ErrorKind.CONFLICT, SigningCertificateNotValidError),
        ("signer_clock_skew", ErrorKind.CONFLICT, SigningClockSkewError),
        ("certificate_retired", ErrorKind.CONFLICT, SigningCertificateMissingError),
        ("signing_certificate_missing", ErrorKind.CONFLICT, SigningCertificateMissingError),
        ("xml_document_signed", ErrorKind.INVALID, SignatureRefusedError),
        ("signature_refused", ErrorKind.INVALID, SignatureRefusedError),
        ("entity_refused", ErrorKind.CONFLICT, SigningCertificateNotValidError),
        ("kms_unavailable", ErrorKind.UNAVAILABLE, SignerUnavailableError),
        ("entity_unavailable", ErrorKind.UNAVAILABLE, SignerUnavailableError),
    ],
)
def test_the_signer_s_codes_become_the_port_s_errors(
    code: str, kind: ErrorKind, expected: type[LedgerError]
) -> None:
    error = port_error(code, kind)

    assert isinstance(error, expected)
    if isinstance(error, SignatureRefusedError):
        assert error.reason == code


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("signer_custody_damaged", ErrorKind.INTERNAL),
        ("xml_signing_pair_refused", ErrorKind.INVALID),
        ("xml_signing_key_not_rsa", ErrorKind.INVALID),
        ("xml_document_signed", ErrorKind.INTERNAL),
        ("kms_refused", ErrorKind.INTERNAL),
        ("certificate_not_found", ErrorKind.NOT_FOUND),
    ],
)
def test_any_other_code_is_not_the_port_s(code: str, kind: ErrorKind) -> None:
    assert port_error(code, kind) is None
