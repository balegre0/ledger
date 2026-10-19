"""DGII's authentication seed (*SemillaModel*): what the channel checks before it has a seed signed.

DGII's services and a buyer's receiver authenticate an emitter by a seed it signs, a ``SemillaModel`` holding
``valor`` then ``fecha``. The signature is fiscal-kit's profile (``ledger_fiscal_kit.xml.dsig``), made by
whoever holds the key; the seed's shape is DGII's, so the channel checks it, read hardened, before it hands
the seed over to be signed (``require_seed``).
"""

from typing import ClassVar, Final

from lxml import etree

from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
from ledger_fiscal_kit.xml.hardened import MalformedDocumentError, parser, require_plain, require_readable

SEED_ROOT: Final = "SemillaModel"
SEED_CHILDREN: Final = ("valor", "fecha")


class SeedMalformedError(EcfInvalidError):
    """What was to be signed as a seed is not DGII's ``SemillaModel``."""

    code: ClassVar[str] = "do_ecf_seed_malformed"

    def __init__(self) -> None:
        super().__init__("a seed is a SemillaModel holding valor then fecha")


def require_seed(seed: bytes) -> None:
    """Refuse ``seed`` unless it reads hardened as DGII's ``SemillaModel`` (``valor``, then ``fecha``).

    ``XmlDocumentError`` (fiscal-kit's) when it cannot be read; ``SeedMalformedError`` when it is not a seed.
    """
    require_readable(seed)
    try:
        root = etree.fromstring(seed, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    require_plain(root.getroottree().docinfo)
    children = [child.tag for child in root if isinstance(child.tag, str)]
    if root.tag != SEED_ROOT or children != list(SEED_CHILDREN):
        raise SeedMalformedError
