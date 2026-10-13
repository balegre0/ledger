"""Whose data a unit of work, a request or an event is about (§6.3, ADR 0007).

A tenant is a company of an organization in one mode. ``mode=None`` reaches only what both modes share (the
company itself, its establishments, its memberships): row-level security compares a moded row's mode with
the unit's, and a unit without one matches none (fail closed). An organization scope reaches the
organization's own rows, in a mode (API clients, idempotency keys) or in none (its companies).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import final
from uuid import UUID

from ledger_kernel.errors import InvalidValueError
lazy from ledger_kernel.ids import CompanyId, OrganizationId


class Mode(StrEnum):
    """Test or live: every moded row, credential and request is one of them, never both."""

    TEST = "test"
    LIVE = "live"


def _require(owner: str, organization: object, mode: object) -> None:
    """Refuse a scope built from parts its fields do not hold: it decides which rows a unit sees."""
    if type(organization) is not UUID:
        raise InvalidValueError(field=f"{owner}.organization", requirement="a UUID")
    if mode is not None and type(mode) is not Mode:
        raise InvalidValueError(field=f"{owner}.mode", requirement="a Mode or None")


@final
@dataclass(frozen=True, slots=True)
class Tenant:
    """One company of an organization, in ``mode`` (``None``: only what both modes share)."""

    organization: OrganizationId
    company: CompanyId
    mode: Mode | None

    def __post_init__(self) -> None:
        _require("tenant", self.organization, self.mode)
        if type(self.company) is not UUID:
            raise InvalidValueError(field="tenant.company", requirement="a UUID")


@final
@dataclass(frozen=True, slots=True)
class OrganizationScope:
    """An organization's own rows, in ``mode`` (``None``: only what both modes share)."""

    organization: OrganizationId
    mode: Mode | None

    def __post_init__(self) -> None:
        _require("organization_scope", self.organization, self.mode)

    @property
    def company(self) -> None:
        """An organization scope reaches no company's rows."""
        return None


type Scope = Tenant | OrganizationScope
"""Every scope answers ``organization``, ``company`` (``None`` for an organization) and ``mode``."""
