"""fiscal's API for the other modules (§3.4): a Protocol, frozen DTOs, and its key.

Callers receive a ``FiscalApi`` from the composition root (``apis.get(FISCAL_API)``, once their manifest
declares the dependency). Every method opens a unit of its own and none may be called while the caller holds
one (``UnitOpenError``). A call is limited to the organization its scope names.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypeIs, final, runtime_checkable
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_platform.runtime.keys import Key
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.runtime.calls import Call, SystemCall

__all__ = ["FISCAL_API", "EnrollmentStatusName", "EnrollmentView", "FiscalApi"]

type EnrollmentStatusName = Literal["pending", "active", "disabled"]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EnrollmentView:
    """An enrollment as other modules see it, as stored at its last review."""

    id: UUID
    company: CompanyId
    mode: Mode
    channel: str
    environment: str
    status: EnrollmentStatusName
    currently_due: tuple[str, ...]
    disabled_reason: str | None
    version: int
    updated_at: datetime


@runtime_checkable
class FiscalApi(Protocol, metaclass=ABCMeta):
    """What the other modules may ask of fiscal. Abstract, so that its method needs no body."""

    @abstractmethod
    async def enrollments(
        self, call: Call | SystemCall, company: CompanyId, mode: Mode
    ) -> tuple[EnrollmentView, ...]:
        """Every enrollment of ``company`` (the call's organization's) in ``mode``, disabled too, by id."""


def _is_fiscal_api(value: object) -> TypeIs[FiscalApi]:
    return isinstance(value, FiscalApi)


FISCAL_API: Final = Key("fiscal", _is_fiscal_api)
"""fiscal's API under its name: how a module reads it and how fiscal binds it."""
