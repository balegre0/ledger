"""``ReceivedDocuments`` over one transaction (SQLAlchemy Core): the e-CF a company received.

Bound to its unit's tenant (a company in a mode), named in every statement; row-level security enforces it
again (§2). A document is received once per environment, issuer and e-NCF (``received_documents_number_key``,
Decision 16): a second one meets the key and is ``ReceivedExistsError``, with nothing written. Nothing is
changed or deleted here.
"""

from typing import final, override
lazy from datetime import date, datetime
lazy from decimal import Decimal
lazy from uuid import UUID

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import ReceivedDocumentRecord
from ledger_country_do.channels.ecf_v1.application.ports import Archived
from ledger_country_do.channels.ecf_v1.application.received import (
    ReceivedDocument,
    ReceivedDocuments,
    ReceivedExistsError,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.exchanges import InboundEcf
lazy from ledger_kernel.tenancy import Mode, Tenant

_COLUMNS = (
    ReceivedDocumentRecord.id,
    ReceivedDocumentRecord.environment,
    ReceivedDocumentRecord.issuer_rnc,
    ReceivedDocumentRecord.issuer_name,
    ReceivedDocumentRecord.ecf_type,
    ReceivedDocumentRecord.sequence,
    ReceivedDocumentRecord.buyer_rnc,
    ReceivedDocumentRecord.issued_on,
    ReceivedDocumentRecord.total,
    ReceivedDocumentRecord.itbis,
    ReceivedDocumentRecord.signed_at,
    ReceivedDocumentRecord.security_code,
    ReceivedDocumentRecord.received_at,
    ReceivedDocumentRecord.xml_key,
    ReceivedDocumentRecord.xml_sha256,
    ReceivedDocumentRecord.arecf_key,
    ReceivedDocumentRecord.arecf_sha256,
    ReceivedDocumentRecord.version,
)
type _Row = Row[
    UUID,
    str,
    str,
    str,
    str,
    int,
    str | None,
    date,
    Decimal,
    Decimal | None,
    datetime,
    str,
    datetime,
    str,
    bytes,
    str,
    bytes,
    int,
]


def _received(row: _Row) -> ReceivedDocument:
    (
        identifier,
        environment,
        issuer_rnc,
        issuer_name,
        ecf_type,
        sequence,
        buyer_rnc,
        issued_on,
        total,
        itbis,
        signed_at,
        security_code,
        received_at,
        xml_key,
        xml_sha256,
        arecf_key,
        arecf_sha256,
        version,
    ) = row
    kind = EcfType(ecf_type)
    return ReceivedDocument(
        id=identifier,
        environment=Environment(environment),
        ecf=InboundEcf(
            ecf_type=kind,
            encf=Encf(kind, sequence),
            issuer_rnc=issuer_rnc,
            issuer_name=issuer_name,
            buyer_rnc=buyer_rnc,
            issued_on=issued_on,
            total=total,
            itbis=itbis,
            signed_at=signed_at,
            security_code=security_code,
        ),
        received_at=received_at,
        document=Archived(key=xml_key, sha256=xml_sha256),
        receipt=Archived(key=arecf_key, sha256=arecf_sha256),
        version=version,
    )


@final
class PgReceived(ReceivedDocuments):
    """The e-CF one company received in one mode, in PostgreSQL."""

    __slots__ = ("_connection", "_mode", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant, mode: Mode) -> None:
        """``tenant`` is the unit's company, in ``mode`` (its own)."""
        self._connection = connection
        self._tenant = tenant
        self._mode = mode

    @override
    async def find(self, environment: Environment, issuer_rnc: str, encf: Encf) -> ReceivedDocument | None:
        row = (
            await self._connection.execute(
                select(*_COLUMNS).where(
                    ReceivedDocumentRecord.organization_id == self._tenant.organization,
                    ReceivedDocumentRecord.company_id == self._tenant.company,
                    ReceivedDocumentRecord.mode == self._mode.value,
                    ReceivedDocumentRecord.environment == environment.value,
                    ReceivedDocumentRecord.issuer_rnc == issuer_rnc,
                    ReceivedDocumentRecord.ecf_type == encf.ecf_type.value,
                    ReceivedDocumentRecord.sequence == encf.sequence,
                )
            )
        ).one_or_none()
        return None if row is None else _received(row)

    @override
    async def add(self, document: ReceivedDocument) -> None:
        ecf = document.ecf
        added = await self._connection.execute(
            insert(ReceivedDocumentRecord)
            .values(
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                mode=self._mode.value,
                id=document.id,
                environment=document.environment.value,
                issuer_rnc=ecf.issuer_rnc,
                issuer_name=ecf.issuer_name,
                ecf_type=ecf.ecf_type.value,
                sequence=ecf.encf.sequence,
                buyer_rnc=ecf.buyer_rnc,
                issued_on=ecf.issued_on,
                total=ecf.total,
                itbis=ecf.itbis,
                signed_at=ecf.signed_at,
                security_code=ecf.security_code,
                received_at=document.received_at,
                xml_key=document.document.key,
                xml_sha256=document.document.sha256,
                arecf_key=document.receipt.key,
                arecf_sha256=document.receipt.sha256,
                version=document.version,
            )
            .on_conflict_do_nothing()
            .returning(ReceivedDocumentRecord.id)
        )
        if added.one_or_none() is None:
            raise ReceivedExistsError
