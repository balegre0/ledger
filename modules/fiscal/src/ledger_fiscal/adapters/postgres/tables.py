"""fiscal's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

``enrollments`` holds a company's enrollments in one mode (row-level security scope ``tenant``): one that is
not disabled per company, mode and channel (§7.6, a partial unique index), each with what its channel required
at its last review (``currently_due``, ``requirements_reason``), whether its channel asked for another review
since (``review_due``, Decision 34) and, once disabled, the client's reason and when (``disabled_reason``,
``disabled_at``, set exactly then). ``routings`` holds the documents routed to a channel (scope ``tenant``):
one per document and channel, for the enrollment it was routed for (a foreign key with the tenant), never
changed or deleted. Every table is in schema ``fiscal`` (§6.2).
"""

from datetime import datetime
from typing import Final
from uuid import UUID
lazy from enum import StrEnum

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from ledger_fiscal.domain.enrollments import EnrollmentStatus
from ledger_kernel.tenancy import Mode
from ledger_platform.data import ModuleTable

SCHEMA: Final = "fiscal"
"""fiscal's schema: its tables and the chassis's (``chassis_schema_v1`` and ``_v2``), under its roles."""


def _one_of(column: str, words: type[StrEnum], *, name: str) -> CheckConstraint:
    """``column`` holds one of ``words``' values: the domain's list, which the migration spells out."""
    allowed = ", ".join(f"'{word}'" for word in words)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


class FiscalTable(ModuleTable, schema=SCHEMA):
    """fiscal's own tables, in its schema; the chassis's are ``chassis_tables("fiscal")``."""


class EnrollmentRecord(FiscalTable):
    """An enrollment of a company in one mode on a channel and one of its environments."""

    __tablename__: str = "enrollments"
    __table_args__: tuple[SchemaItem, ...] = (
        _one_of("mode", Mode, name="ck_enrollments_mode"),
        _one_of("status", EnrollmentStatus, name="ck_enrollments_status"),
        CheckConstraint("version > 0", name="ck_enrollments_version"),
        CheckConstraint(
            (
                "(status = 'disabled') = (disabled_at IS NOT NULL AND disabled_reason IS NOT NULL)"
                " AND (disabled_at IS NULL) = (disabled_reason IS NULL)"
            ),
            name="ck_enrollments_disabled",
        ),
        Index(
            "enrollments_one_per_channel",
            "company_id",
            "mode",
            "channel",
            unique=True,
            postgresql_where=text("status <> 'disabled'"),
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    currently_due: Mapped[list[str]] = mapped_column(ARRAY(Text))
    requirements_reason: Mapped[str | None] = mapped_column(Text)
    """The channel's ``disabled_reason`` at the last review."""
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    """The client's, once disabled."""
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review_due: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    """Whether its channel said what it requires changed since its last review (Decision 34)."""


class RoutingRecord(FiscalTable):
    """A document routed to a channel for one of the company's enrollments: once per document and channel."""

    __tablename__: str = "routings"
    __table_args__: tuple[SchemaItem, ...] = (
        _one_of("mode", Mode, name="ck_routings_mode"),
        ForeignKeyConstraint(
            ("organization_id", "company_id", "mode", "enrollment_id"),
            (
                f"{SCHEMA}.enrollments.organization_id",
                f"{SCHEMA}.enrollments.company_id",
                f"{SCHEMA}.enrollments.mode",
                f"{SCHEMA}.enrollments.id",
            ),
            name="fk_routings_enrollment",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    enrollment_id: Mapped[UUID] = mapped_column()
    routed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
