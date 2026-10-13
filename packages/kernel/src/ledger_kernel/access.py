"""Who acts and what they may do: the vocabulary of authorization every service shares.

A ``Principal`` is a caller whose token was verified: the identity provider's id of the user or machine
(``subject``), the organization it acts in, and the roles the provider granted it there. What it may do in a
company is a set of ``Permission``s, which the identity service derives from the principal's role there.
Permissions cross service boundaries as their values, which therefore never change.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Final, NewType, final

from ledger_kernel.errors import InvalidInputError, InvalidValueError, excerpt
lazy from ledger_kernel.ids import OrganizationId

SubjectId = NewType("SubjectId", str)
"""Who a principal is: the identity provider's id of a user or a machine (Zitadel's are digits)."""

MAX_SUBJECT_LENGTH: Final = 128
MAX_PROVIDER_ORGANIZATION_LENGTH: Final = 128
_SUBJECT: Final = re.compile(rf"[A-Za-z0-9._@:-]{{1,{MAX_SUBJECT_LENGTH}}}", re.ASCII)


class InvalidSubjectError(InvalidInputError):
    """Text that is not a subject: empty, too long, or with a character no provider uses in an id."""

    code: ClassVar[str] = "invalid_subject"

    def __init__(self, text: str) -> None:
        super().__init__(f"not a subject: {excerpt(text)}")


def parse_subject(text: str) -> SubjectId:
    """Read a subject: 1 to ``MAX_SUBJECT_LENGTH`` ASCII letters, digits or ``. _ @ : -``."""
    if _SUBJECT.fullmatch(text) is None:
        raise InvalidSubjectError(text)
    return SubjectId(text)


class Permission(StrEnum):
    """What a principal may do in a company. Stable codes: they cross service boundaries."""

    COMPANY_READ = "company.read"
    """Read the company, its branches and its emission points."""
    COMPANY_MANAGE = "company.manage"
    """Change the company, deactivate it, open branches and emission points."""
    MEMBERSHIP_READ = "membership.read"
    """See who holds which role in the company."""
    MEMBERSHIP_MANAGE = "membership.manage"
    """Grant and revoke every role but the owner's."""
    OWNERSHIP_MANAGE = "ownership.manage"
    """Grant and revoke the owner role."""
    CERTIFICATE_MANAGE = "certificate.manage"
    """Upload and replace the company's signing certificate (milestone 3)."""
    INVOICE_READ = "invoice.read"
    """Read the company's documents (milestone 5)."""
    INVOICE_ISSUE = "invoice.issue"
    """Issue documents in the company's name (milestone 5)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Principal:
    """A caller whose credentials were verified."""

    subject: SubjectId
    organization: OrganizationId
    """The organization it acts in: derived from the identity provider's organization id."""
    provider_organization: str
    """The identity provider's own id of that organization (Zitadel's resource owner)."""
    roles: frozenset[str] = field(default_factory=frozenset[str])
    """Roles the identity provider granted in that organization (project roles); none by default."""

    def __post_init__(self) -> None:
        # Annotations are not enforced at runtime, and a principal decides what a caller may do: it is checked
        # once, where it is made. A failure is a defect of the code that built it (``InvalidValueError``).
        if type(self.roles) is not frozenset or not all(type(role) is str for role in self.roles):
            raise InvalidValueError(field="principal.roles", requirement="a frozenset of text")
        organization = self.provider_organization
        if type(organization) is not str or not 1 <= len(organization) <= MAX_PROVIDER_ORGANIZATION_LENGTH:
            raise InvalidValueError(
                field="principal.provider_organization",
                requirement=f"text of 1 to {MAX_PROVIDER_ORGANIZATION_LENGTH} characters",
            )
