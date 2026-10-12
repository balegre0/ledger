"""A permission cache that remembers what it was told to forget: for the tests of membership following."""

import asyncio
from typing import final

lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.ids import CompanyId, OrganizationId


@final
class Forgotten:
    """Records every ``forget`` and counts every ``forget_all``.

    Each sets ``arrived``, for a test waiting on events delivered by NATS.
    """

    __slots__ = ("arrived", "cleared", "forgotten")

    def __init__(self) -> None:
        self.forgotten: list[tuple[OrganizationId, CompanyId, SubjectId]] = []
        self.cleared = 0
        """How many times everything was forgotten."""
        self.arrived = asyncio.Event()

    def forget(self, organization: OrganizationId, company: CompanyId, subject: SubjectId) -> None:
        """Note that what is kept about ``subject`` in ``company`` was dropped."""
        self.forgotten.append((organization, company, subject))
        self.arrived.set()

    def forget_all(self) -> None:
        """Note that everything kept was dropped."""
        self.cleared += 1
        self.arrived.set()
