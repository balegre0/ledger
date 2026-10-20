"""What the channel archives (§6.8; Decision 13): the kinds that name its objects' keys.

A key is fiscal-kit's ``archive_key(...)`` with ``kind`` an ``ArchiveKind``:
``do-ecf-v1/<mode>/<organization>/<company>/<kind>/<sha256 hex>.<extension>``. The kit checks only a kind's
shape; which kinds exist is the channel's.
"""

from enum import StrEnum


class ArchiveKind(StrEnum):
    """What an archived object is."""

    ECF = "ecf"
    """A signed e-CF, as submitted."""
    RFCE = "rfce"
    """A signed consumer-invoice summary (RFCE), as submitted."""
    ANECF = "anecf"
    """A signed range void (ANECF)."""
    ACECF = "acecf"
    """A signed commercial approval or rejection (ACECF), sent or received."""
    ARECF = "arecf"
    """A signed receipt (ARECF), sent or received."""
    ARECF_REFUSED = "arecf-refused"
    """A buyer's receipt that failed verification: kept as evidence, never trusted."""
    RECEIVED = "received"
    """An e-CF a supplier sent us, raw, as it arrived."""
    REPRESENTATION = "representation"
    """A printed representation (PDF), as first rendered."""
