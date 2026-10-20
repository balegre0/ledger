"""Enrollments (§7.6): a company, in one mode, on one fiscal channel and one of its environments.

An enrollment is ``pending`` while its channel requires something of the company (``currently_due``: a
certificate, a numbering range) or names a reason it cannot serve it (``disabled_reason``, the channel's
word), and ``active`` once it requires nothing. A live enrollment becomes active only once the company's
authority authorization is recorded with evidence as well (C4: ``live_evidence``, Task 31); until then it
stays pending. An active enrollment stays active whatever its channel requires later (Decision 31): a missing
certificate or range parks documents, not the enrollment. ``disabled`` is final: the company enrolls again
with a new one. What the channel requires is read anew every time it is reviewed and kept with the status
the review set.

The channel's answer reaches the domain as anything with its two fields (``Due``): fiscal-kit's
``Requirements`` is one, and the domain imports nothing but the kernel.
"""

import unicodedata
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final, NewType, Protocol, final
from uuid import UUID
lazy from datetime import datetime

from ledger_fiscal.domain.errors import InvalidDisableReasonError
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Mode, Tenant
lazy from ledger_kernel.ids import CompanyId, OrganizationId

EnrollmentId = NewType("EnrollmentId", UUID)
"""An enrollment's id (fiscal-kit's ``EnrollmentId`` is the same UUID, as channels see it)."""
MAX_REASON: Final = 200
"""The longest reason a client may give for disabling an enrollment."""
_INVISIBLE: Final = "C"
"""Unicode's "other" categories: controls, formats, surrogates, unassigned."""


class EnrollmentStatus(StrEnum):
    """Where an enrollment is in its life (see the module)."""

    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


class Due(Protocol, metaclass=ABCMeta):
    """What a channel requires of an enrollment, read now (fiscal-kit's ``Requirements``)."""

    @property
    @abstractmethod
    def currently_due(self) -> tuple[str, ...]:
        """What the company must still provide, in the channel's words."""

    @property
    @abstractmethod
    def disabled_reason(self) -> str | None:
        """Why the channel cannot serve the company now, in its words; ``None`` when nothing stops it."""


def parse_reason(text: str) -> str:
    """A client's reason to disable an enrollment: trimmed, one space between words, NFC, printable."""
    cleaned = unicodedata.normalize("NFC", " ".join(text.split()))
    printable = not any(unicodedata.category(character).startswith(_INVISIBLE) for character in cleaned)
    if not 0 < len(cleaned) <= MAX_REASON or not printable:
        raise InvalidDisableReasonError(MAX_REASON)
    return cleaned


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Asked:
    """An enrollment a company asks for: its id (minted first), the company in a mode, where it submits."""

    enrollment: EnrollmentId
    tenant: Tenant
    channel: str
    environment: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Enrollment:
    """A company's enrollment in one mode on one channel and environment (see the module)."""

    id: EnrollmentId
    organization: OrganizationId
    company: CompanyId
    mode: Mode
    channel: str
    environment: str
    """Fixed for its life: changing environment is disabling it and enrolling again."""
    status: EnrollmentStatus
    currently_due: tuple[str, ...]
    """What its channel required at its last review; empty once disabled."""
    disabled_reason: str | None
    """The channel's reason at its last review, or the client's once disabled (required then)."""
    version: int
    """From 1, one more at each change."""
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvalidValueError(field="enrollment.version", requirement="1 or more")
        if self.updated_at < self.created_at:
            raise InvalidValueError(field="enrollment.updated_at", requirement="no earlier than created_at")
        if self.status is EnrollmentStatus.DISABLED:
            if self.disabled_reason is None:
                raise InvalidValueError(
                    field="enrollment.disabled_reason", requirement="a reason once disabled"
                )
            if self.currently_due:
                raise InvalidValueError(field="enrollment.currently_due", requirement="nothing once disabled")

    @classmethod
    def enroll(cls, asked: Asked, *, requirements: Due, now: datetime) -> Enrollment:
        """The enrollment ``asked`` names, new, reviewed against ``requirements`` at once.

        No authority evidence exists for an enrollment before it does: a live one starts pending.
        """
        tenant = asked.tenant
        mode = tenant.mode
        if mode is None:
            raise InvalidValueError(field="enrollment.tenant", requirement="a company in a mode")
        new = cls(
            id=asked.enrollment,
            organization=tenant.organization,
            company=tenant.company,
            mode=mode,
            channel=asked.channel,
            environment=asked.environment,
            status=EnrollmentStatus.PENDING,
            currently_due=requirements.currently_due,
            disabled_reason=requirements.disabled_reason,
            version=1,
            created_at=now,
            updated_at=now,
        )
        status = new._status_after(requirements, live_evidence=False)
        return new if status is new.status else replace(new, status=status)

    @property
    def tenant(self) -> Tenant:
        """Its company, in its mode: the scope of every unit that reads or changes it."""
        return Tenant(self.organization, self.company, self.mode)

    @property
    def active(self) -> bool:
        """Whether documents of its company and mode are routed to its channel."""
        return self.status is EnrollmentStatus.ACTIVE

    def _status_after(self, requirements: Due, *, live_evidence: bool) -> EnrollmentStatus:
        ready = not requirements.currently_due and requirements.disabled_reason is None
        if self.status is EnrollmentStatus.PENDING and ready and (self.mode is Mode.TEST or live_evidence):
            return EnrollmentStatus.ACTIVE
        return self.status

    def reviewed(self, requirements: Due, *, live_evidence: bool, now: datetime) -> Enrollment:
        """The enrollment as ``requirements`` leave it (see the module); itself when nothing changes.

        ``live_evidence``: whether the company's authority authorization is recorded (live mode only).
        """
        if self.status is EnrollmentStatus.DISABLED:
            return self
        status = self._status_after(requirements, live_evidence=live_evidence)
        if (status, requirements.currently_due, requirements.disabled_reason) == (
            self.status,
            self.currently_due,
            self.disabled_reason,
        ):
            return self
        return replace(
            self,
            status=status,
            currently_due=requirements.currently_due,
            disabled_reason=requirements.disabled_reason,
            version=self.version + 1,
            updated_at=now,
        )

    def disabled(self, reason: str, *, now: datetime) -> Enrollment:
        """The enrollment disabled for ``reason`` (``parse_reason``'s); itself when it is disabled already."""
        if self.status is EnrollmentStatus.DISABLED:
            return self
        return replace(
            self,
            status=EnrollmentStatus.DISABLED,
            currently_due=(),
            disabled_reason=reason,
            version=self.version + 1,
            updated_at=now,
        )
