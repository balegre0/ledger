"""identity's errors: each with its kind and a stable code clients match on (§4.2, §10).

Messages name ids, fields and limits, never text a client typed: a legal name or tax id is the client's data.
The refusals every module shares are the kernel's, one class per code: ``CompanyNotFoundError`` and
``PermissionDeniedError`` (``ledger_kernel.access``), and ``InvalidValueError`` for a value our own code built
wrong (``ledger_kernel.errors``).
"""

from typing import ClassVar
lazy from uuid import UUID

from ledger_kernel.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError, excerpt
lazy from ledger_kernel.access import SubjectId


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
    """An active company, of this organization or another, holds the tax id: one taxpayer, one company.

    The answer is the same wherever that company is: nothing about another organization is told.
    """

    code: ClassVar[str] = "tax_id_taken"

    def __init__(self) -> None:
        super().__init__("an active company already holds this tax id")


class DuplicateTaxIdError(InvalidInputError):
    """A company lists the same tax id twice (once normalized: separators and case do not tell them apart)."""

    code: ClassVar[str] = "tax_id_duplicated"

    def __init__(self) -> None:
        super().__init__("a company lists each of its tax ids once")


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


class OrganizationAdminRequiredError(ForbiddenError):
    """The caller acts in the organization, but the identity provider did not make it an administrator of it.

    Its code is the plain ``forbidden``: registering is the one act decided at the organization's level.
    """

    def __init__(self, role: str) -> None:
        super().__init__(f"registering a company needs the organization role {role}")


class OwnersOnlyError(ForbiddenError):
    """Only an owner grants the owner role, or takes it away."""

    code: ClassVar[str] = "owners_only"

    def __init__(self) -> None:
        super().__init__("only an owner grants or takes the owner role")
