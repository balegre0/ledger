"""signer's use cases over in-memory ports: importing, describing, retiring, notices and deactivations.

What each stores, announces, audits and enqueues, in how many units, and what it refuses with nothing kept
(plan Decisions 8, 9 and 10; Review Focus 3).
"""

import hashlib
from datetime import timedelta
from typing import Final, Protocol
lazy from collections.abc import Mapping, Sequence
lazy from datetime import datetime
lazy from uuid import UUID

import pytest
from cryptography.hazmat.primitives import hashes
from structlog.testing import capture_logs

from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.audit import current_actor
from ledger_platform.data.codec import payload_of
from ledger_platform.data.idempotency import InvalidIdempotencyKeyError
from ledger_platform.http.idempotency import IdempotencyKeyReusedError
from ledger_signer.api import BackendNotOfferedError, CertificateNotFoundError
from ledger_signer.application.certificates import (
    Describing,
    Retiring,
    retire_company,
    retired_event,
    view_of,
)
from ledger_signer.application.importing import Importing, ImportRequest, fingerprint_of
from ledger_signer.application.notices import EXPIRY_NOTICE, Notices
from ledger_signer.application.ports import (
    ActiveCertificateTakenError,
    BundleIncompleteError,
    BundleUnreadableError,
    DataKey,
    EntityRefusedError,
    EntityUnavailableError,
    KmsRefusedError,
    KmsUnavailableError,
    SealContext,
    SealedMaterial,
    Secret,
    SignerRules,
    SignerUnits,
)
from ledger_signer.domain.certificates import (
    BackendKind,
    Certificate,
    CertificateId,
    CertificateStatus,
    RetiredReason,
)
from ledger_signer.domain.errors import CertificateExpiredError, CertificateNotForSigningError
from ledger_signer.events import CertificateExpiring, CertificateInstalled, CertificateRetired
from ledger_testing.pki import bundle, distinguished_name
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.errors import LedgerError
lazy from ledger_signer.application.ports import CertificationEntity, Custody, ImportClaim, KeyManagement
lazy from ledger_signer.events import CertificateEvent
lazy from ledger_testing.pki import Authority, Issued

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
OWNER: Final = Tenant(ORGANIZATION, COMPANY, None)
IN_TEST: Final = Tenant(ORGANIZATION, COMPANY, Mode.TEST)
IN_LIVE: Final = Tenant(ORGANIZATION, COMPANY, Mode.LIVE)
TEST: Final = frozenset({Mode.TEST})
LIVE: Final = frozenset({Mode.LIVE})
BOTH: Final = frozenset({Mode.TEST, Mode.LIVE})
PASSWORD: Final = "PASSWORD-SENTINEL"
CREDENTIAL: Final = "CREDENTIAL-SENTINEL"
KEY: Final = "import-1"
SKEW: Final = timedelta(seconds=300)
NOTICE_DAYS: Final = (30, 14, 7, 1, 0)
MATERIAL: Final = SealedMaterial(sealed=b"sealed", wrapped_data_key=b"wrapped", kms_key_id="memory:1")
_LENGTH_BYTES: Final = 8


class Audited(Protocol):
    """An audit row the stand-in units kept."""

    @property
    def scope(self) -> Tenant: ...
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


class Enqueued(Protocol):
    """A job the stand-in units enqueued."""

    @property
    def scope(self) -> Tenant: ...
    @property
    def kind(self) -> str: ...
    @property
    def key(self) -> str: ...
    @property
    def run_at(self) -> datetime: ...
    @property
    def payload(self) -> Mapping[str, str | int]: ...


class Store(SignerUnits, Protocol):
    """The stand-in units, and what they committed."""

    rows: dict[CertificateId, Certificate]
    materials: dict[CertificateId, SealedMaterial]
    events: list[CertificateEvent]
    audits: list[Audited]
    jobs: list[Enqueued]
    claims: list[ImportClaim]
    opened: list[Tenant]
    locked: list[bool]
    rival: Certificate | None

    def seed(self, certificate: Certificate, material: SealedMaterial) -> None: ...


class Kms(Protocol):
    """The stand-in KMS's switch."""

    available: bool


class Moving(Protocol):
    """The stand-in clock."""

    now: datetime

    def __call__(self) -> datetime: ...


class Entity(Protocol):
    """The stand-in entity's switches."""

    available: bool
    refusing: bool


def rules(*backends: BackendKind) -> SignerRules:
    """The deployment's rules, offering ``backends`` (the local one when none is named)."""
    return SignerRules(
        backends=frozenset(backends or (BackendKind.LOCAL,)),
        skew=SKEW,
        concurrency=4,
        notice_days=NOTICE_DAYS,
    )


def importing(units: SignerUnits, custody: Custody, clock: Clock, *backends: BackendKind) -> Importing:
    """The import use case over the stand-ins."""
    return Importing(units=units, custody=custody, rules=rules(*backends), clock=clock)


def request(modes: frozenset[Mode] = TEST, *, mode: Mode = Mode.TEST, key: str = KEY) -> ImportRequest:
    """An import, in ``mode``, of a certificate for ``modes``."""
    return ImportRequest(tenant=Tenant(ORGANIZATION, COMPANY, mode), modes=modes, idempotency_key=key)


def certificate(
    modes: frozenset[Mode] = TEST, *, company: CompanyId = COMPANY, at: datetime | None = None
) -> Certificate:
    """An active certificate of ``company`` serving ``modes``, as an earlier import kept it."""
    installed = at or datetime.fromisoformat("2026-10-01T00:00:00+00:00")
    return Certificate(
        id=CertificateId(new_id()),
        tenant=Tenant(ORGANIZATION, company, None),
        modes=modes,
        backend=BackendKind.LOCAL,
        status=CertificateStatus.ACTIVE,
        der=b"der",
        fingerprint_sha256=bytes(32),
        serial_number="1f",
        subject="CN=Earlier",
        issuer="CN=Authority",
        not_before=installed - timedelta(days=1),
        not_after=installed + timedelta(days=365),
        installed_at=installed,
        retired_at=None,
        retired_reason=None,
        version=1,
    )


def of_type[E: CertificateEvent](events: Sequence[CertificateEvent], kind: type[E]) -> list[E]:
    """The events of ``kind``, in order."""
    return [event for event in events if isinstance(event, kind)]


def expected_hash(modes: str, backend: str, source: bytes, *, mode: str = "test") -> bytes:
    """Decision 9's fingerprint spelled out: the length-prefixed company, modes, backend and source."""
    digest = hashlib.sha256()
    for part in (str(COMPANY).encode(), mode.encode(), modes.encode(), backend.encode(), source):
        digest.update(len(part).to_bytes(_LENGTH_BYTES, "big"))
        digest.update(part)
    return digest.digest()


# ── importing ───────────────────────────────────────────────────────────────────────────────────────────────


async def test_a_local_import_installs_audits_announces_and_plans_its_notices(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    sent = bundle(holder, password=PASSWORD)

    outcome = await importing(units, custody, clock).local(request(), sent, Secret(PASSWORD))

    view = outcome.view
    assert not outcome.replayed
    assert (view.organization, view.company, view.modes) == (ORGANIZATION, COMPANY, TEST)
    assert (view.backend, view.status, view.version) == (BackendKind.LOCAL, CertificateStatus.ACTIVE, 1)
    assert view.fingerprint_sha256 == holder.certificate.fingerprint(hashes.SHA256())
    assert view.subject == holder.certificate.subject.rfc4514_string()
    assert (view.not_before, view.not_after) == (
        holder.certificate.not_valid_before_utc,
        holder.certificate.not_valid_after_utc,
    )
    assert (view.installed_at, view.retired_at, view.retired_reason) == (clock(), None, None)
    assert units.opened == [IN_TEST, IN_TEST]  # the key looked up, then claimed
    assert units.locked == [True]
    kept = units.rows[view.id]
    assert view_of(kept) == view
    assert units.materials[view.id].kms_key_id == "memory:1"
    [installed] = units.events
    assert installed == CertificateInstalled(
        scope=OWNER,
        subject=view.id,
        aggregate_version=1,
        certificate=view.id,
        modes=(Mode.TEST,),
        backend="local",
        not_after=view.not_after,
        replaces=(),
    )
    [audited] = units.audits
    assert (audited.scope, audited.action, audited.subject, audited.before) == (
        IN_TEST,
        "certificate.install",
        view.id,
        None,
    )
    assert audited.after is not None
    assert audited.detail == {
        "backend": "local",
        "modes": "test",
        "fingerprint_sha256": view.fingerprint_sha256.hex(),
        "replaces": "",
    }


async def test_its_notices_are_jobs_keyed_by_certificate_and_threshold(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    outcome = await importing(units, custody, clock).local(
        request(), bundle(holder, password=PASSWORD), Secret(PASSWORD)
    )

    not_after = outcome.view.not_after
    assert [(job.kind, job.key, job.run_at, job.payload, job.scope) for job in units.jobs] == [
        (
            EXPIRY_NOTICE,
            f"{outcome.view.id}:{days}",
            not_after - timedelta(days=days),
            {"certificate": str(outcome.view.id), "threshold_days": days},
            IN_TEST,
        )
        for days in NOTICE_DAYS
    ]


async def test_a_remote_import_installs_the_certificate_the_entity_signs_with(
    units: Store, custody: Custody, clock: Clock, entity_holder: Issued
) -> None:
    outcome = await importing(units, custody, clock, BackendKind.LOCAL, BackendKind.REMOTE).remote(
        request(BOTH, mode=Mode.LIVE), Secret(CREDENTIAL)
    )

    assert (outcome.view.backend, outcome.view.modes) == (BackendKind.REMOTE, BOTH)
    assert outcome.view.fingerprint_sha256 == entity_holder.certificate.fingerprint(hashes.SHA256())
    [installed] = of_type(units.events, CertificateInstalled)
    assert (installed.backend, installed.modes) == ("remote", (Mode.TEST, Mode.LIVE))
    assert [claim.request_hash for claim in units.claims] == [
        expected_hash("live,test", "remote", hashlib.sha256(CREDENTIAL.encode()).digest(), mode="live")
    ]


async def test_a_backend_the_deployment_does_not_offer_is_refused_before_anything(
    units: Store, custody: Custody, clock: Clock
) -> None:
    with pytest.raises(BackendNotOfferedError) as refused:
        await importing(units, custody, clock).remote(request(), Secret(CREDENTIAL))

    assert refused.value.kind is ErrorKind.INVALID
    assert units.opened == []


async def test_the_import_claims_its_key_with_the_fingerprint_of_decision_9(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    sent = bundle(holder, password=PASSWORD)

    _ = await importing(units, custody, clock).local(request(BOTH), sent, Secret(PASSWORD))

    [claim] = units.claims
    assert (claim.principal, claim.key) == (current_actor().name, KEY)
    assert claim.request_hash == expected_hash("live,test", "local", hashlib.sha256(sent).digest())


async def test_the_same_key_replays_the_first_answer_and_installs_nothing_more(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    use_case = importing(units, custody, clock)
    sent = bundle(holder, password=PASSWORD)
    first = await use_case.local(request(), sent, Secret(PASSWORD))

    again = await use_case.local(request(), sent, Secret(PASSWORD))

    assert again.replayed
    assert again.view == first.view
    assert len(units.rows) == 1
    assert len(units.events) == 1
    assert len(units.audits) == 1


async def test_another_request_under_a_used_key_is_refused(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    use_case = importing(units, custody, clock)
    sent = bundle(holder, password=PASSWORD)
    _ = await use_case.local(request(), sent, Secret(PASSWORD))

    with pytest.raises(IdempotencyKeyReusedError):
        await use_case.local(request(BOTH), sent, Secret(PASSWORD))

    assert len(units.rows) == 1


async def test_the_same_key_in_the_other_mode_is_another_key(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    use_case = importing(units, custody, clock)
    sent = bundle(holder, password=PASSWORD)
    first = await use_case.local(request(), sent, Secret(PASSWORD))

    other = await use_case.local(request(mode=Mode.LIVE), sent, Secret(PASSWORD))

    assert not other.replayed
    assert other.view.id != first.view.id


@pytest.mark.parametrize("key", ["", "two words", "é", "k" * 256])
async def test_a_key_that_is_not_visible_ascii_is_refused_before_anything(
    units: Store, custody: Custody, clock: Clock, holder: Issued, key: str
) -> None:
    with pytest.raises(InvalidIdempotencyKeyError):
        await importing(units, custody, clock).local(
            request(key=key), bundle(holder, password=PASSWORD), Secret(PASSWORD)
        )

    assert units.opened == []


async def test_installing_takes_the_modes_of_the_certificates_before_it_in_one_unit(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    earlier, live = certificate(BOTH), certificate(LIVE, company=CompanyId(new_id()))
    units.seed(earlier, MATERIAL)
    units.seed(live, MATERIAL)
    use_case = importing(units, custody, clock)
    sent = bundle(holder, password=PASSWORD)

    for_test = await use_case.local(request(TEST, key="one"), sent, Secret(PASSWORD))

    assert units.rows[earlier.id].modes == LIVE
    assert units.rows[earlier.id].is_active
    assert units.rows[live.id] == live
    assert of_type(units.events, CertificateInstalled)[0].replaces == (earlier.id,)
    assert of_type(units.events, CertificateRetired) == []

    for_both = await use_case.local(request(BOTH, key="two"), sent, Secret(PASSWORD))

    assert len(units.opened) == 4
    retired = [units.rows[earlier.id], units.rows[for_test.view.id]]
    assert [(row.status, row.retired_reason) for row in retired] == [
        (CertificateStatus.RETIRED, RetiredReason.REPLACED)
    ] * 2
    assert earlier.id not in units.materials
    assert for_test.view.id not in units.materials
    second = units.events[1:]
    assert of_type(second, CertificateInstalled)[0].replaces == (earlier.id, for_test.view.id)
    assert {(event.certificate, event.reason) for event in of_type(second, CertificateRetired)} == {
        (earlier.id, "replaced"),
        (for_test.view.id, "replaced"),
    }
    assert units.audits[-1].detail["replaces"] == f"{earlier.id},{for_test.view.id}"
    assert units.rows[for_both.view.id].modes == BOTH


async def test_an_install_raced_by_another_is_retried_once_and_replaces_it(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    rival = certificate(TEST)
    units.rival = rival

    outcome = await importing(units, custody, clock).local(
        request(), bundle(holder, password=PASSWORD), Secret(PASSWORD)
    )

    assert units.opened == [IN_TEST, IN_TEST, IN_TEST]
    assert units.rows[rival.id].retired_reason is RetiredReason.REPLACED
    assert units.rows[outcome.view.id].is_active
    [installed] = of_type(units.events, CertificateInstalled)
    assert installed.replaces == (rival.id,)


async def test_a_wrong_password_is_refused_and_nothing_is_kept(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    with pytest.raises(BundleUnreadableError):
        await importing(units, custody, clock).local(
            request(), bundle(holder, password="another"), Secret(PASSWORD)
        )

    assert (units.rows, units.claims, units.events, units.audits, units.jobs) == ({}, [], [], [], [])


async def test_a_certificate_the_domain_refuses_is_refused_and_nothing_is_kept(
    units: Store, custody: Custody, clock: Clock, authority: Authority
) -> None:
    clock_now = clock()
    expired = authority.issue(
        distinguished_name(),
        not_before=clock_now - timedelta(days=30),
        not_after=clock_now - timedelta(days=1),
    )
    use_case = importing(units, custody, clock)

    with pytest.raises(CertificateExpiredError):
        await use_case.local(request(), bundle(expired, password=PASSWORD), Secret(PASSWORD))
    with pytest.raises(CertificateNotForSigningError):
        await use_case.local(request(), bundle(authority.issued, password=PASSWORD), Secret(PASSWORD))

    assert (units.rows, units.claims, units.events, units.audits, units.jobs) == ({}, [], [], [], [])


async def test_a_kms_that_fails_keeps_nothing(
    units: Store, custody: Custody, clock: Clock, holder: Issued, kms: Kms
) -> None:
    kms.available = False

    with pytest.raises(KmsUnavailableError):
        await importing(units, custody, clock).local(
            request(), bundle(holder, password=PASSWORD), Secret(PASSWORD)
        )

    assert (units.rows, units.claims, units.events, units.audits, units.jobs) == ({}, [], [], [], [])


async def test_an_entity_that_fails_keeps_nothing(
    units: Store, custody: Custody, clock: Clock, entity: Entity
) -> None:
    use_case = importing(units, custody, clock, BackendKind.REMOTE)
    entity.refusing = True
    with pytest.raises(EntityRefusedError):
        await use_case.remote(request(), Secret(CREDENTIAL))
    entity.available = False
    with pytest.raises(EntityUnavailableError):
        await use_case.remote(request(), Secret(CREDENTIAL))

    assert (units.rows, units.claims, units.events, units.audits, units.jobs) == ({}, [], [], [], [])


async def test_a_replay_needs_no_password_no_kms_and_no_valid_certificate(
    units: Store, custody: Custody, clock: Moving, holder: Issued, kms: Kms
) -> None:
    use_case = importing(units, custody, clock)
    sent = bundle(holder, password=PASSWORD)
    first = await use_case.local(request(), sent, Secret(PASSWORD))
    opened = len(units.opened)
    kms.available = False
    clock.now = first.view.not_after + timedelta(days=1)

    again = await use_case.local(request(), sent, Secret("not the password"))

    assert (again.replayed, again.view) == (True, first.view)
    assert units.opened[opened:] == [IN_TEST]  # the look-up's unit alone
    assert len(units.rows) == 1


async def test_a_remote_replay_does_not_ask_the_entity(
    units: Store, custody: Custody, clock: Clock, entity: Entity
) -> None:
    use_case = importing(units, custody, clock, BackendKind.REMOTE)
    first = await use_case.remote(request(), Secret(CREDENTIAL))
    entity.available = False

    again = await use_case.remote(request(), Secret(CREDENTIAL))

    assert (again.replayed, again.view) == (True, first.view)


async def test_another_request_under_a_used_key_is_refused_before_custody(
    units: Store, custody: Custody, clock: Clock, holder: Issued, kms: Kms
) -> None:
    use_case = importing(units, custody, clock)
    sent = bundle(holder, password=PASSWORD)
    _ = await use_case.local(request(), sent, Secret(PASSWORD))
    kms.available = False

    with pytest.raises(IdempotencyKeyReusedError):
        await use_case.local(request(BOTH), sent, Secret(PASSWORD))


async def test_a_first_import_with_a_wrong_password_is_refused_then_one_with_the_right_one_installs(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    use_case = importing(units, custody, clock)
    sent = bundle(holder, password=PASSWORD)
    with pytest.raises(BundleUnreadableError):
        await use_case.local(request(), sent, Secret("not the password"))

    installed = await use_case.local(request(), sent, Secret(PASSWORD))

    assert not installed.replayed
    assert list(units.rows) == [installed.view.id]


async def test_the_stand_ins_refuse_a_call_out_from_inside_a_unit(
    units: Store, kms: KeyManagement, entity: CertificationEntity
) -> None:
    async with units.unit(IN_TEST):
        with pytest.raises(LedgerError, match="unit of work was open"):
            await kms.ready()
        with pytest.raises(LedgerError, match="unit of work was open"):
            _ = await entity.certificate(CREDENTIAL.encode())

    await kms.ready()


def test_an_import_names_a_mode_and_one_or_two_modes() -> None:
    with pytest.raises(InvalidValueError):
        _ = ImportRequest(tenant=OWNER, modes=TEST, idempotency_key=KEY)
    with pytest.raises(InvalidValueError):
        _ = ImportRequest(tenant=IN_TEST, modes=frozenset(), idempotency_key=KEY)


def test_the_fingerprint_ignores_the_order_of_the_modes_and_names_everything_else() -> None:
    source = hashlib.sha256(b"bundle").digest()
    base = fingerprint_of(request(BOTH), BackendKind.LOCAL, source)

    assert base == expected_hash("live,test", "local", source)
    assert fingerprint_of(request(TEST), BackendKind.LOCAL, source) != base
    assert fingerprint_of(request(BOTH, mode=Mode.LIVE), BackendKind.LOCAL, source) != base
    assert fingerprint_of(request(BOTH), BackendKind.REMOTE, source) != base
    assert fingerprint_of(request(BOTH), BackendKind.LOCAL, bytes(32)) != base
    elsewhere = ImportRequest(
        tenant=Tenant(ORGANIZATION, CompanyId(new_id()), Mode.TEST), modes=BOTH, idempotency_key=KEY
    )
    assert fingerprint_of(elsewhere, BackendKind.LOCAL, source) != base


# ── describing and retiring ─────────────────────────────────────────────────────────────────────────────────


async def test_describing_lists_the_mode_s_certificates_active_first(units: Store) -> None:
    older = certificate(TEST, at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"))
    retired = older.retired(RetiredReason.RETIRED, at=datetime.fromisoformat("2026-02-01T00:00:00+00:00"))
    active = certificate(BOTH, at=datetime.fromisoformat("2026-03-01T00:00:00+00:00"))
    live_only = certificate(LIVE)
    elsewhere = certificate(TEST, company=CompanyId(new_id()))
    for kept in (retired, active, live_only, elsewhere):
        units.seed(kept, MATERIAL)
    describing = Describing(units)

    assert await describing.certificates(IN_TEST, include_retired=False) == (view_of(active),)
    assert await describing.certificates(IN_TEST, include_retired=True) == (view_of(active), view_of(retired))
    assert await describing.active(IN_TEST) == view_of(active)
    assert await describing.active(Tenant(ORGANIZATION, elsewhere.tenant.company, Mode.LIVE)) is None
    assert units.opened == [
        IN_TEST,
        IN_TEST,
        IN_TEST,
        Tenant(ORGANIZATION, elsewhere.tenant.company, Mode.LIVE),
    ]


async def test_describing_asks_for_a_mode(units: Store) -> None:
    with pytest.raises(InvalidValueError):
        _ = await Describing(units).certificates(OWNER, include_retired=False)
    with pytest.raises(InvalidValueError):
        _ = await Describing(units).active(OWNER)


async def test_retiring_retires_for_every_mode_audits_and_announces(units: Store, clock: Clock) -> None:
    kept = certificate(BOTH)
    units.seed(kept, MATERIAL)

    view = await Retiring(units).retire(OWNER, kept.id)

    assert (view.status, view.retired_reason, view.retired_at, view.version) == (
        CertificateStatus.RETIRED,
        "retired",
        clock(),
        2,
    )
    assert kept.id not in units.materials
    assert units.locked == [True]
    assert units.opened == [OWNER]
    assert units.events == [
        CertificateRetired(
            scope=OWNER, subject=kept.id, aggregate_version=2, certificate=kept.id, reason="retired"
        )
    ]
    [audited] = units.audits
    assert (audited.action, audited.subject, audited.detail) == (
        "certificate.retire",
        kept.id,
        {"reason": "retired"},
    )
    assert audited.before is not None
    assert audited.after is not None
    assert audited.before != audited.after


async def test_retiring_a_retired_certificate_answers_it_unchanged(units: Store, clock: Clock) -> None:
    retired = certificate(TEST).retired(RetiredReason.REPLACED, at=clock())
    units.seed(retired, MATERIAL)

    view = await Retiring(units).retire(OWNER, retired.id)

    assert view == view_of(retired)
    assert (units.events, units.audits) == ([], [])


async def test_retiring_another_company_s_certificate_finds_none(units: Store) -> None:
    elsewhere = certificate(TEST, company=CompanyId(new_id()))
    units.seed(elsewhere, MATERIAL)

    with pytest.raises(CertificateNotFoundError):
        await Retiring(units).retire(OWNER, elsewhere.id)
    with pytest.raises(CertificateNotFoundError):
        await Retiring(units).retire(OWNER, CertificateId(new_id()))

    assert units.rows[elsewhere.id] == elsewhere


async def test_retiring_names_a_company_without_a_mode(units: Store) -> None:
    with pytest.raises(InvalidValueError):
        _ = await Retiring(units).retire(IN_TEST, CertificateId(new_id()))


async def test_a_deactivated_company_has_every_active_certificate_retired(units: Store, clock: Clock) -> None:
    test, live = certificate(TEST), certificate(LIVE)
    replaced = certificate(TEST).retired(RetiredReason.REPLACED, at=clock())
    elsewhere = certificate(TEST, company=CompanyId(new_id()))
    for kept in (test, live, replaced, elsewhere):
        units.seed(kept, MATERIAL)

    async with units.unit(OWNER) as unit:
        await retire_company(unit, COMPANY)

    assert {(event.certificate, event.reason) for event in of_type(units.events, CertificateRetired)} == {
        (test.id, "company_deactivated"),
        (live.id, "company_deactivated"),
    }
    assert [audited.detail for audited in units.audits] == [{"reason": "company_deactivated"}] * 2
    assert units.rows[replaced.id] == replaced
    assert units.rows[elsewhere.id] == elsewhere
    assert units.locked == [True]


def test_only_a_retired_certificate_is_announced_retired() -> None:
    with pytest.raises(InvalidValueError):
        _ = retired_event(certificate(TEST))


# ── notices ─────────────────────────────────────────────────────────────────────────────────────────────────


async def test_a_notice_of_an_active_certificate_announces_it_expires(units: Store) -> None:
    kept = certificate(TEST)
    units.seed(kept, MATERIAL)

    assert await Notices(units).notify(IN_TEST, kept.id, threshold_days=7)

    assert units.events == [
        CertificateExpiring(
            scope=OWNER,
            subject=kept.id,
            aggregate_version=1,
            certificate=kept.id,
            not_after=kept.not_after,
            threshold_days=7,
        )
    ]


async def test_a_notice_of_a_retired_or_unknown_certificate_announces_nothing(
    units: Store, clock: Clock
) -> None:
    retired = certificate(TEST).retired(RetiredReason.RETIRED, at=clock())
    units.seed(retired, MATERIAL)
    notices = Notices(units)

    assert not await notices.notify(IN_TEST, retired.id, threshold_days=7)
    assert not await notices.notify(IN_TEST, CertificateId(new_id()), threshold_days=7)

    assert units.events == []


# ── the ports' values ───────────────────────────────────────────────────────────────────────────────────────


def test_a_secret_is_never_shown() -> None:
    secret = Secret(PASSWORD)

    assert secret.reveal() == PASSWORD
    assert PASSWORD not in repr(secret)
    assert PASSWORD not in str(secret)
    assert PASSWORD not in f"{secret}"


def test_sealed_material_and_data_keys_never_show_their_bytes() -> None:
    material = SealedMaterial(sealed=b"SEALED-SENTINEL", wrapped_data_key=b"WRAPPED-SENTINEL", kms_key_id="k")
    data_key = DataKey(plaintext=b"PLAINTEXT-SENTINEL", wrapped=b"w", key_id="k")

    assert "SENTINEL" not in repr(material)
    assert "SENTINEL" not in repr(data_key)


def test_a_seal_context_names_the_purpose_and_the_certificate_s_owners() -> None:
    certificate_id = CertificateId(new_id())

    context = SealContext(organization=ORGANIZATION, company=COMPANY, certificate=certificate_id).context()

    assert context == {
        "purpose": "signer.custody",
        "organization": str(ORGANIZATION),
        "company": str(COMPANY),
        "certificate": str(certificate_id),
    }


@pytest.mark.parametrize(
    ("backends", "skew", "concurrency", "days"),
    [
        (frozenset[BackendKind](), SKEW, 4, NOTICE_DAYS),
        (frozenset({BackendKind.LOCAL}), timedelta(0), 4, NOTICE_DAYS),
        (frozenset({BackendKind.LOCAL}), SKEW, 0, NOTICE_DAYS),
        (frozenset({BackendKind.LOCAL}), SKEW, 4, (7, 7)),
        (frozenset({BackendKind.LOCAL}), SKEW, 4, (-1,)),
    ],
)
def test_rules_that_break_their_bounds_are_a_defect(
    backends: frozenset[BackendKind], skew: timedelta, concurrency: int, days: tuple[int, ...]
) -> None:
    with pytest.raises(InvalidValueError):
        _ = SignerRules(backends=backends, skew=skew, concurrency=concurrency, notice_days=days)


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (BundleUnreadableError(), ErrorKind.INVALID, "certificate_bundle_unreadable"),
        (BundleIncompleteError(), ErrorKind.INVALID, "certificate_bundle_incomplete"),
        (KmsUnavailableError(), ErrorKind.UNAVAILABLE, "kms_unavailable"),
        (KmsRefusedError(), ErrorKind.INTERNAL, "kms_refused"),
        (EntityUnavailableError(), ErrorKind.UNAVAILABLE, "entity_unavailable"),
        (EntityRefusedError(), ErrorKind.CONFLICT, "entity_refused"),
        (ActiveCertificateTakenError(), ErrorKind.CONFLICT, "certificate_install_raced"),
    ],
)
def test_the_ports_errors_have_their_kinds_and_codes(error: LedgerError, kind: ErrorKind, code: str) -> None:
    assert (error.kind, error.code) == (kind, code)
    assert str(error)


# ── secrets (Review Focus 3) ────────────────────────────────────────────────────────────────────────────────


async def test_no_password_or_credential_reaches_an_error_a_log_an_audit_row_or_an_event(
    units: Store, custody: Custody, clock: Clock, holder: Issued
) -> None:
    use_case = importing(units, custody, clock, BackendKind.LOCAL, BackendKind.REMOTE)
    shown: list[str] = []
    with capture_logs() as logs:
        try:
            await use_case.local(request(key="wrong"), bundle(holder, password="another"), Secret(PASSWORD))
        except BundleUnreadableError as error:
            shown.extend((str(error), repr(error), repr(error.__cause__), repr(error.__context__)))
        local = await use_case.local(
            request(key="local"), bundle(holder, password=PASSWORD), Secret(PASSWORD)
        )
        remote = await use_case.remote(request(key="remote"), Secret(CREDENTIAL))
    shown.extend((repr(local), repr(remote), repr(units.claims), repr(logs), repr(units.jobs)))
    shown.extend(repr(audited) for audited in units.audits)
    shown.extend(repr(payload_of(event)) for event in units.events)
    shown.extend(repr(material) for material in units.materials.values())

    for text in shown:
        assert PASSWORD not in text
        assert CREDENTIAL not in text
        assert "HOLDER-SENTINEL" not in text
