"""Certificates in custody: what names one, how its key is held, and whether it still serves.

The adapters read certificates (no cryptography here); the domain gets their facts. A certificate is shared by
its company's modes: it serves one or both, and an active one serves at least one.
"""

from enum import StrEnum
from typing import NewType
from uuid import UUID

CertificateId = NewType("CertificateId", UUID)
"""A certificate in custody: its internal id (UUIDv7); its public form is ``crt_…`` at the HTTP edge only."""


class BackendKind(StrEnum):
    """Where a certificate's key is, and so who signs with it (spec §3.7)."""

    LOCAL = "local"
    """In our custody, sealed under a data key the KMS wraps."""
    REMOTE = "remote"
    """At an INDOTEL-authorized certification entity; we hold a credential for it."""


class CertificateStatus(StrEnum):
    """Whether a certificate may still sign."""

    ACTIVE = "active"
    RETIRED = "retired"
    """Replaced, retired by the company, or its company deactivated: it never signs again."""
