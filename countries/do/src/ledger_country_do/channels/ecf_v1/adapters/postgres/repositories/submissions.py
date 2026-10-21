"""``Submissions`` over one transaction (SQLAlchemy Core): the track ids a company's documents hold.

Bound to its unit's tenant (a company in a mode), named in every statement; row-level security enforces it
again (§2). A track id is recorded once per document (its key), kept as first recorded, never changed or
deleted (the channel's role may not): a released number's next document must know its predecessors' track
ids (Decision 9), which ``excluded`` finds by the number (``submissions_number``).
"""

from typing import final, override
lazy from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import SubmissionRecord
from ledger_country_do.channels.ecf_v1.application.documents import Submissions
lazy from ledger_country_do.channels.ecf_v1.application.documents import EcfDocument
lazy from ledger_kernel.tenancy import Mode, Tenant


@final
class PgSubmissions(Submissions):
    """The track ids of one company's documents in one mode, in PostgreSQL."""

    __slots__ = ("_connection", "_mode", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant, mode: Mode) -> None:
        """``tenant`` is the unit's company, in ``mode`` (its own)."""
        self._connection = connection
        self._tenant = tenant
        self._mode = mode

    @override
    async def add(
        self, document: EcfDocument, track_id: str, *, at: datetime, received_at: datetime | None
    ) -> None:
        await self._connection.execute(
            insert(SubmissionRecord)
            .values(
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                mode=self._mode.value,
                document_id=document.id,
                track_id=track_id,
                environment=document.environment.value,
                ecf_type=document.encf.ecf_type.value,
                sequence=document.encf.sequence,
                submitted_at=at,
                received_at=received_at,
            )
            .on_conflict_do_nothing()
        )

    @override
    async def excluded(self, document: EcfDocument) -> frozenset[str]:
        rows = await self._connection.execute(
            select(SubmissionRecord.track_id).where(
                SubmissionRecord.organization_id == self._tenant.organization,
                SubmissionRecord.company_id == self._tenant.company,
                SubmissionRecord.mode == self._mode.value,
                SubmissionRecord.environment == document.environment.value,
                SubmissionRecord.ecf_type == document.encf.ecf_type.value,
                SubmissionRecord.sequence == document.encf.sequence,
                SubmissionRecord.document_id != document.id,
            )
        )
        return frozenset(rows.scalars().all())
