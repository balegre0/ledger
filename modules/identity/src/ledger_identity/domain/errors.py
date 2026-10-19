"""identity's errors: each with its kind and a stable code clients match on (§4.2, §10).

Messages name ids, fields and limits, never text a client typed: a legal name or tax id is the client's data.
The refusals every module shares are the kernel's, one class per code: ``CompanyNotFoundError`` and
``PermissionDeniedError`` (``ledger_kernel.access``), and ``InvalidValueError`` for a value our own code built
wrong (``ledger_kernel.errors``).
"""

from typing import ClassVar
lazy from uuid import UUID

from ledger_identity.domain.ids import API_CLIENT_PREFIX, ESTABLISHMENT_PREFIX
from ledger_kernel.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError, excerpt
from ledger_kernel.ids import COMPANY_PREFIX, ORGANIZATION_PREFIX, format_id
lazy from ledger_kernel.access import SubjectId


class InvalidNameError(InvalidInputError):
    """A name that cannot be shown as it is: empty, too long, or with an invisible character."""

    code: ClassVar[str] = "invalid_name"

    def __init__(self, field: str, problem: str) -> None:
        super().__init__(f"{field} {problem}")


class InvalidTimeZoneError(InvalidInputError):
    """Text that is not the name of an IANA time zone."""

    code: ClassVar[str] = "invalid_time_zone"

    def __init__(self) -> None:
        super().__init__("a time zone is the name of an IANA zone, such as America/Santo_Domingo")


class JurisdictionUnsupportedError(InvalidInputError):
    """A company of a jurisdiction no installed regime governs: nothing could judge its documents."""

    code: ClassVar[str] = "jurisdiction_unsupported"

    def __init__(self) -> None:
        super().__init__("no installed regime governs this jurisdiction")


class CurrencyUnsupportedError(InvalidInputError):
    """A functional currency the regime of the company's jurisdiction does not let it keep its books in."""

    code: ClassVar[str] = "currency_unsupported"

    def __init__(self) -> None:
        super().__init__("the jurisdiction's regime does not let a company keep its books in this currency")


class TaxIdForeignError(InvalidInputError):
    """A tax id of another country's scheme, where the jurisdiction's regime allows its own country's only."""

    code: ClassVar[str] = "tax_id_foreign"

    def __init__(self, position: int) -> None:
        super().__init__(f"tax_ids.{position} is of another country's scheme than the jurisdiction's")


class InvalidCodeError(InvalidInputError):
    """A code that is not 1 to 10 upper-case ASCII letters and digits."""

    code: ClassVar[str] = "invalid_code"

    def __init__(self) -> None:
        super().__init__("a code is 1 to 10 upper-case letters and digits")


class OrganizationNotFoundError(NotFoundError):
    """The organization has no company yet, so identity holds nothing of it."""

    code: ClassVar[str] = "organization_not_found"

    def __init__(self, organization: UUID) -> None:
        super().__init__(f"organization {format_id(ORGANIZATION_PREFIX, organization)} has no company yet")


class EstablishmentNotFoundError(NotFoundError):
    """No establishment by this id in the company."""

    code: ClassVar[str] = "establishment_not_found"

    def __init__(self, establishment: UUID | None = None) -> None:
        """``establishment`` is named by its public id; none when what was sent names no establishment."""
        super().__init__(
            "establishment was not found"
            if establishment is None
            else f"establishment {format_id(ESTABLISHMENT_PREFIX, establishment)} was not found"
        )


class CountryNotFoundError(NotFoundError):
    """No regime is installed for the country asked for (or what was asked for is no country code)."""

    code: ClassVar[str] = "country_not_found"

    def __init__(self) -> None:
        super().__init__("no regime is installed for this country")  # what was sent is not repeated


class ApiClientNotFoundError(NotFoundError):
    """No API client by this id in the organization, in the mode asked."""

    code: ClassVar[str] = "api_client_not_found"

    def __init__(self, client: UUID | None = None) -> None:
        """``client`` is named by its public id; none when what was sent names no API client."""
        super().__init__(
            "API client was not found"
            if client is None
            else f"API client {format_id(API_CLIENT_PREFIX, client)} was not found"
        )


class ApiClientExistsError(ConflictError):
    """The machine user is an API client of the organization already."""

    code: ClassVar[str] = "api_client_exists"

    def __init__(self) -> None:
        super().__init__("this machine user is an active API client of the organization already")


class ApiClientHumanError(InvalidInputError):
    """The subject to register is a person (the registering one): an API client is a machine user."""

    code: ClassVar[str] = "api_client_human"

    def __init__(self) -> None:
        super().__init__("an API client is a machine user: this subject is a person")


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


class EstablishmentCodeTakenError(ConflictError):
    """The company already has an establishment with this code."""

    code: ClassVar[str] = "establishment_code_taken"

    def __init__(self) -> None:
        super().__init__("the company already has an establishment with this code")


class CompanyInactiveError(ConflictError):
    """The company was deactivated: it changes no more."""

    code: ClassVar[str] = "company_inactive"

    def __init__(self, company: UUID) -> None:
        super().__init__(f"company {format_id(COMPANY_PREFIX, company)} is inactive")


class FiscalProfileLockedError(ConflictError):
    """The company's fiscal profile was locked (before its first numbered document): it changes no more."""

    code: ClassVar[str] = "fiscal_profile_locked"

    def __init__(self, company: UUID) -> None:
        super().__init__(f"company {format_id(COMPANY_PREFIX, company)} has its fiscal profile locked")


class StaleVersionError(ConflictError):
    """The company is not at a version ``If-Match`` names, or it changed meanwhile."""

    code: ClassVar[str] = "stale_version"

    def __init__(self, company: UUID) -> None:
        super().__init__(
            f"company {format_id(COMPANY_PREFIX, company)} is not at the version the request names"
        )


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
