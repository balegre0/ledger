"""Where a company issues documents: its branches, and the emission points of each."""

from dataclasses import dataclass
from typing import final
lazy from datetime import datetime

from ledger_identity.domain.errors import BranchNotFoundError
from ledger_identity.domain.ids import BranchId, EmissionPointId
from ledger_kernel.ids import new_id
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.text import Code, Name
lazy from ledger_kernel.tenancy import Tenant


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Branch:
    """A branch of a company, known by a code unique in the company."""

    id: BranchId
    tenant: Tenant
    code: Code
    name: Name
    created_at: datetime

    @classmethod
    def open(cls, company: Company, *, code: Code, name: Name, now: datetime) -> Branch:
        """A new branch of ``company``, which must be active."""
        company.require_active()
        return cls(id=BranchId(new_id()), tenant=company.tenant, code=code, name=name, created_at=now)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EmissionPoint:
    """A point of a branch that issues documents, known by a code unique in the branch."""

    id: EmissionPointId
    tenant: Tenant
    branch: BranchId
    code: Code
    name: Name
    created_at: datetime

    @classmethod
    def open(
        cls, company: Company, branch: Branch, *, code: Code, name: Name, now: datetime
    ) -> EmissionPoint:
        """A new emission point of ``branch``, which must be one of ``company``'s, and that must be active."""
        company.require_active()
        if branch.tenant != company.tenant:
            raise BranchNotFoundError(branch.id)
        return cls(
            id=EmissionPointId(new_id()),
            tenant=company.tenant,
            branch=branch.id,
            code=code,
            name=name,
            created_at=now,
        )
