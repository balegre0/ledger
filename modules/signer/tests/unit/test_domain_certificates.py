"""The domain of certificates: what is admitted at import, and in which order it is refused.

How an installation takes modes from the certificates before it, and when a certificate may sign (plan
Decisions 5 and 8).
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict, Unpack
lazy from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_signer.domain.certificates import (
    MIN_RSA_BITS,
    BackendKind,
    Certificate,
    CertificateFacts,
    CertificateId,
    CertificateStatus,
    RetiredReason,
    admit,
    install,
)
from ledger_signer.domain.errors import (
    CertificateExpiredError,
    CertificateKeyMismatchError,
    CertificateKeyUnsupportedError,
    CertificateNotForSigningError,
    CertificateNotYetValidError,
    CertificateRetiredError,
    CertificateValidityTooShortError,
    ClockSkewError,
    NotCoveredError,
)
lazy from ledger_kernel.errors import LedgerError

NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
LATER: Final = NOW + timedelta(hours=1)
SKEW: Final = timedelta(seconds=300)
TICK: Final = timedelta(microseconds=1)
COMPANY: Final = Tenant(organization=OrganizationId(new_id()), company=CompanyId(new_id()), mode=None)
TEST: Final = frozenset({Mode.TEST})
LIVE: Final = frozenset({Mode.LIVE})
BOTH: Final = frozenset({Mode.TEST, Mode.LIVE})
SUBJECT: Final = "serialNumber=VATDO-101850043,CN=Holder"
DER: Final = bytes.fromhex("3082") + b"der"


YESTERDAY: Final = NOW - timedelta(days=1)
NEXT_YEAR: Final = NOW + timedelta(days=365)


class Changes(TypedDict, total=False):
    """What a test changes of the facts of a good certificate."""

    fingerprint_sha256: bytes
    not_before: datetime
    not_after: datetime
    rsa_bits: int | None
    may_sign: bool
    is_authority: bool
    matches_key: bool


def facts(**changes: Unpack[Changes]) -> CertificateFacts:
    """A certificate that may sign now (an RSA-2048 signing certificate of its key, valid a year), changed."""
    return CertificateFacts(
        der=DER,
        fingerprint_sha256=changes.get("fingerprint_sha256", bytes(range(32))),
        serial_number="1f",
        subject=SUBJECT,
        issuer="CN=Authority",
        not_before=changes.get("not_before", YESTERDAY),
        not_after=changes.get("not_after", NEXT_YEAR),
        rsa_bits=changes.get("rsa_bits", MIN_RSA_BITS),
        may_sign=changes.get("may_sign", True),
        is_authority=changes.get("is_authority", False),
        matches_key=changes.get("matches_key", True),
    )


def certificate(
    modes: frozenset[Mode] = TEST, *, not_before: datetime = YESTERDAY, not_after: datetime = NEXT_YEAR
) -> Certificate:
    read = facts(not_before=not_before, not_after=not_after)
    return Certificate(
        id=CertificateId(new_id()),
        tenant=COMPANY,
        modes=modes,
        backend=BackendKind.LOCAL,
        status=CertificateStatus.ACTIVE,
        der=read.der,
        fingerprint_sha256=read.fingerprint_sha256,
        serial_number=read.serial_number,
        subject=read.subject,
        issuer=read.issuer,
        not_before=read.not_before,
        not_after=read.not_after,
        installed_at=NOW,
        retired_at=None,
        retired_reason=None,
        version=1,
    )


# ── Admission ───────────────────────────────────────────────────────────────────


def test_a_certificate_that_may_sign_now_is_admitted() -> None:
    admit(facts(), now=NOW, skew=SKEW)
    admit(facts(rsa_bits=4096, not_before=NOW, not_after=NOW + SKEW), now=NOW, skew=SKEW)  # every edge


@pytest.mark.parametrize(
    ("changes", "refusal"),
    [
        ({"rsa_bits": None}, CertificateKeyUnsupportedError),
        ({"rsa_bits": MIN_RSA_BITS - 1}, CertificateKeyUnsupportedError),
        ({"is_authority": True}, CertificateNotForSigningError),
        ({"may_sign": False}, CertificateNotForSigningError),
        ({"matches_key": False}, CertificateKeyMismatchError),
        ({"not_before": NOW - timedelta(days=365), "not_after": NOW}, CertificateExpiredError),
        ({"not_before": NOW + TICK, "not_after": NOW + timedelta(days=365)}, CertificateNotYetValidError),
        (
            {"not_before": NOW - timedelta(days=1), "not_after": NOW + SKEW - TICK},
            CertificateValidityTooShortError,
        ),
    ],
)
def test_each_refusal(changes: Changes, refusal: type[LedgerError]) -> None:
    with pytest.raises(refusal):
        admit(facts(**changes), now=NOW, skew=SKEW)


def test_refusals_come_in_their_order() -> None:
    """Each certificate breaks every rule after the one it is refused by."""
    broken: Changes = {
        "matches_key": False,
        "not_before": NOW + timedelta(days=1),
        "not_after": NOW + timedelta(days=2),
    }
    order: list[tuple[Changes, type[LedgerError]]] = [
        ({"rsa_bits": 1024, "is_authority": True, **broken}, CertificateKeyUnsupportedError),
        ({"is_authority": True, **broken}, CertificateNotForSigningError),
        (broken, CertificateKeyMismatchError),
        (
            {"not_before": NOW - timedelta(days=2), "not_after": NOW - timedelta(days=1)},
            CertificateExpiredError,
        ),
        ({"not_before": NOW + TICK, "not_after": NOW + 2 * TICK}, CertificateNotYetValidError),
    ]
    for changes, refusal in order:
        with pytest.raises(refusal):
            admit(facts(**changes), now=NOW, skew=SKEW)


def test_import_refusals_are_the_client_s_and_name_nothing_of_the_certificate() -> None:
    refusals: list[LedgerError] = [
        CertificateKeyUnsupportedError(MIN_RSA_BITS),
        CertificateNotForSigningError(),
        CertificateKeyMismatchError(),
        CertificateExpiredError(),
        CertificateNotYetValidError(),
        CertificateValidityTooShortError(),
    ]
    assert [(error.kind, error.code) for error in refusals] == [
        (ErrorKind.INVALID, "certificate_key_unsupported"),
        (ErrorKind.INVALID, "certificate_not_for_signing"),
        (ErrorKind.INVALID, "certificate_key_mismatch"),
        (ErrorKind.INVALID, "certificate_expired"),
        (ErrorKind.INVALID, "certificate_not_yet_valid"),
        (ErrorKind.INVALID, "certificate_validity_too_short"),
    ]
    assert str(refusals[0]) == "the certificate's key is not an RSA key of at least 2048 bits"
    for error in refusals:
        assert not {"VATDO", "101850043", "Holder", "1f"} & set(str(error).split())


def test_facts_and_certificates_never_show_their_subject() -> None:
    assert "101850043" not in repr(facts())
    assert "101850043" not in repr(certificate())


@pytest.mark.parametrize(
    "changes",
    [
        {"fingerprint_sha256": bytes(31)},
        {"not_after": NOW - timedelta(days=1)},
        {"not_before": NOW.replace(tzinfo=None)},
        {"not_after": (NOW + timedelta(days=1)).replace(tzinfo=None)},
    ],
)
def test_facts_our_code_could_not_have_read_are_a_defect(changes: Changes) -> None:
    with pytest.raises(InvalidValueError):
        facts(**changes)


def test_admitting_needs_an_aware_clock_and_a_skew() -> None:
    with pytest.raises(InvalidValueError):
        admit(facts(), now=NOW.replace(tzinfo=None), skew=SKEW)
    with pytest.raises(InvalidValueError):
        admit(facts(), now=NOW, skew=-TICK)


# ── The aggregate ───────────────────────────────────────────────────────────────


def test_an_installed_certificate_is_active_for_its_modes() -> None:
    made = certificate(BOTH)
    assert (made.status, made.modes, made.version, made.installed_at) == (
        CertificateStatus.ACTIVE,
        BOTH,
        1,
        NOW,
    )
    assert (made.retired_at, made.retired_reason, made.backend) == (None, None, BackendKind.LOCAL)
    assert (made.der, made.fingerprint_sha256, made.subject, made.issuer, made.serial_number) == (
        b"0\x82der",
        bytes(range(32)),
        SUBJECT,
        "CN=Authority",
        "1f",
    )
    assert made.serves(Mode.TEST)
    assert made.serves(Mode.LIVE)
    assert not certificate(TEST).serves(Mode.LIVE)


DEFECTS: Final[list[Callable[[Certificate], Certificate]]] = [
    lambda made: replace(
        made, tenant=Tenant(organization=COMPANY.organization, company=COMPANY.company, mode=Mode.TEST)
    ),
    lambda made: replace(made, modes=frozenset()),
    lambda made: replace(made, version=0),
    lambda made: replace(made, fingerprint_sha256=b""),
    lambda made: replace(made, installed_at=NOW.replace(tzinfo=None)),
    lambda made: replace(made, retired_at=NOW),
    lambda made: replace(made, retired_reason=RetiredReason.RETIRED),
    lambda made: replace(made, status=CertificateStatus.RETIRED),
    lambda made: replace(made, status=CertificateStatus.RETIRED, retired_reason=RetiredReason.RETIRED),
    lambda made: replace(made, status=CertificateStatus.RETIRED, retired_at=NOW),
    lambda made: replace(
        made,
        status=CertificateStatus.RETIRED,
        retired_at=NOW.replace(tzinfo=None),
        retired_reason=RetiredReason.RETIRED,
    ),
]
"""Each makes, of an active certificate, one our code could not have made."""


@pytest.mark.parametrize("defect", DEFECTS)
def test_a_certificate_our_code_could_not_have_made_is_a_defect(
    defect: Callable[[Certificate], Certificate],
) -> None:
    with pytest.raises(InvalidValueError):
        defect(certificate())


def test_retiring_is_idempotent() -> None:
    once = certificate(BOTH).retired(RetiredReason.RETIRED, at=LATER)
    assert (once.status, once.retired_at, once.retired_reason, once.version) == (
        CertificateStatus.RETIRED,
        LATER,
        RetiredReason.RETIRED,
        2,
    )
    assert once.modes == BOTH  # kept for the record; it serves none
    assert not once.serves(Mode.TEST)
    assert once.retired(RetiredReason.COMPANY_DEACTIVATED, at=LATER + SKEW) is once


def test_losing_some_modes_keeps_a_certificate_active_and_losing_all_retires_it_replaced() -> None:
    both = certificate(BOTH)
    live_only = both.without(TEST, at=LATER)
    assert (live_only.modes, live_only.status, live_only.version) == (LIVE, CertificateStatus.ACTIVE, 2)
    gone = live_only.without(BOTH, at=LATER)
    assert (gone.status, gone.retired_reason, gone.retired_at, gone.modes, gone.version) == (
        CertificateStatus.RETIRED,
        RetiredReason.REPLACED,
        LATER,
        LIVE,
        3,
    )
    assert both.without(frozenset(), at=LATER) is both
    assert certificate(TEST).without(LIVE, at=LATER).version == 1
    assert gone.without(LIVE, at=LATER) is gone


# ── Installation ────────────────────────────────────────────────────────────────


def test_the_first_certificate_replaces_nothing() -> None:
    new = certificate(BOTH)
    installed = install(new, (), at=NOW)
    assert (installed.installed, installed.changed, installed.replaced, installed.retired) == (
        new,
        (),
        (),
        (),
    )


def test_a_new_certificate_for_one_mode_leaves_the_old_one_the_other() -> None:
    old = certificate(BOTH)
    installed = install(certificate(TEST), (old,), at=LATER)
    (changed,) = installed.changed
    assert (changed.id, changed.modes, changed.status) == (old.id, LIVE, CertificateStatus.ACTIVE)
    assert (installed.replaced, installed.retired) == ((old.id,), ())


def test_a_new_certificate_for_both_modes_retires_the_old_one_replaced() -> None:
    old = certificate(BOTH)
    installed = install(certificate(BOTH), (old,), at=LATER)
    assert [(changed.id, changed.retired_reason) for changed in installed.retired] == [
        (old.id, RetiredReason.REPLACED)
    ]


def test_a_certificate_of_another_mode_is_left_alone() -> None:
    live = certificate(LIVE)
    test = certificate(TEST)
    installed = install(certificate(TEST), (live, test), at=LATER)
    assert installed.replaced == (test.id,)


@pytest.mark.parametrize(
    "active",
    [
        (certificate(LIVE), certificate(LIVE)),  # two serving live: the defect it would keep
        (certificate(LIVE).retired(RetiredReason.RETIRED, at=NOW),),
        (
            replace(
                certificate(LIVE),
                tenant=Tenant(organization=COMPANY.organization, company=CompanyId(new_id()), mode=None),
            ),
        ),
    ],
)
def test_installing_over_what_is_not_the_company_s_active_certificates_is_a_defect(
    active: tuple[Certificate, ...],
) -> None:
    with pytest.raises(InvalidValueError):
        install(certificate(TEST), active, at=LATER)


def test_installing_a_retired_certificate_is_a_defect() -> None:
    with pytest.raises(InvalidValueError):
        install(certificate(TEST).retired(RetiredReason.RETIRED, at=NOW), (), at=LATER)


MODES: Final = st.sampled_from([TEST, LIVE, BOTH])


@given(st.lists(st.one_of(MODES, st.integers(min_value=0, max_value=9)), max_size=30))
def test_never_two_active_certificates_serve_one_mode(steps: list[frozenset[Mode] | int]) -> None:
    """Random installs (a set of modes) and retirements (the n-th certificate so far, if it exists)."""
    certificates: list[Certificate] = []
    for at, step in enumerate(steps):
        moment = NOW + timedelta(minutes=at)
        if isinstance(step, int):
            if step < len(certificates):
                certificates[step] = certificates[step].retired(RetiredReason.RETIRED, at=moment)
            continue
        active = [known for known in certificates if known.is_active]
        installed = install(certificate(step), active, at=moment)
        by_id = {changed.id: changed for changed in installed.changed}
        certificates = [by_id.get(known.id, known) for known in certificates] + [installed.installed]
        for mode in Mode:
            assert sum(known.serves(mode) for known in certificates) <= 1
        assert all(known.modes for known in certificates if known.is_active)
        assert all(known.retired_at is not None for known in certificates if not known.is_active)
        assert {mode for mode in Mode if installed.installed.serves(mode)} == step


# ── Signing: the window and the skew ────────────────────────────────────────────


def test_a_certificate_signs_up_to_its_last_moment_less_the_skew() -> None:
    signer = certificate(not_before=NOW - timedelta(days=1), not_after=NOW + SKEW)
    signer.require_signable(signed_at=NOW, now=NOW, skew=SKEW)  # NOW is exactly not_after - skew
    late = certificate(not_before=NOW - timedelta(days=1), not_after=NOW + SKEW - TICK)
    with pytest.raises(NotCoveredError):
        late.require_signable(signed_at=NOW, now=NOW, skew=SKEW)


def test_a_certificate_signs_from_its_first_moment() -> None:
    fresh = certificate(not_before=NOW, not_after=NOW + timedelta(days=1))
    fresh.require_signable(signed_at=NOW, now=NOW, skew=SKEW)
    with pytest.raises(NotCoveredError):
        fresh.require_signable(signed_at=NOW - TICK, now=NOW, skew=SKEW)


@pytest.mark.parametrize("direction", [1, -1])
def test_the_document_s_moment_is_within_the_skew_of_the_clock_either_way(direction: int) -> None:
    signer = certificate(not_before=NOW - timedelta(days=1), not_after=NOW + timedelta(days=1))
    signer.require_signable(signed_at=NOW + direction * SKEW, now=NOW, skew=SKEW)
    with pytest.raises(ClockSkewError):
        signer.require_signable(signed_at=NOW + direction * (SKEW + TICK), now=NOW, skew=SKEW)


def test_the_channel_s_former_cases_are_refused_by_the_signer() -> None:
    """The DO channel's own validity check before the signer held the rule (moved here by its Task 6)."""
    expired = certificate(not_before=NOW - timedelta(days=400), not_after=NOW - timedelta(seconds=1))
    not_yet = certificate(not_before=NOW + timedelta(seconds=1), not_after=NOW + timedelta(days=400))
    short = certificate(not_before=NOW - timedelta(days=1), not_after=NOW + SKEW - timedelta(seconds=1))
    for refused in (expired, not_yet, short):
        with pytest.raises(NotCoveredError):
            refused.require_signable(signed_at=NOW, now=NOW, skew=SKEW)
    certificate(not_before=NOW, not_after=NOW + SKEW).require_signable(signed_at=NOW, now=NOW, skew=SKEW)


def test_a_retired_certificate_never_signs_and_the_skew_comes_before_the_window() -> None:
    retired = certificate().retired(RetiredReason.REPLACED, at=NOW)
    with pytest.raises(CertificateRetiredError):
        retired.require_signable(signed_at=NOW + timedelta(days=9999), now=NOW, skew=SKEW)
    expired = certificate(not_before=NOW - timedelta(days=2), not_after=NOW - timedelta(days=1))
    with pytest.raises(ClockSkewError):
        expired.require_signable(signed_at=NOW + timedelta(days=1), now=NOW, skew=SKEW)


def test_signing_refusals_are_conflicts_with_their_own_codes() -> None:
    refusals: list[LedgerError] = [CertificateRetiredError(), NotCoveredError(), ClockSkewError()]
    assert [(error.kind, error.code) for error in refusals] == [
        (ErrorKind.CONFLICT, "certificate_retired"),
        (ErrorKind.CONFLICT, "signer_window_not_covered"),
        (ErrorKind.CONFLICT, "signer_clock_skew"),
    ]


@pytest.mark.parametrize(
    ("signed_at", "now", "skew"),
    [
        (NOW.replace(tzinfo=None), NOW, SKEW),
        (NOW, NOW.replace(tzinfo=None), SKEW),
        (NOW, NOW, -TICK),
    ],
)
def test_moments_and_skews_our_code_could_not_have_given_are_a_defect(
    signed_at: datetime, now: datetime, skew: timedelta
) -> None:
    with pytest.raises(InvalidValueError):
        certificate().require_signable(signed_at=signed_at, now=now, skew=skew)
