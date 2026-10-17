"""What identity knows of a caller beyond its token (§9.2, §9.7): its pins, its mode, whether it was revoked.

An API client is a machine user registered with an organization, bound to one mode and pinned to a version
of the API; its organization is pinned too. The chassis asks identity for all of it through ``Standings`` once
the token is proven (``with_standing``), and the answer settles three things for the request: the version it
speaks (``resolve``), the mode it acts in (an API client's credential carries it: ``Ledger-Mode`` is then
optional, and may only repeat it), and whether a credential that was revoked still counts (it does not).
"""

from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import ClassVar, Final, Protocol, final, override

from ledger_kernel.errors import UnauthenticatedError
from ledger_platform.config.options import require_at_least, require_seconds
from ledger_platform.http.versions import CURRENT_VERSION
lazy from ledger_kernel.access import Principal, SubjectId
lazy from ledger_kernel.clock import MonotonicClock
lazy from ledger_kernel.ids import OrganizationId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.http.versions import ApiVersion


class ApiClientRevokedError(UnauthenticatedError):
    """The API client the token belongs to was revoked: its tokens no longer count, whatever their expiry."""

    code: ClassVar[str] = "api_client_revoked"

    def __init__(self) -> None:
        super().__init__("this API client was revoked")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Standing:
    """What is known of a caller: nothing, for a human of an organization identity has not seen yet."""

    mode: Mode | None = None
    """The mode its API client is bound to; none for a human, who names it in ``Ledger-Mode``."""
    client: ApiVersion | None = None
    """The version its API client is pinned to."""
    organization: ApiVersion | None = None
    """The version its organization is pinned to."""
    revoked: bool = False
    """Whether its API client was revoked."""


_NOTHING: Final = Standing()


def resolve(sent: ApiVersion | None, standing: Standing) -> ApiVersion:
    """The version a request speaks: what it sent, else its client's pin, its organization's, the current."""
    for version in (sent, standing.client, standing.organization):
        if version is not None:
            return version
    return CURRENT_VERSION


class Standings(Protocol, metaclass=ABCMeta):
    """Where how a caller stands comes from: identity keeps it (its API client, its organization).

    The method is abstract so that it needs no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def of(self, principal: Principal) -> Standing:
        """What is known of ``principal``: its API client's mode, pin, revocation; its organization's pin."""


@final
class CachedStandings(Standings):
    """``Standings`` remembered for ``ttl`` seconds, for at most ``size`` callers (the oldest go first).

    Every request asks for the standing of its caller, and it changes rarely (a pin is fixed when its owner is
    made; an API client is registered or revoked now and then): this keeps the question off the database. A
    change shows within ``ttl``: a revoked client works that long. Nothing is remembered of a question that
    failed, nor of an answer that knows nothing: it is what a caller of an organization that does not exist
    yet gets, and its next question must see the organization once it does.
    """

    __slots__ = ("_clock", "_inner", "_kept", "_size", "_ttl")

    def __init__(self, inner: Standings, *, ttl: float, size: int, clock: MonotonicClock = monotonic) -> None:
        require_seconds("standing", "ttl", ttl)
        require_at_least("standing", "size", size)
        self._inner = inner
        self._ttl = ttl
        self._size = size
        self._clock = clock
        self._kept: OrderedDict[tuple[OrganizationId, SubjectId], tuple[float, Standing]] = OrderedDict()

    @override
    async def of(self, principal: Principal) -> Standing:
        key, now = (principal.organization, principal.subject), self._clock()
        held = self._kept.get(key)
        if held is not None and now < held[0]:
            return held[1]
        standing = await self._inner.of(principal)
        _ = self._kept.pop(key, None)
        if standing != _NOTHING:
            self._kept[key] = (now + self._ttl, standing)
            while len(self._kept) > self._size:
                _ = self._kept.popitem(last=False)
        return standing
