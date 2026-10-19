"""What identity knows of a caller beyond its token (§9.2, §9.7): its pins, its mode, whether it was revoked.

An API client is a machine user registered with an organization, bound to one mode and pinned to a version
of the API; its organization is pinned too. The chassis asks identity for all of it (``StandingsPort``) once
the token is proven, and the answer settles what the request is: the version it speaks (``resolve``), the
mode it acts in (an API client's credential carries it: ``Ledger-Mode`` is then optional, and may only
repeat it), whether a credential that was revoked still counts (it does not), and whether a machine may act at
all: a machine acts only as an API client, one that is none is refused (403 ``api_client_unregistered``), and
a person never stands as one.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Final, Protocol, final, runtime_checkable

from ledger_kernel.errors import ForbiddenError, LedgerError, UnauthenticatedError
from ledger_platform.http.versions import CURRENT_VERSION
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.http.versions import ApiVersion


class ApiClientRevokedError(UnauthenticatedError):
    """The API client the token belongs to was revoked: its tokens no longer count, whatever their expiry."""

    code: ClassVar[str] = "api_client_revoked"

    def __init__(self) -> None:
        super().__init__("this API client was revoked")


class ApiClientUnregisteredError(ForbiddenError):
    """A machine user its organization never registered as an API client: a machine acts only as one."""

    code: ClassVar[str] = "api_client_unregistered"

    def __init__(self) -> None:
        super().__init__("this machine user is not an API client of its organization")


class StandingsBoundError(LedgerError):
    """Standings bound a second time: two modules keep them, or the app bound them twice (a defect)."""

    code: ClassVar[str] = "standings_bound"

    def __init__(self) -> None:
        super().__init__("how callers stand is bound once: by the module that provides the StandingsPort")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Standing:
    """What is known of a caller: nothing, for a human of an organization identity has not seen yet."""

    mode: Mode | None = None
    """The mode its active API client is bound to; none for a person, who names it in ``Ledger-Mode``, and
    for a machine user that is no API client (refused)."""
    client: ApiVersion | None = None
    """The version its API client is pinned to."""
    organization: ApiVersion | None = None
    """The version its organization is pinned to."""
    revoked: bool = False
    """Whether its API client was revoked."""


NOTHING_KNOWN: Final = Standing()
"""How a caller stands when nothing is known of it."""


def resolve(sent: ApiVersion | None, standing: Standing) -> ApiVersion:
    """The version a request speaks: what it sent, else its client's pin, its organization's, the current."""
    for version in (sent, standing.client, standing.organization):
        if version is not None:
            return version
    return CURRENT_VERSION


@runtime_checkable
class StandingsPort(Protocol, metaclass=ABCMeta):
    """Where how a caller stands comes from: a module provides it (identity, from its API clients).

    The platform's port: a module declares ``provides = ["StandingsPort"]`` and hands over its implementation
    under ``STANDINGS_PORT`` (``runtime.standings``); the app binds it to its ``Authentication``, whose caller
    every route declares.
    The method is abstract so that it needs no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def of(self, principal: Principal) -> Standing:
        """What is known of ``principal``: its API client's mode, pin, revocation; its organization's pin."""
