"""Who acts and what they may do: the vocabulary of authorization every app shares.

A ``Principal`` is a caller whose token was verified: the identity provider's id of the user or machine
(``subject``), the organization it acts in, and the roles the provider granted it there. What it may do in a
company is a set of ``Permission``s, codes a module declares in its manifest, which identity derives from
the principal's ``Role`` there: one of the built-in roles every manifest grants its permissions to (§7.7).
Refusals are one class per code: ``PermissionDeniedError`` and ``CompanyNotFoundError`` are raised by the
platform's access checks and by the modules alike.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Final, NewType, final

from ledger_kernel.errors import ForbiddenError, InvalidInputError, InvalidValueError, NotFoundError, excerpt
lazy from ledger_kernel.ids import CompanyId, OrganizationId

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


class Role(StrEnum):
    """A principal's role in one company."""

    # The one list of the built-in roles (§7.7): identity's memberships hold one, and the manifest model reads
    # ``[[permissions]] roles`` with it. Its docstring is the public API's description of the type.

    OWNER = "owner"
    """Everything, including who else owns the company."""
    ADMIN = "admin"
    """Everything but the owner role."""
    ACCOUNTANT = "accountant"
    """Reads the company and its memberships (and, with invoicing, its documents)."""
    ISSUER = "issuer"
    """Reads the company (and, with invoicing, issues documents)."""
    VIEWER = "viewer"
    """Reads."""


Permission = NewType("Permission", str)
"""What a principal may do in a company: ``<module>.<resource>.<action>``, declared in a manifest (§7.7).

Codes cross module boundaries as their text, which therefore never changes once declared.
"""

_PERMISSION: Final = re.compile(
    r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.(?:read|create|issue|void|manage|own)", re.ASCII
)


class InvalidPermissionError(InvalidInputError):
    """Text that is not a permission code."""

    code: ClassVar[str] = "invalid_permission"

    def __init__(self, text: str) -> None:
        super().__init__(f"not a permission (<module>.<resource>.<action>): {excerpt(text)}")


def parse_permission(text: str) -> Permission:
    """Read a permission code: ``<module>.<resource>.<action>``.

    Each part is lower-case ASCII letters, digits and underscores, led by a letter; the action is one of
    ``read``, ``create``, ``issue``, ``void``, ``manage`` or ``own``.
    """
    if _PERMISSION.fullmatch(text) is None:
        raise InvalidPermissionError(text)
    return Permission(text)


class PermissionDeniedError(ForbiddenError):
    """The caller holds a role in the company, but not one that allows this.

    Raised wherever a permission is checked: on a request (``CompanyAccess.require``), and by a module under
    the company's lock, when the role changed after the request was checked. One class, one code.
    """

    code: ClassVar[str] = "permission_denied"

    permission: Permission

    def __init__(self, permission: Permission) -> None:
        super().__init__(f"this needs the permission {permission}")
        self.permission = permission


class CompanyNotFoundError(NotFoundError):
    """No such company for the caller: it does not exist, or the caller holds no role in it.

    A caller learns nothing of companies it has no part in, its own organization's or another's.
    """

    code: ClassVar[str] = "company_not_found"

    def __init__(self, company: CompanyId) -> None:
        super().__init__(f"company {company} was not found")


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
