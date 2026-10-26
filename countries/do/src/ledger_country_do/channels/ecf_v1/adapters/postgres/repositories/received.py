"""``ReceivedDocuments`` over one transaction (SQLAlchemy Core): the e-CF a company received.

Bound to its unit's tenant (a company in a mode), named in every statement; row-level security enforces it
again (§2). A document is received once per environment, issuer and e-NCF (``received_documents_number_key``,
Decision 16): a second one meets the key and is ``ReceivedExistsError``, with nothing written. What changes
is the company's commercial approval of it (``approval_*``: its decision, the ACECF signed, where sending it
to the emitter and to DGII stands, DGII's messages as ``[{code, text}]``) and its version; nothing is
deleted. A list is read in its page's direction.
"""

from typing import Final, TypedDict, final, override
lazy from collections.abc import Mapping
lazy from datetime import date, datetime
lazy from decimal import Decimal
lazy from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import Row, select, update
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import ColumnElement
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import ReceivedDocumentRecord
from ledger_country_do.channels.ecf_v1.application.ports import Archived
from ledger_country_do.channels.ecf_v1.application.received import (
    OutgoingApproval,
    PartySending,
    ReceivedDocument,
    ReceivedDocuments,
    ReceivedExistsError,
    SignedApproval,
)
from ledger_country_do.channels.ecf_v1.domain.approval import Decided, Decision, Sending
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.exchanges import InboundEcf
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage
from ledger_kernel.pagination import page_of
from ledger_platform.data import paged
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_kernel.tenancy import Mode, Tenant


class _StoredMessage(TypedDict):
    """One of DGII's messages as ``approval_dgii_messages`` keeps it."""

    code: str
    text: str


_MESSAGES: Final = TypeAdapter(list[_StoredMessage])
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
    ReceivedDocumentRecord.approval_decision,
    ReceivedDocumentRecord.approval_reason,
    ReceivedDocumentRecord.approval_requested_at,
    ReceivedDocumentRecord.approval_attempts,
    ReceivedDocumentRecord.approval_at,
    ReceivedDocumentRecord.approval_key,
    ReceivedDocumentRecord.approval_sha256,
    ReceivedDocumentRecord.approval_emitter,
    ReceivedDocumentRecord.approval_emitter_reason,
    ReceivedDocumentRecord.approval_sent_to_emitter_at,
    ReceivedDocumentRecord.approval_dgii,
    ReceivedDocumentRecord.approval_dgii_reason,
    ReceivedDocumentRecord.approval_dgii_messages,
    ReceivedDocumentRecord.approval_dgii_started_at,
    ReceivedDocumentRecord.approval_sent_to_dgii_at,
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
    str | None,
    str | None,
    datetime | None,
    int,
    datetime | None,
    str | None,
    bytes | None,
    str | None,
    str | None,
    datetime | None,
    str | None,
    str | None,
    list[dict[str, str]],
    datetime | None,
    datetime | None,
]
type _Party = tuple[str | None, str | None, datetime | None]
"""A party's columns: its state, why, when it took the ACECF."""


def _party(columns: _Party) -> PartySending:
    state, reason, sent_at = columns
    return PartySending(state=Sending(state or Sending.PENDING), reason=reason, sent_at=sent_at)


def _signed(at: datetime | None, key: str | None, sha256: bytes | None) -> SignedApproval | None:
    if at is None or key is None or sha256 is None:
        return None
    return SignedApproval(at=at, acecf=Archived(key=key, sha256=sha256))


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
        decision,
        reason,
        requested_at,
        attempts,
        approval_at,
        approval_key,
        approval_sha256,
        emitter,
        emitter_reason,
        sent_to_emitter_at,
        dgii,
        dgii_reason,
        messages,
        dgii_started_at,
        sent_to_dgii_at,
    ) = row
    kind = EcfType(ecf_type)
    approval = (
        None
        if decision is None or requested_at is None
        else OutgoingApproval(
            decision=Decision(decision),
            reason=reason,
            requested_at=requested_at,
            emitter=_party((emitter, emitter_reason, sent_to_emitter_at)),
            dgii=_party((dgii, dgii_reason, sent_to_dgii_at)),
            attempts=attempts,
            signed=_signed(approval_at, approval_key, approval_sha256),
            dgii_messages=tuple(
                DgiiMessage(message["code"], message["text"]).authority()
                for message in _MESSAGES.validate_python(messages)
            ),
            dgii_started_at=dgii_started_at,
        )
    )
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
        approval=approval,
    )


def _approval_columns(approval: OutgoingApproval | None) -> Mapping[str, object]:
    """The columns ``approval`` fills (all empty without one)."""
    if approval is None:
        return {}
    signed = approval.signed
    return {
        "approval_decision": approval.decision.value,
        "approval_reason": approval.reason,
        "approval_requested_at": approval.requested_at,
        "approval_attempts": approval.attempts,
        "approval_at": None if signed is None else signed.at,
        "approval_key": None if signed is None else signed.acecf.key,
        "approval_sha256": None if signed is None else signed.acecf.sha256,
        "approval_emitter": approval.emitter.state.value,
        "approval_emitter_reason": approval.emitter.reason,
        "approval_sent_to_emitter_at": approval.emitter.sent_at,
        "approval_dgii": approval.dgii.state.value,
        "approval_dgii_reason": approval.dgii.reason,
        "approval_dgii_messages": [
            {"code": message.code, "text": message.message} for message in approval.dgii_messages
        ],
        "approval_dgii_started_at": approval.dgii_started_at,
        "approval_sent_to_dgii_at": approval.dgii.sent_at,
    }


def _decided(decided: Decided) -> ColumnElement[bool]:
    if decided is Decided.NONE:
        return ReceivedDocumentRecord.approval_decision.is_(None)
    return ReceivedDocumentRecord.approval_decision == decided.value


@final
class PgReceived(ReceivedDocuments):
    """The e-CF one company received in one mode, in PostgreSQL."""

    __slots__ = ("_connection", "_mode", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant, mode: Mode) -> None:
        """``tenant`` is the unit's company, in ``mode`` (its own)."""
        self._connection = connection
        self._tenant = tenant
        self._mode = mode

    def _mine(self) -> tuple[ColumnElement[bool], ...]:
        return (
            ReceivedDocumentRecord.organization_id == self._tenant.organization,
            ReceivedDocumentRecord.company_id == self._tenant.company,
            ReceivedDocumentRecord.mode == self._mode.value,
        )

    @override
    async def find(self, environment: Environment, issuer_rnc: str, encf: Encf) -> ReceivedDocument | None:
        row = (
            await self._connection.execute(
                select(*_COLUMNS).where(
                    *self._mine(),
                    ReceivedDocumentRecord.environment == environment.value,
                    ReceivedDocumentRecord.issuer_rnc == issuer_rnc,
                    ReceivedDocumentRecord.ecf_type == encf.ecf_type.value,
                    ReceivedDocumentRecord.sequence == encf.sequence,
                )
            )
        ).one_or_none()
        return None if row is None else _received(row)

    @override
    async def get(self, document: UUID, *, lock: bool) -> ReceivedDocument | None:
        statement = select(*_COLUMNS).where(*self._mine(), ReceivedDocumentRecord.id == document)
        row = (
            await self._connection.execute(statement.with_for_update() if lock else statement)
        ).one_or_none()
        return None if row is None else _received(row)

    @override
    async def page(
        self,
        request: PageRequest,
        *,
        environment: Environment | None,
        issuer: str | None,
        decided: Decided | None,
    ) -> Page[ReceivedDocument]:
        statement = select(*_COLUMNS).where(*self._mine())
        if environment is not None:
            statement = statement.where(ReceivedDocumentRecord.environment == environment.value)
        if issuer is not None:
            statement = statement.where(ReceivedDocumentRecord.issuer_rnc == issuer)
        if decided is not None:
            statement = statement.where(_decided(decided))
        rows = (await self._connection.execute(paged(statement, ReceivedDocumentRecord.id, request))).all()
        return page_of([_received(row) for row in rows], request, key=lambda found: found.id)

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
                **_approval_columns(document.approval),
            )
            .on_conflict_do_nothing()
            .returning(ReceivedDocumentRecord.id)
        )
        if added.one_or_none() is None:
            raise ReceivedExistsError

    @override
    async def changed(self, document: ReceivedDocument) -> None:
        await self._connection.execute(
            update(ReceivedDocumentRecord)
            .where(*self._mine(), ReceivedDocumentRecord.id == document.id)
            .values(version=document.version, **_approval_columns(document.approval))
        )
