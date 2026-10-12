"""A permission cache that remembers what it was told to forget: for the tests of membership following."""

import asyncio
from typing import final

lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.ids import CompanyId, OrganizationId


@final
class Forgotten:
    """Records every ``forget``; each sets ``arrived``, for a test waiting on events delivered by NATS."""

    __slots__ = ("arrived", "forgotten")

    def __init__(self) -> None:
        self.forgotten: list[tuple[OrganizationId, CompanyId, SubjectId]] = []
        self.arrived = asyncio.Event()

    def forget(self, organization: OrganizationId, company: CompanyId, subject: SubjectId) -> None:
        """Note that what is kept about ``subject`` in ``company`` was dropped."""
        self.forgotten.append((organization, company, subject))
        self.arrived.set()
