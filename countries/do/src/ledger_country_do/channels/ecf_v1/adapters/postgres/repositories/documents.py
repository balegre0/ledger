"""``Documents`` over one transaction (SQLAlchemy Core): a company's e-CF documents in one mode.

The repository is bound to its unit's tenant (a company in a mode) and names it in every statement;
row-level security enforces it again (§2). A document is read with its range's last day (a join: a range
keeps its authorization as registered). Its draft is the platform codec's JSON (``encode``), read back by
fiscal-kit's ``read_draft``. A second document for an invoice (a request that raced the one that
committed first) is the table's refusal (``documents_invoice_key``), raised as ``DocumentExistsError``.
A signature's columns are all there or none: the table checks it
(``ck_documents_signature``), so a row is signed exactly when it has them all. Past signing, a change keeps
the submission's mark, DGII's track id, code, ``secuenciaUtilizada`` and messages (``[{code, text}]``),
how often DGII was asked, delivery (since when, the attempts failed, why, the buyer's receipt's key and
SHA-256, both or neither), the number's fate, and its buyer's commercial approval (``{approved, reason,
at}``, the moment in ISO 8601). A document is found by its number too, the one that holds it
(``documents_number``: a released number is held by none).
"""

from typing import Final, TypedDict, final, override
lazy from collections.abc import Collection, Mapping
lazy from datetime import date, datetime
lazy from uuid import UUID

from psycopg.errors import UniqueViolation
from pydantic import TypeAdapter
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
lazy from sqlalchemy import ColumnElement, Row, Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import DocumentRecord, NumberingRangeRecord
from ledger_country_do.channels.ecf_v1.application.documents import (
    Approval,
    DocumentExistsError,
    Documents,
    EcfDocument,
    Signature,
)
from ledger_country_do.channels.ecf_v1.application.ports import Archived
from ledger_country_do.channels.ecf_v1.domain.ecf import DeliveryState, EcfDocumentState, EmissionMode
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.references import AcceptedEcf
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage, NumberFate
from ledger_fiscal_kit.draft_codec import read_draft
from ledger_fiscal_kit.drafts import DocumentRef
from ledger_platform.data.codec import JsonValue, encode
lazy from ledger_kernel.tenancy import Mode, Tenant


class _StoredMessage(TypedDict):
    """One of DGII's messages as ``documents.messages`` keeps it."""

    code: str
    text: str


class _StoredApproval(TypedDict):
    """A buyer's commercial approval as ``documents.commercial_approval`` keeps it."""

    approved: bool
    reason: str | None
    at: datetime


_DRAFT: Final = TypeAdapter[JsonValue](JsonValue)
_MESSAGES: Final = TypeAdapter(list[_StoredMessage])
_APPROVAL: Final = TypeAdapter(_StoredApproval | None)
ONE_PER_INVOICE: Final = "documents_invoice_key"
"""The constraint that gives an invoice one document."""
_SIGNATURE_COLUMNS: Final[tuple[str, ...]] = (
    "signed_at",
    "security_code",
    "qr_url",
    "emission_mode",
    "ecf_key",
    "ecf_sha256",
    "rfce_key",
    "rfce_sha256",
)
_ACCEPTED: Final = (EcfDocumentState.ACCEPTED.value, EcfDocumentState.ACCEPTED_CONDITIONALLY.value)
_DOCUMENT: Final = select(
    DocumentRecord.id,
    DocumentRecord.invoice_id,
    DocumentRecord.environment,
    DocumentRecord.ecf_type,
    DocumentRecord.sequence,
    DocumentRecord.range_id,
    NumberingRangeRecord.expires_on,
    DocumentRecord.state,
    DocumentRecord.route,
    DocumentRecord.delivery_state,
    DocumentRecord.issued_on,
    DocumentRecord.signed_at,
    DocumentRecord.security_code,
    DocumentRecord.qr_url,
    DocumentRecord.emission_mode,
    DocumentRecord.ecf_key,
    DocumentRecord.ecf_sha256,
    DocumentRecord.rfce_key,
    DocumentRecord.rfce_sha256,
    DocumentRecord.draft,
    DocumentRecord.version,
    DocumentRecord.created_at,
    DocumentRecord.updated_at,
    DocumentRecord.submission_started_at,
    DocumentRecord.track_id,
    DocumentRecord.track_id_since,
    DocumentRecord.polls,
    DocumentRecord.outcome_code,
    DocumentRecord.sequence_used,
    DocumentRecord.number_fate,
    DocumentRecord.messages,
    DocumentRecord.delivery_since,
    DocumentRecord.delivery_attempts,
    DocumentRecord.delivery_reason,
    DocumentRecord.receipt_key,
    DocumentRecord.receipt_sha256,
    DocumentRecord.commercial_approval,
).join(
    NumberingRangeRecord,
    (NumberingRangeRecord.organization_id == DocumentRecord.organization_id)
    & (NumberingRangeRecord.company_id == DocumentRecord.company_id)
    & (NumberingRangeRecord.mode == DocumentRecord.mode)
    & (NumberingRangeRecord.id == DocumentRecord.range_id),
)
type _Row = Row[
    UUID,
    UUID,
    str,
    str,
    int,
    UUID,
    date | None,
    str,
    str,
    str,
    date,
    datetime | None,
    str | None,
    str | None,
    str | None,
    str | None,
    bytes | None,
    str | None,
    bytes | None,
    dict[str, object],
    int,
    datetime,
    datetime,
    datetime | None,
    str | None,
    datetime | None,
    int,
    int | None,
    bool | None,
    str | None,
    list[dict[str, str]],
    datetime | None,
    int,
    str | None,
    str | None,
    bytes | None,
    dict[str, object] | None,
]
type _Query = Select[
    UUID,
    UUID,
    str,
    str,
    int,
    UUID,
    date | None,
    str,
    str,
    str,
    date,
    datetime | None,
    str | None,
    str | None,
    str | None,
    str | None,
    bytes | None,
    str | None,
    bytes | None,
    dict[str, object],
    int,
    datetime,
    datetime,
    datetime | None,
    str | None,
    datetime | None,
    int,
    int | None,
    bool | None,
    str | None,
    list[dict[str, str]],
    datetime | None,
    int,
    str | None,
    str | None,
    bytes | None,
    dict[str, object] | None,
]


type _Signed = tuple[
    datetime | None, str | None, str | None, str | None, str | None, bytes | None, str | None, bytes | None
]
"""A row's signature columns, in ``_DOCUMENT``'s order: ``signed_at`` to ``rfce_sha256``."""


def _signature(columns: _Signed) -> Signature | None:
    """The signature ``columns`` record, or ``None`` before signing (see the module)."""
    signed_at, code, qr_url, mode, ecf_key, ecf_sha256, rfce_key, rfce_sha256 = columns
    if (
        signed_at is None
        or code is None
        or qr_url is None
        or mode is None
        or ecf_key is None
        or ecf_sha256 is None
    ):
        return None
    return Signature(
        signed_at=signed_at,
        security_code=code,
        qr_url=qr_url,
        emission_mode=EmissionMode(mode),
        ecf=Archived(key=ecf_key, sha256=ecf_sha256),
        rfce=None if rfce_key is None or rfce_sha256 is None else Archived(key=rfce_key, sha256=rfce_sha256),
    )


def _document(row: _Row) -> EcfDocument:
    (
        identifier,
        invoice,
        environment,
        ecf_type,
        sequence,
        range_id,
        expires_on,
        state,
        route,
        delivery,
        issued_on,
        signed_at,
        code,
        qr_url,
        mode,
        ecf_key,
        ecf_sha256,
        rfce_key,
        rfce_sha256,
        draft,
        version,
        created_at,
        updated_at,
        mark,
        track_id,
        since,
        polls,
        outcome_code,
        sequence_used,
        fate,
        messages,
        delivery_since,
        delivery_attempts,
        delivery_reason,
        receipt_key,
        receipt_sha256,
        approval,
    ) = row
    return EcfDocument(
        id=identifier,
        invoice=DocumentRef(invoice),
        environment=Environment(environment),
        encf=Encf(EcfType(ecf_type), sequence),
        range_id=range_id,
        range_expires_on=expires_on,
        state=EcfDocumentState(state),
        route=Route(route),
        delivery=DeliveryState(delivery),
        issued_on=issued_on,
        signature=_signature((signed_at, code, qr_url, mode, ecf_key, ecf_sha256, rfce_key, rfce_sha256)),
        draft=read_draft(_DRAFT.validate_python(draft)),
        version=version,
        created_at=created_at,
        updated_at=updated_at,
        submission_started_at=mark,
        track_id=track_id,
        track_id_since=since,
        polls=polls,
        outcome_code=outcome_code,
        sequence_used=sequence_used,
        number_fate=None if fate is None else NumberFate(fate),
        messages=tuple(
            DgiiMessage(message["code"], message["text"]).authority()
            for message in _MESSAGES.validate_python(messages)
        ),
        delivery_since=delivery_since,
        delivery_attempts=delivery_attempts,
        delivery_reason=delivery_reason,
        receipt=None
        if receipt_key is None or receipt_sha256 is None
        else Archived(key=receipt_key, sha256=receipt_sha256),
        commercial_approval=_approval(_APPROVAL.validate_python(approval)),
    )


def _approval(stored: _StoredApproval | None) -> Approval | None:
    if stored is None:
        return None
    return Approval(approved=stored["approved"], reason=stored["reason"], at=stored["at"])


def _stored(approval: Approval | None) -> Mapping[str, object] | None:
    if approval is None:
        return None
    return {"approved": approval.approved, "reason": approval.reason, "at": approval.at.isoformat()}


def _progress_columns(document: EcfDocument) -> Mapping[str, object]:
    """What a document records past signing: its mark, DGII's answer, its number's fate, its delivery."""
    return {
        "delivery_state": document.delivery.value,
        "submission_started_at": document.submission_started_at,
        "track_id": document.track_id,
        "track_id_since": document.track_id_since,
        "polls": document.polls,
        "outcome_code": document.outcome_code,
        "sequence_used": document.sequence_used,
        "number_fate": None if document.number_fate is None else document.number_fate.value,
        "messages": [{"code": message.code, "text": message.message} for message in document.messages],
        "delivery_since": document.delivery_since,
        "delivery_attempts": document.delivery_attempts,
        "delivery_reason": document.delivery_reason,
        "receipt_key": None if document.receipt is None else document.receipt.key,
        "receipt_sha256": None if document.receipt is None else document.receipt.sha256,
        "commercial_approval": _stored(document.commercial_approval),
    }


def _signed_columns(signature: Signature | None) -> Mapping[str, object]:
    """The columns ``signature`` fills (all ``None`` without one)."""
    if signature is None:
        return dict.fromkeys(_SIGNATURE_COLUMNS)
    rfce = signature.rfce
    return {
        "signed_at": signature.signed_at,
        "security_code": signature.security_code,
        "qr_url": signature.qr_url,
        "emission_mode": signature.emission_mode.value,
        "ecf_key": signature.ecf.key,
        "ecf_sha256": signature.ecf.sha256,
        "rfce_key": None if rfce is None else rfce.key,
        "rfce_sha256": None if rfce is None else rfce.sha256,
    }


@final
class PgDocuments(Documents):
    """The e-CF documents of one company in one mode, in PostgreSQL."""

    __slots__ = ("_connection", "_mode", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant, mode: Mode) -> None:
        """``tenant`` is the unit's company, in ``mode`` (its own)."""
        self._connection = connection
        self._tenant = tenant
        self._mode = mode

    def _owner(self) -> dict[str, object]:
        return {
            "organization_id": self._tenant.organization,
            "company_id": self._tenant.company,
            "mode": self._mode.value,
        }

    def _mine(self) -> tuple[ColumnElement[bool], ...]:
        owner = self._owner()
        return (
            DocumentRecord.organization_id == owner["organization_id"],
            DocumentRecord.company_id == owner["company_id"],
            DocumentRecord.mode == owner["mode"],
        )

    async def _one(self, statement: _Query) -> EcfDocument | None:
        row = (await self._connection.execute(statement)).one_or_none()
        return None if row is None else _document(row)

    @override
    async def of_invoice(self, invoice: DocumentRef) -> EcfDocument | None:
        return await self._one(_DOCUMENT.where(*self._mine(), DocumentRecord.invoice_id == invoice))

    @override
    async def get(self, document: UUID, *, lock: bool) -> EcfDocument | None:
        statement = _DOCUMENT.where(*self._mine(), DocumentRecord.id == document)
        return await self._one(statement.with_for_update(of=DocumentRecord) if lock else statement)

    @override
    async def by_number(self, environment: Environment, encf: Encf) -> EcfDocument | None:
        statement = _DOCUMENT.where(
            *self._mine(),
            DocumentRecord.environment == environment.value,
            DocumentRecord.ecf_type == encf.ecf_type.value,
            DocumentRecord.sequence == encf.sequence,
            DocumentRecord.number_fate.is_distinct_from(NumberFate.RELEASED.value),
        )
        return await self._one(statement.with_for_update(of=DocumentRecord))

    @override
    async def add(self, document: EcfDocument) -> None:
        try:
            await self._inserted(document)
        except IntegrityError as error:
            refusal = error.orig
            if isinstance(refusal, UniqueViolation) and refusal.diag.constraint_name == ONE_PER_INVOICE:
                raise DocumentExistsError from error
            raise

    async def _inserted(self, document: EcfDocument) -> None:
        await self._connection.execute(
            insert(DocumentRecord).values(
                **self._owner(),
                id=document.id,
                invoice_id=document.invoice,
                environment=document.environment.value,
                ecf_type=document.encf.ecf_type.value,
                sequence=document.encf.sequence,
                range_id=document.range_id,
                state=document.state.value,
                route=document.route.value,
                issued_on=document.issued_on,
                **_progress_columns(document),
                **_signed_columns(document.signature),
                draft=encode(document.draft),
                version=document.version,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
        )

    @override
    async def changed(self, document: EcfDocument) -> None:
        await self._connection.execute(
            update(DocumentRecord)
            .where(*self._mine(), DocumentRecord.id == document.id)
            .values(
                state=document.state.value,
                **_signed_columns(document.signature),
                **_progress_columns(document),
                version=document.version,
                updated_at=document.updated_at,
            )
        )

    @override
    async def accepted(self, invoices: Collection[DocumentRef]) -> Mapping[DocumentRef, AcceptedEcf]:
        if not invoices:
            return {}
        rows = await self._connection.execute(
            select(
                DocumentRecord.invoice_id,
                DocumentRecord.ecf_type,
                DocumentRecord.sequence,
                DocumentRecord.issued_on,
            ).where(
                *self._mine(),
                DocumentRecord.invoice_id.in_(tuple(invoices)),
                DocumentRecord.state.in_(_ACCEPTED),
            )
        )
        return {
            DocumentRef(invoice): AcceptedEcf(encf=Encf(EcfType(ecf_type), sequence), issued_on=issued_on)
            for invoice, ecf_type, sequence, issued_on in rows.all()
        }
