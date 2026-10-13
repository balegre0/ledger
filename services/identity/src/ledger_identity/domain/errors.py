"""The identity service's errors: each with its kind and a stable code clients match on (§4.2, §10).

Messages name ids, fields and limits, never text a client typed: a legal name or tax id is the client's data.
"""

from typing import ClassVar
lazy from uuid import UUID

from ledger_kernel.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    LedgerError,
    NotFoundError,
    excerpt,
)
lazy from ledger_kernel.access import Permission, SubjectId


class InvalidStateError(LedgerError):
    """A domain value built from parts that do not belong together. A bug in the code that builds it.

    Annotations are not enforced at runtime and the rules that guard roles rest on these values: each is
    checked once, where it is made. The message names the field, never a value.
    """

    code: ClassVar[str] = "invalid_state"

    def __init__(self, *, field: str, requirement: str) -> None:
        super().__init__(f"{field} must be {requirement}")


class InvalidNameError(InvalidInputError):
    """A name that cannot be shown as it is: empty, too long, or with an invisible character."""

    code: ClassVar[str] = "invalid_name"

    def __init__(self, field: str, problem: str) -> None:
        super().__init__(f"{field} {problem}")


class InvalidCodeError(InvalidInputError):
    """A code that is not 1 to 10 upper-case ASCII letters and digits."""

    code: ClassVar[str] = "invalid_code"

    def __init__(self) -> None:
        super().__init__("a code is 1 to 10 upper-case letters and digits")


class OrganizationNotFoundError(NotFoundError):
    """The organization has no company yet, so identity holds nothing of it."""

    code: ClassVar[str] = "organization_not_found"

    def __init__(self, organization: UUID) -> None:
        super().__init__(f"organization {organization} has no company yet")


class CompanyNotFoundError(NotFoundError):
    """No company by this id in the organization."""

    code: ClassVar[str] = "company_not_found"

    def __init__(self, company: UUID) -> None:
        super().__init__(f"company {company} was not found")


class BranchNotFoundError(NotFoundError):
    """No branch by this id in the company."""

    code: ClassVar[str] = "branch_not_found"

    def __init__(self, branch: UUID) -> None:
        super().__init__(f"branch {branch} was not found")


class EmissionPointNotFoundError(NotFoundError):
    """No emission point by this id in the branch."""

    code: ClassVar[str] = "emission_point_not_found"

    def __init__(self, point: UUID) -> None:
        super().__init__(f"emission point {point} was not found")


class MembershipNotFoundError(NotFoundError):
    """The subject holds no role in the company."""

    code: ClassVar[str] = "membership_not_found"

    def __init__(self, subject: SubjectId) -> None:
        super().__init__(f"{excerpt(subject)} holds no role in the company")


class TaxIdTakenError(ConflictError):
    """The organization already has a company with this tax id."""

    code: ClassVar[str] = "tax_id_taken"

    def __init__(self) -> None:
        super().__init__("the organization already has a company with this tax id")


class BranchCodeTakenError(ConflictError):
    """The company already has a branch with this code."""

    code: ClassVar[str] = "branch_code_taken"

    def __init__(self) -> None:
        super().__init__("the company already has a branch with this code")


class EmissionPointCodeTakenError(ConflictError):
    """The branch already has an emission point with this code."""

    code: ClassVar[str] = "emission_point_code_taken"

    def __init__(self) -> None:
        super().__init__("the branch already has an emission point with this code")


class CompanyInactiveError(ConflictError):
    """The company was deactivated: it changes no more."""

    code: ClassVar[str] = "company_inactive"

    def __init__(self, company: UUID) -> None:
        super().__init__(f"company {company} is inactive")


class StaleVersionError(ConflictError):
    """The company is not at a version ``If-Match`` names, or it changed meanwhile."""

    code: ClassVar[str] = "stale_version"

    def __init__(self, company: UUID) -> None:
        super().__init__(f"company {company} is not at the version the request names")


class LastOwnerError(ConflictError):
    """The change would leave the company without an owner."""

    code: ClassVar[str] = "last_owner"

    def __init__(self) -> None:
        super().__init__("a company keeps at least one owner")


class OwnersOnlyError(ForbiddenError):
    """Only an owner grants the owner role, or takes it away."""

    code: ClassVar[str] = "owners_only"

    def __init__(self) -> None:
        super().__init__("only an owner grants or takes the owner role")


class ActorNotAllowedError(ForbiddenError):
    """The actor's membership, read as it is now, lacks the permission the change needs.

    The same code as the platform's ``PermissionDeniedError``: for a client both mean "your role does not
    allow this"; this one is raised under the company's lock, when the role changed after the request was
    checked.
    """

    code: ClassVar[str] = "permission_denied"

    def __init__(self, permission: Permission) -> None:
        super().__init__(f"this needs the permission {permission.value}")
