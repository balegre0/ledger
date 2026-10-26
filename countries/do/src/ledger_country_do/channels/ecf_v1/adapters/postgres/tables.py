"""do-ecf-v1's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

Every table holds a company's rows in one mode (row-level security scope ``tenant``), the tenant's columns
leading its keys (§6.3), in schema ``do_ecf`` (§6.2):

- ``numbering_series``: one row per series (an environment and an e-CF type); its lock serializes the
  series' registrations and allocations (``SELECT ... FOR UPDATE`` by way of an upsert);
- ``numbering_ranges``: DGII's authorized ranges of a series, ``next`` the number each gives next (``last +
  1``: spent), within ten digits;
- ``released_numbers``: numbers DGII rejected without using them, allocatable from ``available_at`` on
  (Decision 9);
- ``documents``: the e-CF numbered from them, one per invoice (Decision 35), each number held by one document
  until DGII releases it; the draft it was numbered for (``draft``, the platform codec's JSON), where it
  stands and, once signed, the keys and SHA-256 of what the archive keeps (§6.8: never the bytes); once
  submitted, the submission's mark, DGII's track id and answer; once accepted, its delivery to the buyer
  (since when, the attempts failed, why, the key and SHA-256 of the buyer's receipt);
- ``submissions``: every track id a document was given or adopted, by its number (Decision 9);
- ``received_documents``: the e-CF other emitters sent the company and the receiver received (§3.8), each
  once per environment, issuer and e-NCF (Decision 16), with the keys and SHA-256 of what the archive keeps
  of it and of the receipt it was answered with, and the company's commercial approval of it (Task 23: its
  decision, the ACECF signed, where sending it to the emitter and to DGII stands);
- ``company_options``: how a company delivers to its buyers in a mode (Decision 19).

Outside row-level security by design (§6.3), the receiver's own credentials: ``receiver_seeds`` (a seed's
SHA-256, never the seed) and ``receiver_tokens`` (a token's SHA-256, never the token), each naming the
company and environment it serves; only the receiver's code reads them.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID
lazy from enum import StrEnum

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from ledger_country_do.channels.ecf_v1.domain.approval import Decision, Sending
from ledger_country_do.channels.ecf_v1.domain.ecf import DeliveryState, EcfDocumentState, EmissionMode
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import RangeStatus
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.channels.ecf_v1.domain.status_map import NumberFate
from ledger_kernel.tenancy import Mode
from ledger_platform.data import ModuleTable

SCHEMA: Final = "do_ecf"
"""The channel's schema: its tables and the chassis's (``chassis_schema_v1`` and ``_v2``), under its roles."""
TENANT: Final = ("organization_id", "company_id", "mode")
SERIES: Final = (*TENANT, "environment", "ecf_type")


def _one_of(column: str, words: type[StrEnum], *, name: str) -> CheckConstraint:
    """``column`` holds one of ``words``' values: the domain's list, which the migration spells out."""
    allowed = ", ".join(f"'{word}'" for word in words)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


def _series_words(table: str) -> tuple[CheckConstraint, ...]:
    """A table's mode, environment and e-CF type are the domain's words.

    The live environment's rows are the live mode's, and only they are (``ChannelDef.mode_of``).
    """
    return (
        _one_of("mode", Mode, name=f"ck_{table}_mode"),
        _one_of("environment", Environment, name=f"ck_{table}_environment"),
        _one_of("ecf_type", EcfType, name=f"ck_{table}_ecf_type"),
        CheckConstraint("(environment = 'ecf') = (mode = 'live')", name=f"ck_{table}_live"),
    )


def _of_series(table: str) -> ForeignKeyConstraint:
    """The key from ``table``'s series to its row: the series exists before anything of it."""
    return ForeignKeyConstraint(
        list(SERIES), [f"{SCHEMA}.numbering_series.{column}" for column in SERIES], name=f"fk_{table}_series"
    )


class DoEcfTable(ModuleTable, schema=SCHEMA):
    """The channel's own tables, in its schema; the chassis's are ``chassis_tables("do_ecf")``."""


class NumberingSeriesRecord(DoEcfTable):
    """A series: the company's ranges of one e-CF type in one environment. Its row is the series' lock."""

    __tablename__: str = "numbering_series"
    __table_args__: tuple[SchemaItem, ...] = (
        *_series_words("numbering_series"),
        CheckConstraint("version > 0", name="ck_numbering_series_version"),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    environment: Mapped[str] = mapped_column(Text, primary_key=True)
    ecf_type: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger)
    """How many times the series was locked: one for each registration or allocation."""


class NumberingRangeRecord(DoEcfTable):
    """A DGII-authorized range of a series, and the number it gives next.

    A trigger of the revision guards it: its authorization never changes, ``next`` never goes back, and no
    two ranges of a series share a number.
    """

    __tablename__: str = "numbering_ranges"
    __table_args__: tuple[SchemaItem, ...] = (
        _of_series("numbering_ranges"),
        *_series_words("numbering_ranges"),
        _one_of("status", RangeStatus, name="ck_numbering_ranges_status"),
        CheckConstraint(
            "1 <= first AND first <= next AND next <= last + 1 AND last <= 9999999999",
            name="ck_numbering_ranges_numbers",
        ),
        CheckConstraint("version > 0", name="ck_numbering_ranges_version"),
        Index("numbering_ranges_series", *SERIES),
        UniqueConstraint(*SERIES, "id", name="numbering_ranges_series_key"),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(Text)
    ecf_type: Mapped[str] = mapped_column(Text)
    first: Mapped[int] = mapped_column(BigInteger)
    last: Mapped[int] = mapped_column(BigInteger)
    next: Mapped[int] = mapped_column(BigInteger)
    expires_on: Mapped[date | None] = mapped_column(Date)
    """Its last day, in Santo Domingo; ``None`` for a 32 or 34 DGII authorized undated."""
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger)


class ReleasedNumberRecord(DoEcfTable):
    """A number DGII rejected without using it (``secuenciaUtilizada`` false), and when it may be taken."""

    __tablename__: str = "released_numbers"
    __table_args__: tuple[SchemaItem, ...] = (
        _of_series("released_numbers"),
        *_series_words("released_numbers"),
        CheckConstraint("sequence BETWEEN 1 AND 9999999999", name="ck_released_numbers_sequence"),
        CheckConstraint("available_at >= released_at", name="ck_released_numbers_available"),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    environment: Mapped[str] = mapped_column(Text, primary_key=True)
    ecf_type: Mapped[str] = mapped_column(Text, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    document_id: Mapped[UUID] = mapped_column()
    """The document whose rejection released it: its track ids are never adopted by the next one."""


UNSIGNED: Final = (EcfDocumentState.NUMBERED, EcfDocumentState.BLOCKED, EcfDocumentState.VOIDED)
"""The states of a document that holds no signature: before signing, or kept from it."""
_UNSIGNED: Final = ", ".join(f"'{state}'" for state in UNSIGNED)
SIGNATURE_CHECK: Final = " AND ".join(
    (
        f"(signed_at IS NULL) = (state IN ({_UNSIGNED}))",
        "(signed_at IS NULL) = (security_code IS NULL)",
        "(signed_at IS NULL) = (qr_url IS NULL)",
        "(signed_at IS NULL) = (emission_mode IS NULL)",
        "(signed_at IS NULL) = (ecf_key IS NULL)",
        "(ecf_key IS NULL) = (ecf_sha256 IS NULL)",
    )
)
"""A document is signed exactly in the states after signing, and then with all a signature records."""
_SUMMARIZED: Final = "(rfce_key IS NOT NULL) = (route = 'rfce' AND signed_at IS NOT NULL)"
RFCE_CHECK: Final = f"{_SUMMARIZED} AND (rfce_key IS NULL) = (rfce_sha256 IS NULL)"
"""A summary is archived exactly for a signed document of the RFCE route."""
DIGESTS_CHECK: Final = (
    "(ecf_sha256 IS NULL OR octet_length(ecf_sha256) = 32) "
    "AND (rfce_sha256 IS NULL OR octet_length(rfce_sha256) = 32)"
)
HELD_NUMBER: Final = "number_fate IS DISTINCT FROM 'released'"
"""A number a document holds: any but one DGII released (another document may take it again)."""
SUBMITTED: Final = (
    EcfDocumentState.SUBMITTED,
    EcfDocumentState.IN_PROCESS,
    EcfDocumentState.OUTCOME_UNKNOWN,
    EcfDocumentState.ACCEPTED,
    EcfDocumentState.ACCEPTED_CONDITIONALLY,
    EcfDocumentState.REJECTED,
)
"""The states a document reaches only through a submission, which marked it first."""
_SUBMITTED: Final = ", ".join(f"'{state}'" for state in SUBMITTED)
SUBMISSION_CHECK: Final = (
    f"(state IN ({_UNSIGNED}) AND submission_started_at IS NULL) OR state = 'signed' "
    f"OR (state IN ({_SUBMITTED}) AND submission_started_at IS NOT NULL)"
)
"""Marked only once signed, and on every document past a submission (Decision 8)."""
TRACKED_CHECK: Final = "state NOT IN ('submitted', 'in_process') OR track_id IS NOT NULL"
"""DGII deliberates about a document by its track id."""
UNKNOWN_CHECK: Final = "state <> 'outcome_unknown' OR track_id_since IS NOT NULL"
"""An outcome unknown since a moment: the track-id window runs from it (Decision 7)."""
_DECIDED: Final = "'accepted', 'accepted_conditionally', 'rejected'"
FATE_CHECK: Final = (
    f"(number_fate IS NOT NULL) = (state IN ({_DECIDED})) "
    "AND (state <> 'rejected' OR number_fate IN ('burned', 'released')) "
    "AND (state NOT IN ('accepted', 'accepted_conditionally') OR number_fate = 'used')"
)
"""A number's fate is known exactly once DGII decided: used if accepted, burned or released if rejected."""
RECEIPT_CHECK: Final = (
    "(receipt_key IS NULL) = (receipt_sha256 IS NULL) "
    "AND (receipt_sha256 IS NULL OR octet_length(receipt_sha256) = 32)"
)
"""A buyer's receipt is kept by its key and SHA-256, both or neither."""
DELIVERING_CHECK: Final = "delivery_state <> 'delivering' OR delivery_since IS NOT NULL"
"""A delivery under way started at a moment: its seven days run from it (Decision 21)."""


class DocumentRecord(DoEcfTable):
    """An e-CF: the invoice it is of, its number, where it stands, what the archive keeps of it once signed.

    The number is its series' (``environment``, ``ecf_type``) ``sequence``, from the range ``range_id``.
    Nothing deletes a document: its number is accounted for for ever (§8.6.3).
    """

    __tablename__: str = "documents"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(*TENANT, "invoice_id", name="documents_invoice_key"),
        ForeignKeyConstraint(
            [*SERIES, "range_id"],
            [f"{SCHEMA}.numbering_ranges.{column}" for column in (*SERIES, "id")],
            name="fk_documents_range",
        ),
        *_series_words("documents"),
        _one_of("state", EcfDocumentState, name="ck_documents_state"),
        _one_of("route", Route, name="ck_documents_route"),
        _one_of("delivery_state", DeliveryState, name="ck_documents_delivery_state"),
        _one_of("emission_mode", EmissionMode, name="ck_documents_emission_mode"),
        _one_of("number_fate", NumberFate, name="ck_documents_number_fate"),
        CheckConstraint("sequence BETWEEN 1 AND 9999999999", name="ck_documents_sequence"),
        CheckConstraint(SIGNATURE_CHECK, name="ck_documents_signature"),
        CheckConstraint(RFCE_CHECK, name="ck_documents_rfce"),
        CheckConstraint(DIGESTS_CHECK, name="ck_documents_digests"),
        CheckConstraint("version > 0", name="ck_documents_version"),
        CheckConstraint(SUBMISSION_CHECK, name="ck_documents_submission"),
        CheckConstraint(TRACKED_CHECK, name="ck_documents_track_id"),
        CheckConstraint(UNKNOWN_CHECK, name="ck_documents_unknown"),
        CheckConstraint(FATE_CHECK, name="ck_documents_fate"),
        CheckConstraint("polls >= 0", name="ck_documents_polls"),
        CheckConstraint(RECEIPT_CHECK, name="ck_documents_receipt"),
        CheckConstraint(DELIVERING_CHECK, name="ck_documents_delivering"),
        CheckConstraint("delivery_attempts >= 0", name="ck_documents_delivery_attempts"),
        Index("documents_number", *SERIES, "sequence", unique=True, postgresql_where=text(HELD_NUMBER)),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column()
    """The commercial document (invoicing's invoice) it is the e-CF of."""
    environment: Mapped[str] = mapped_column(Text)
    ecf_type: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(BigInteger)
    range_id: Mapped[UUID] = mapped_column()
    state: Mapped[str] = mapped_column(Text)
    route: Mapped[str] = mapped_column(Text)
    delivery_state: Mapped[str] = mapped_column(Text)
    issued_on: Mapped[date] = mapped_column(Date)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    security_code: Mapped[str | None] = mapped_column(Text)
    qr_url: Mapped[str | None] = mapped_column(Text)
    emission_mode: Mapped[str | None] = mapped_column(Text)
    ecf_key: Mapped[str | None] = mapped_column(Text)
    ecf_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    rfce_key: Mapped[str | None] = mapped_column(Text)
    rfce_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    number_fate: Mapped[str | None] = mapped_column(Text)
    """What became of its number once DGII decided (burned, released); ``None`` while it holds it."""
    draft: Mapped[dict[str, object]] = mapped_column(JSONB)
    """The ``FiscalDraft`` it was numbered for, in the platform codec's JSON: what every signature signs."""
    version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submission_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """The submission's mark, committed before the one call that sends it: never sent again once set."""
    track_id: Mapped[str | None] = mapped_column(Text)
    track_id_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When its outcome first became unknown: the track-id window runs from it, never reset."""
    polls: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    """How often DGII was asked about it, or could not be reached: the next wait grows with it."""
    outcome_code: Mapped[int | None] = mapped_column(Integer)
    """DGII's last code about it, as the service that answered reads it."""
    sequence_used: Mapped[bool | None] = mapped_column(Boolean)
    """DGII's ``secuenciaUtilizada``, once it said."""
    messages: Mapped[list[dict[str, str]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    """DGII's messages about it (``code``, ``text``), verbatim within the kit's bounds."""
    delivery_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When its delivery to the buyer started (its first run, or the client's retry)."""
    delivery_attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    """How many attempts at delivering it failed since then."""
    delivery_reason: Mapped[str | None] = mapped_column(Text)
    """Why its delivery failed, failed last, or does not apply: a code."""
    receipt_key: Mapped[str | None] = mapped_column(Text)
    """Where the archive keeps the buyer's receipt (ARECF), trusted or refused."""
    receipt_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    commercial_approval: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    """Its buyer's last commercial approval or rejection (``approved``, ``reason``, ``at``), once one came."""


class SubmissionRecord(DoEcfTable):
    """A track id a document was given or adopted, under its number: never adopted by another document.

    Nothing changes or deletes a row: a released number's next document must know its predecessor's.
    """

    __tablename__: str = "submissions"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            [*TENANT, "document_id"],
            [f"{SCHEMA}.documents.{column}" for column in (*TENANT, "id")],
            name="fk_submissions_document",
        ),
        *_series_words("submissions"),
        CheckConstraint("sequence BETWEEN 1 AND 9999999999", name="ck_submissions_sequence"),
        CheckConstraint("length(track_id) > 0", name="ck_submissions_track_id"),
        Index("submissions_number", *SERIES, "sequence"),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(primary_key=True)
    track_id: Mapped[str] = mapped_column(Text, primary_key=True)
    environment: Mapped[str] = mapped_column(Text)
    ecf_type: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(BigInteger)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    """When it was recorded (the unit's time)."""
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When DGII says it received the document, where DGII listed it (``consultatrackids``)."""


RECEIVED_DIGESTS_CHECK: Final = "octet_length(xml_sha256) = 32 AND octet_length(arecf_sha256) = 32"
APPROVAL_CHECK: Final = (
    "(approval_decision IS NULL) = (approval_requested_at IS NULL) "
    "AND (approval_decision IS NULL) = (approval_emitter IS NULL) "
    "AND (approval_decision IS NULL) = (approval_dgii IS NULL)"
)
"""A decision has its moment and both parties' states, or the document has none of them."""
APPROVAL_REASON_CHECK: Final = (
    "(approval_decision IS NOT DISTINCT FROM 'rejected') = (approval_reason IS NOT NULL)"
)
"""A rejection says why; an approval does not."""
APPROVAL_ACECF_CHECK: Final = (
    "(approval_key IS NULL) = (approval_sha256 IS NULL) "
    "AND (approval_key IS NULL) = (approval_at IS NULL) "
    "AND (approval_sha256 IS NULL OR octet_length(approval_sha256) = 32)"
)
"""The ACECF signed is kept by its moment, key and SHA-256: all or none."""
APPROVAL_SENT_CHECK: Final = (
    "(approval_emitter IS DISTINCT FROM 'sent' OR approval_sent_to_emitter_at IS NOT NULL) "
    "AND (approval_dgii IS DISTINCT FROM 'sent' OR approval_sent_to_dgii_at IS NOT NULL) "
    "AND (approval_key IS NOT NULL OR (approval_dgii_started_at IS NULL "
    "AND approval_sent_to_emitter_at IS NULL AND approval_sent_to_dgii_at IS NULL))"
)
"""A party took it at a moment; nothing is marked or sent before the ACECF is signed."""


class ReceivedDocumentRecord(DoEcfTable):
    """An e-CF another emitter sent the company, received: what reception read of it, where it is kept.

    One per environment, issuer and e-NCF (Decision 16): the same e-NCF again is a duplicate. Nothing deletes
    it; what changes is the company's commercial approval of it (Task 23): its decision, the ACECF signed for
    it, and where sending that to the emitter and to DGII stands.
    """

    __tablename__: str = "received_documents"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            *TENANT, "environment", "issuer_rnc", "ecf_type", "sequence", name="received_documents_number_key"
        ),
        *_series_words("received_documents"),
        CheckConstraint("sequence BETWEEN 1 AND 9999999999", name="ck_received_documents_sequence"),
        CheckConstraint(RECEIVED_DIGESTS_CHECK, name="ck_received_documents_digests"),
        CheckConstraint("version > 0", name="ck_received_documents_version"),
        _one_of("approval_decision", Decision, name="ck_received_documents_approval_decision"),
        _one_of("approval_emitter", Sending, name="ck_received_documents_approval_emitter"),
        _one_of("approval_dgii", Sending, name="ck_received_documents_approval_dgii"),
        CheckConstraint(APPROVAL_CHECK, name="ck_received_documents_approval"),
        CheckConstraint(APPROVAL_REASON_CHECK, name="ck_received_documents_approval_reason"),
        CheckConstraint(APPROVAL_ACECF_CHECK, name="ck_received_documents_approval_acecf"),
        CheckConstraint(APPROVAL_SENT_CHECK, name="ck_received_documents_approval_sent"),
        CheckConstraint("approval_attempts >= 0", name="ck_received_documents_approval_attempts"),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(Text)
    """The environment DGII's registration names: it implies the mode (Decision 15)."""
    issuer_rnc: Mapped[str] = mapped_column(Text)
    issuer_name: Mapped[str] = mapped_column(Text)
    ecf_type: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(BigInteger)
    buyer_rnc: Mapped[str | None] = mapped_column(Text)
    """``None`` when the document names no buyer (a 43)."""
    issued_on: Mapped[date] = mapped_column(Date)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    itbis: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    security_code: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    xml_key: Mapped[str] = mapped_column(Text)
    """Where the archive keeps the document as it arrived (``received``)."""
    xml_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    arecf_key: Mapped[str] = mapped_column(Text)
    """Where the archive keeps the signed receipt it was answered with (``arecf``)."""
    arecf_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    version: Mapped[int] = mapped_column(BigInteger)
    approval_decision: Mapped[str | None] = mapped_column(Text)
    """The company's commercial decision (``approved``, ``rejected``); ``None`` before it decides."""
    approval_reason: Mapped[str | None] = mapped_column(Text)
    """Why it rejected it."""
    approval_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When it decided: the emitter and DGII are tried for seven days from then (Decision 21)."""
    approval_attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    """How many runs of ``do_ecf.approve`` left a party to try again."""
    approval_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """The ACECF's ``FechaHoraAprobacionComercial``: the moment it was signed."""
    approval_key: Mapped[str | None] = mapped_column(Text)
    """Where the archive keeps the ACECF signed (``acecf``)."""
    approval_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    approval_emitter: Mapped[str | None] = mapped_column(Text)
    """Where sending it to the emitter stands (``Sending``)."""
    approval_emitter_reason: Mapped[str | None] = mapped_column(Text)
    """Why it is not sent, or not sent yet: a code."""
    approval_sent_to_emitter_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_dgii: Mapped[str | None] = mapped_column(Text)
    """Where sending it to DGII stands (``Sending``)."""
    approval_dgii_reason: Mapped[str | None] = mapped_column(Text)
    """Why it is not sent, or not sent yet: a code."""
    approval_dgii_messages: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    """DGII's messages about it (``code``, ``text``), verbatim within the kit's bounds."""
    approval_dgii_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """The submission's mark, committed before the one call that sends it to DGII: never sent again."""
    approval_sent_to_dgii_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReceiverSeedRecord(DoEcfTable):
    """A seed the receiver gave out, by its SHA-256: whose (company, environment), until when, taken when."""

    __tablename__: str = "receiver_seeds"
    __table_args__: tuple[SchemaItem, ...] = (
        _one_of("environment", Environment, name="ck_receiver_seeds_environment"),
        CheckConstraint("octet_length(seed_hash) = 32", name="ck_receiver_seeds_hash"),
        Index("receiver_seeds_expiry", "expires_at"),
    )

    seed_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    company_id: Mapped[UUID] = mapped_column()
    environment: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When it was exchanged for a token: it serves once."""


class ReceiverTokenRecord(DoEcfTable):
    """A token the receiver issued, by its SHA-256: whose company and environment, which sender, until when.

    ``senders``: the RNCs and cédulas the sender's certificate names; ``stand_in``: a DGII stand-in's
    (Decision 17), which sends for any issuer in a test environment.
    """

    __tablename__: str = "receiver_tokens"
    __table_args__: tuple[SchemaItem, ...] = (
        _one_of("mode", Mode, name="ck_receiver_tokens_mode"),
        _one_of("environment", Environment, name="ck_receiver_tokens_environment"),
        CheckConstraint("(environment = 'ecf') = (mode = 'live')", name="ck_receiver_tokens_live"),
        CheckConstraint("octet_length(token_hash) = 32", name="ck_receiver_tokens_hash"),
        CheckConstraint("NOT stand_in OR environment <> 'ecf'", name="ck_receiver_tokens_stand_in"),
        Index("receiver_tokens_expiry", "expires_at"),
    )

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    company_id: Mapped[UUID] = mapped_column()
    mode: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(Text)
    senders: Mapped[list[str]] = mapped_column(ARRAY(Text))
    stand_in: Mapped[bool] = mapped_column(Boolean)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CompanyOptionsRecord(DoEcfTable):
    """How a company delivers to its buyers in a mode; no row is the defaults (authentication off)."""

    __tablename__: str = "company_options"
    __table_args__: tuple[SchemaItem, ...] = (
        _one_of("mode", Mode, name="ck_company_options_mode"),
        CheckConstraint("version > 0", name="ck_company_options_version"),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    buyer_authentication: Mapped[bool] = mapped_column(Boolean)
    """Whether the company signs a buyer's seed for a token (Decision 19; F14)."""
    version: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
