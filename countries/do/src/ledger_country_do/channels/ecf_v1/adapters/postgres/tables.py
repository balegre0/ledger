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
  stands and, once signed, the keys and SHA-256 of what the archive keeps (§6.8: never the bytes).
"""

from datetime import date, datetime
from typing import Final
from uuid import UUID
lazy from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

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
DIGESTS_CHECK: Final = "octet_length(ecf_sha256) = 32 AND octet_length(rfce_sha256) = 32"
HELD_NUMBER: Final = "number_fate IS DISTINCT FROM 'released'"
"""A number a document holds: any but one DGII released (another document may take it again)."""


class DocumentRecord(DoEcfTable):
    """An e-CF: the invoice it is of, its number, where it stands, what the archive keeps of it once signed.

    The number is its series' (``environment``, ``ecf_type``) ``sequence``, from the range ``range_id``.
    Nothing deletes a document: its number is accounted for for ever (§8.6.3).
    """

    __tablename__: str = "documents"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(*TENANT, "invoice_id", name="documents_invoice_key"),
        ForeignKeyConstraint(
            [*TENANT, "range_id"],
            [f"{SCHEMA}.numbering_ranges.{column}" for column in (*TENANT, "id")],
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
