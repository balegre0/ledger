"""Zitadel's claims (§10): the identity provider's rules for reading a token.

Where its tokens name the caller's organization and roles, and what tells its access tokens from its ID
tokens. A second provider would bring rules of its own beside these.
"""

from typing import Final
from uuid import NAMESPACE_URL, uuid5
lazy from collections.abc import Iterator, Mapping

from pydantic import TypeAdapter

from ledger_kernel.ids import OrganizationId

ORGANIZATION_CLAIM: Final = "urn:zitadel:iam:user:resourceowner:id"
"""The caller's organization (asked for with the scope ``urn:zitadel:iam:user:resourceowner``)."""
ACCESS_TOKEN_TYPES: Final = frozenset({"jwt", "at+jwt", "application/jwt", "application/at+jwt"})
"""The ``typ`` headers of an access token, in lower case (RFC 7515 4.1.9 allows the ``application/`` form)."""
ACCESS_TOKEN_CLAIMS: Final = ("jti", "client_id")
"""Claims every access token carries (RFC 9068 2.2): text, not empty."""
ID_TOKEN_CLAIMS: Final = frozenset({"azp", "nonce", "at_hash", "c_hash"})
"""Claims of an OpenID Connect ID token that no access token carries: a token with any of them is refused."""
_ORGANIZATIONS: Final = uuid5(NAMESPACE_URL, "urn:ledger:organization")
_ROLES: Final = TypeAdapter(dict[str, dict[str, str]])


def organization_of(provider_organization: str) -> OrganizationId:
    """The ledger id of the provider's organization: derived, so every app computes the same one."""
    return OrganizationId(uuid5(_ORGANIZATIONS, provider_organization))


def project_roles_claim(project: str) -> str:
    """The claim of the roles granted in ``project``: ``{role: {organization id: domain}}``.

    The only claim roles are read from, for the project that is the tokens' audience. Zitadel's generic
    ``urn:zitadel:iam:org:project:roles`` gathers the roles of every project the token was asked for, and a
    role of another project, though named alike, was granted for something else: neither counts.
    """
    return f"urn:zitadel:iam:org:project:{project}:roles"


def granted_roles(claims: Mapping[str, object], name: str, organization: str) -> Iterator[str]:
    """The roles the claim ``name`` grants in ``organization``."""
    roles = _ROLES.validate_python(claims.get(name, {}))
    yield from (role for role, organizations in roles.items() if organization in organizations)
