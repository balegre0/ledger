"""An enrollment's life (§7.6, Decision 31): pending until its channel requires nothing, active for good, and
disabled for good; a live enrollment never activates before the authority evidence is recorded (Task 31)."""

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final
lazy from collections.abc import Callable

import pytest

from ledger_fiscal.domain.enrollments import (
    MAX_REASON,
    Asked,
    Disabled,
    Enrollment,
    EnrollmentId,
    EnrollmentStatus,
    parse_reason,
)
from ledger_fiscal.domain.errors import (
    EnrollmentExistsError,
    EnrollmentNotFoundError,
    InvalidDisableReasonError,
)
from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, format_id, new_id
from ledger_kernel.tenancy import Mode, Tenant

NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
LATER: Final = NOW + timedelta(minutes=5)
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)
LIVE: Final = Tenant(TENANT.organization, TENANT.company, Mode.LIVE)


@dataclass(frozen=True, slots=True)
class Due:
    """What a channel requires: the kit's ``Requirements`` has these two fields."""

    currently_due: tuple[str, ...] = ()
    disabled_reason: str | None = None


NOTHING_DUE: Final = Due()


def enrolled(requirements: Due = NOTHING_DUE, *, tenant: Tenant = TENANT) -> Enrollment:
    asked = Asked(
        enrollment=EnrollmentId(new_id()),
        tenant=tenant,
        channel="do-ecf-v1",
        environment="testecf" if tenant.mode is Mode.TEST else "ecf",
    )
    return Enrollment.enroll(asked, requirements=requirements, now=NOW)


def test_a_new_enrollment_waits_for_what_its_channel_requires() -> None:
    enrollment = enrolled(Due(("do_ecf.range", "signer.certificate")))
    assert (enrollment.status, enrollment.currently_due, enrollment.version) == (
        EnrollmentStatus.PENDING,
        ("do_ecf.range", "signer.certificate"),
        1,
    )
    assert (enrollment.created_at, enrollment.updated_at, enrollment.tenant) == (NOW, NOW, TENANT)
    assert (enrollment.requirements_reason, enrollment.disablement) == (None, None)


def test_a_new_test_enrollment_its_channel_requires_nothing_of_is_active_at_once() -> None:
    enrollment = enrolled()
    assert (enrollment.status, enrollment.version, enrollment.active) == (EnrollmentStatus.ACTIVE, 1, True)


def test_a_test_enrollment_activates_once_nothing_is_due() -> None:
    pending = enrolled(Due(("do_ecf.range",)))
    active = pending.reviewed(NOTHING_DUE, live_evidence=False, now=LATER)
    assert (active.status, active.currently_due, active.version, active.updated_at) == (
        EnrollmentStatus.ACTIVE,
        (),
        2,
        LATER,
    )
    assert active.created_at == NOW


def test_a_channel_s_disabled_reason_keeps_it_pending() -> None:
    pending = enrolled(Due((), "do_ecf.rnc_not_authorized"))
    assert (pending.status, pending.requirements_reason) == (
        EnrollmentStatus.PENDING,
        "do_ecf.rnc_not_authorized",
    )
    again = pending.reviewed(Due((), "do_ecf.rnc_not_authorized"), live_evidence=False, now=LATER)
    assert again is pending  # nothing changed: the same state, the same version


def test_a_live_enrollment_activates_only_once_the_evidence_is_recorded() -> None:
    live = enrolled(tenant=LIVE)
    assert live.status is EnrollmentStatus.PENDING  # no evidence exists before the enrollment does
    assert live.reviewed(NOTHING_DUE, live_evidence=False, now=LATER) is live
    active = live.reviewed(NOTHING_DUE, live_evidence=True, now=LATER)
    assert (active.status, active.version) == (EnrollmentStatus.ACTIVE, 2)


def test_an_active_enrollment_stays_active_when_something_is_due_again() -> None:
    active = enrolled()
    due = active.reviewed(Due(("signer.certificate",)), live_evidence=False, now=LATER)
    assert (due.status, due.currently_due, due.version) == (
        EnrollmentStatus.ACTIVE,
        ("signer.certificate",),
        2,
    )


def test_a_disabled_enrollment_is_final_and_disabling_it_again_changes_nothing() -> None:
    pending = enrolled(Due(("do_ecf.range",), "do_ecf.rnc_not_authorized"))
    disabled = pending.disabled("Moving to production", now=LATER)
    assert (disabled.status, disabled.disablement, disabled.version, disabled.updated_at) == (
        EnrollmentStatus.DISABLED,
        Disabled(reason="Moving to production", at=LATER),
        2,
        LATER,
    )
    assert (disabled.currently_due, disabled.requirements_reason) == (
        ("do_ecf.range",),
        "do_ecf.rnc_not_authorized",
    )  # what the last review left: the channel's words, beside the client's
    assert disabled.active is False
    assert disabled.reviewed(NOTHING_DUE, live_evidence=True, now=LATER) is disabled
    assert disabled.disabled("Another reason", now=LATER) is disabled


def test_a_review_clears_the_mark_a_channel_left_and_counts_as_a_change_only_when_something_changed() -> None:
    """Decision 34: ``fiscal.requirements`` marks an enrollment ``review_due``; its next review clears it. A
    review that changes nothing else leaves the version as it was (nothing to announce)."""
    pending = enrolled(Due(("do_ecf.range",)))
    marked = replace(pending, review_due=True)
    same = marked.reviewed(Due(("do_ecf.range",)), live_evidence=False, now=LATER)
    assert (same.review_due, same.version, same.updated_at, same.status) == (
        False,
        1,
        NOW,
        EnrollmentStatus.PENDING,
    )
    active = marked.reviewed(NOTHING_DUE, live_evidence=False, now=LATER)
    assert (active.review_due, active.version, active.status) == (False, 2, EnrollmentStatus.ACTIVE)
    assert pending.reviewed(Due(("do_ecf.range",)), live_evidence=False, now=LATER) is pending
    assert enrolled().review_due is False


def test_an_enrollment_is_its_tenant_s() -> None:
    enrollment = enrolled(Due(("x",)))
    assert (enrollment.organization, enrollment.company, enrollment.mode) == (
        TENANT.organization,
        TENANT.company,
        Mode.TEST,
    )


def _unversioned(found: Enrollment) -> Enrollment:
    return replace(found, version=0)


def _disabled_unrecorded(found: Enrollment) -> Enrollment:
    return replace(found, status=EnrollmentStatus.DISABLED)


def _pending_but_disabled(found: Enrollment) -> Enrollment:
    return replace(found, disablement=Disabled(reason="x", at=NOW))


def _disabled_before_created(found: Enrollment) -> Enrollment:
    return replace(
        found,
        status=EnrollmentStatus.DISABLED,
        disablement=Disabled(reason="x", at=NOW - timedelta(seconds=1)),
    )


def _updated_before_created(found: Enrollment) -> Enrollment:
    return replace(found, updated_at=NOW - timedelta(seconds=1))


@pytest.mark.parametrize(
    ("broken", "field"),
    [
        (_unversioned, "enrollment.version"),
        (_disabled_unrecorded, "enrollment.disablement"),
        (_pending_but_disabled, "enrollment.disablement"),
        (_disabled_before_created, "enrollment.disablement.at"),
        (_updated_before_created, "enrollment.updated_at"),
    ],
)
def test_an_enrollment_that_breaks_its_invariants_is_a_defect(
    broken: Callable[[Enrollment], Enrollment], field: str
) -> None:
    with pytest.raises(InvalidValueError, match=re.escape(field)):
        broken(enrolled(Due(("x",))))


def test_an_enrollment_needs_a_mode() -> None:
    with pytest.raises(InvalidValueError, match=r"enrollment\.tenant"):
        enrolled(tenant=Tenant(TENANT.organization, TENANT.company, None))


def test_a_reason_is_cleaned_and_one_that_cannot_be_shown_is_refused_without_being_echoed() -> None:
    assert parse_reason("  Moving \t to\nproduction ") == "Moving to production"
    for text in ("", "   ", "x" * (MAX_REASON + 1), "bad\x00reason"):
        with pytest.raises(InvalidDisableReasonError) as caught:
            parse_reason(text)
        assert text.strip() == "" or text not in str(caught.value)
        assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "fiscal_disable_reason_invalid")


def test_the_errors_say_what_is_wrong_and_the_conflict_names_the_holder() -> None:
    holder = EnrollmentId(new_id())
    exists = EnrollmentExistsError(holder)
    assert (exists.kind, exists.code, exists.existing_id) == (
        ErrorKind.CONFLICT,
        "fiscal_enrollment_exists",
        holder,
    )
    assert exists.problem_members() == {"existing_id": format_id("enr", holder)}
    missing = EnrollmentNotFoundError()
    assert (missing.kind, missing.code) == (ErrorKind.NOT_FOUND, "fiscal_enrollment_not_found")
