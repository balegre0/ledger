"""API clients: machine users of an organization, each bound to one mode and pinned to an API version (§7.1).

An integration (an ISV's connector, a customer's ERP) calls the API with a Zitadel machine user. Registering
that machine user as an API client makes its credential carry what a human's request names: the mode it acts
in (so ``Ledger-Mode`` is optional for it) and the dated version of the API it speaks unless a request says
otherwise (pinned to the current version when it is registered). What it may do is the roles it holds in
companies, like any principal's (memberships); registering it grants none. A client is revoked, never
deleted: its record stays, and its tokens stop counting at once (§10).
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import final
lazy from datetime import date, datetime

from ledger_identity.domain.ids import ApiClientId
from ledger_kernel.ids import new_id
lazy from ledger_identity.domain.text import Name
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.ids import OrganizationId
lazy from ledger_kernel.tenancy import Mode


class ApiClientStatus(StrEnum):
    """Whether an API client's credential still counts."""

    ACTIVE = "active"
    REVOKED = "revoked"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiClientSpec:
    """What an API client is registered as: its machine user, a name to know it by and its API version."""

    subject: SubjectId
    """The identity provider's id of the machine user: what its tokens carry as ``sub``."""
    name: Name
    api_version: date
    """The dated API version it speaks unless a request says otherwise: fixed when it is registered."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiClient:
    """A machine user registered with an organization for one mode."""

    id: ApiClientId
    organization: OrganizationId
    mode: Mode
    subject: SubjectId
    """The identity provider's id of the machine user: what its tokens carry as ``sub``."""
    name: Name
    api_version: date
    """The dated API version it speaks unless a request says otherwise: fixed when it was registered."""
    status: ApiClientStatus
    created_at: datetime
    created_by: SubjectId
    revoked_at: datetime | None

    @classmethod
    def register(
        cls, spec: ApiClientSpec, *, organization: OrganizationId, mode: Mode, actor: SubjectId, now: datetime
    ) -> ApiClient:
        """A new, active API client of ``organization`` for ``mode``, registered by ``actor`` ``now``."""
        return cls(
            id=ApiClientId(new_id()),
            organization=organization,
            mode=mode,
            subject=spec.subject,
            name=spec.name,
            api_version=spec.api_version,
            status=ApiClientStatus.ACTIVE,
            created_at=now,
            created_by=actor,
            revoked_at=None,
        )

    @property
    def active(self) -> bool:
        """Whether its credential still counts."""
        return self.status is ApiClientStatus.ACTIVE

    def revoke(self, *, now: datetime) -> ApiClient:
        """The client revoked ``now``; one revoked already is as it was (and keeps its first time)."""
        if not self.active:
            return self
        return replace(self, status=ApiClientStatus.REVOKED, revoked_at=now)
