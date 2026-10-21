"""What an enrollment still requires, read when fiscal asks (Decision 3; ``domain.requirements``).

The signer is asked first, outside any unit (it is another module's port), whether a certificate covers now;
then one unit reads the environment's ranges. Nothing is kept: fiscal keeps what its review found.
"""

from abc import ABCMeta, abstractmethod
from typing import Protocol, final
lazy from collections.abc import Callable
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.domain.requirements import due
lazy from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnits
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.requirements import Requirement


class Certificates(Protocol, metaclass=ABCMeta):
    """Whether a company may sign: the signer's answer about its certificate."""

    @abstractmethod
    async def valid_at(self, who: SigningAs, moment: datetime) -> bool:
        """Whether ``who`` has a certificate whose validity covers ``moment``.

        ``SignerUnavailableError`` when the signer cannot be reached.
        """


@final
class Requiring:
    """What an enrollment requires (see the module)."""

    __slots__ = ("_certificates", "_clock", "_receiver", "_units")

    def __init__(
        self,
        units: NumberingUnits,
        certificates: Certificates,
        *,
        receiver: bool,
        clock: Callable[[], datetime],
    ) -> None:
        """``receiver``: whether the receiver's public URL is configured."""
        self._units = units
        self._certificates = certificates
        self._receiver = receiver
        self._clock = clock

    async def due(self, who: SigningAs, environment: Environment) -> tuple[Requirement, ...]:
        """What ``who``'s enrollment in ``environment`` still requires, now."""
        now = self._clock()
        certificate = await self._certificates.valid_at(who, now)
        async with self._units.unit(who.tenant) as unit:
            ranges = await unit.ranges.of(environment)
        return due(certificate=certificate, ranges=ranges, now=now, receiver=self._receiver)
