"""DGII's authentication seed (*SemillaModel*): what the channel checks before it has a seed signed.

DGII's services and a buyer's receiver authenticate an emitter by a seed it signs, a ``SemillaModel`` holding
``valor`` then ``fecha``. The signature is fiscal-kit's profile (``ledger_fiscal_kit.xml.dsig``), made by
whoever holds the key; the seed's shape is DGII's, so the channel checks it, read hardened, before it hands
the seed over to be signed (``require_seed``, called by every ``XmlSigner.sign_seed``).
"""

from typing import ClassVar, Final

from lxml import etree

from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
from ledger_fiscal_kit.ports import MAX_SEED_BYTES
from ledger_fiscal_kit.xml.hardened import (
    MalformedDocumentError,
    XmlDocumentError,
    parser,
    require_plain,
    require_readable,
)

SEED_ROOT: Final = "SemillaModel"
SEED_CHILDREN: Final = ("valor", "fecha")


class SeedMalformedError(EcfInvalidError):
    """What was to be signed as a seed is not DGII's ``SemillaModel``, or does not read hardened."""

    code: ClassVar[str] = "do_ecf_seed_malformed"

    def __init__(self) -> None:
        super().__init__("a seed is a SemillaModel holding valor then fecha")


def require_seed(seed: bytes) -> None:
    """Refuse ``seed`` unless it reads hardened as DGII's ``SemillaModel`` (``valor``, then ``fecha``).

    ``SeedMalformedError`` either way, its cause fiscal-kit's ``XmlDocumentError`` when it cannot be read: a
    pack error, so a client's handling of a refused seed needs no kit error.
    """
    try:
        children = _read(seed)
    except XmlDocumentError as error:
        raise SeedMalformedError from error
    if children != (SEED_ROOT, *SEED_CHILDREN):
        raise SeedMalformedError


def _read(seed: bytes) -> tuple[object, ...]:
    """``seed``'s root tag then its child elements' tags, read hardened (``XmlDocumentError`` otherwise).

    Bounded by the port's ``MAX_SEED_BYTES``, not the reader's document limit: a seed the port would refuse is
    no seed here, so it never reaches the port as an internal error.
    """
    require_readable(seed, limit=MAX_SEED_BYTES)
    try:
        root = etree.fromstring(seed, parser())
    except etree.XMLSyntaxError:
        raise MalformedDocumentError from None
    require_plain(root.getroottree().docinfo)
    return (root.tag, *(child.tag for child in root if isinstance(child.tag, str)))
