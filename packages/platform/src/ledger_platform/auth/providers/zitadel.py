"""Zitadel's claims (§10): the identity provider's rules for reading a token.

Where its tokens name the caller's organization and roles, what tells its access tokens from its ID tokens,
and what tells a person from a machine. A second provider would bring rules of its own beside these.
"""

from typing import Final
from uuid import NAMESPACE_URL, uuid5
lazy from collections.abc import Collection, Iterator, Mapping

from pydantic import TypeAdapter

from ledger_kernel.access import PrincipalKind
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


PRINCIPAL_KIND_CLAIM: Final = "urn:ledger:principal_kind"
"""The signed claim that says whether a person or a machine calls: ``human`` or ``machine``.

Zitadel puts no user type in an access token; an Action of the organization's pre-access-token flow adds this
claim from the user's type (``deploy/local/zitadel/bootstrap.sh``, a deployment step everywhere else). Where
it is there it decides, and a token it contradicts is refused; ``token_kind_claim_required`` makes it
mandatory (staging and production)."""
KINDS: Final = {kind.value: kind for kind in PrincipalKind}


def kind_of(client_id: str, audiences: Collection[str], project: str) -> PrincipalKind:
    """Whether the token of ``client_id`` for ``audiences`` was issued to a person or to a machine.

    Checked against Zitadel v4.19.2. A person signs in through an application of the provider: the token's
    ``client_id`` is that application's id, and Zitadel adds the application to the audiences. A machine user
    authenticates with its own credentials (client credentials or the JWT profile grant) and no application:
    ``client_id`` is its user name, and the audiences are only the projects asked for. The project itself
    (``project``, the tokens' audience) is no application a person signs in through: a ``client_id`` equal to
    it (a machine user named after the project, a token exchange) is a machine's. Zitadel puts no claim that
    names the user's type in an access token, so this is the one signal its own claims give; the signed
    ``PRINCIPAL_KIND_CLAIM`` decides where a token carries it. Whatever it says, a subject an API client names
    is bound to that client's mode and revocation (identity's standings), so a machine passing for a person
    gains nothing.
    """
    if client_id != project and client_id in audiences:
        return PrincipalKind.HUMAN
    return PrincipalKind.MACHINE
