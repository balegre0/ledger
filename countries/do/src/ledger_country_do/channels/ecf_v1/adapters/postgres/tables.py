"""do-ecf-v1's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

Every table holds a company's rows in one mode (row-level security scope ``tenant``), the tenant's columns
leading its keys (§6.3), in schema ``do_ecf`` (§6.2):

- ``numbering_series``: one row per series (an environment and an e-CF type); its lock serializes the
  series' registrations and allocations (``SELECT ... FOR UPDATE`` by way of an upsert);
- ``numbering_ranges``: DGII's authorized ranges of a series, ``next`` the number each gives next (``last +
  1``: spent), within ten digits;
- ``released_numbers``: numbers DGII rejected without using them, allocatable from ``available_at`` on
  (Decision 9).

The documents numbered from them are the next revision's, with the code that keeps them.
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
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import RangeStatus
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
