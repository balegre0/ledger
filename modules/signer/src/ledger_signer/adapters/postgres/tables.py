"""signer's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

A certificate belongs to a company and is shared by its modes (it says which it serves): both tables are under
the company's row-level security (``organization_id``, ``company_id``), and the custody row points at its
certificate through a key that carries the company, so it can never point at another's. ``certificates`` holds
the public part (what describing reads); ``custody`` the sealed material, which describing never reads and a
retirement deletes. One active certificate serves a company in each mode (two partial unique indexes). Every
table is in schema ``signer`` (§6.2), which only the signer's login reaches (ADR 0010).
"""

from datetime import datetime
from typing import Final
from uuid import UUID
lazy from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from ledger_platform.data import ModuleTable
from ledger_signer.domain.certificates import (
    FINGERPRINT_BYTES,
    BackendKind,
    CertificateStatus,
    RetiredReason,
)

SCHEMA: Final = "signer"
"""signer's schema: its tables and the chassis's (``chassis_schema_v1`` and ``_v2``), under its roles."""


def _one_of(column: str, words: type[StrEnum], *, name: str) -> CheckConstraint:
    """``column`` holds one of ``words``' values: the domain's list, which the migration spells out."""
    allowed = ", ".join(f"'{word}'" for word in words)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


def _active_in(mode: str) -> Index:
    """One active certificate serves a company in ``mode``."""
    return Index(
        f"certificates_one_active_{mode}",
        "company_id",
        unique=True,
        postgresql_where=text(f"status = 'active' AND serves_{mode}"),
    )


class SignerTable(ModuleTable, schema=SCHEMA):
    """signer's own tables, in its schema; the chassis's are ``chassis_tables("signer")``."""


class CertificateRecord(SignerTable):
    """A certificate of a company: its public part, the modes it serves and whether it may still sign."""

    __tablename__: str = "certificates"
    __table_args__: tuple[SchemaItem, ...] = (
        CheckConstraint("serves_test OR serves_live OR status = 'retired'", name="ck_certificates_modes"),
        _one_of("backend", BackendKind, name="ck_certificates_backend"),
        _one_of("status", CertificateStatus, name="ck_certificates_status"),
        CheckConstraint(
            f"octet_length(fingerprint_sha256) = {FINGERPRINT_BYTES}", name="ck_certificates_fingerprint"
        ),
        CheckConstraint("not_after > not_before", name="ck_certificates_validity"),
        _one_of("retired_reason", RetiredReason, name="ck_certificates_retired_reason"),
        CheckConstraint(
            (
                "(status = 'active') = (retired_at IS NULL AND retired_reason IS NULL)"
                " AND (retired_at IS NULL) = (retired_reason IS NULL)"
            ),
            name="ck_certificates_retirement",
        ),
        _active_in("test"),
        _active_in("live"),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    serves_test: Mapped[bool] = mapped_column()
    serves_live: Mapped[bool] = mapped_column()
    backend: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    certificate_der: Mapped[bytes] = mapped_column(LargeBinary)
    fingerprint_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    serial_number: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str] = mapped_column(Text)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(BigInteger)


class CustodyRecord(SignerTable):
    """A certificate's sealed material: its PKCS#8 key (local) or its entity credential (remote).

    ``sealed`` is AES-256-GCM (nonce, ciphertext, tag) under a data key of its own, which the KMS wrapped
    (``wrapped_data_key``) under its master key ``kms_key_id``.
    """

    __tablename__: str = "custody"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            ["organization_id", "company_id", "certificate_id"],
            [
                f"{SCHEMA}.certificates.organization_id",
                f"{SCHEMA}.certificates.company_id",
                f"{SCHEMA}.certificates.id",
            ],
            name="fk_custody_certificate",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    certificate_id: Mapped[UUID] = mapped_column(primary_key=True)
    sealed: Mapped[bytes] = mapped_column(LargeBinary)
    wrapped_data_key: Mapped[bytes] = mapped_column(LargeBinary)
    kms_key_id: Mapped[str] = mapped_column(Text)
