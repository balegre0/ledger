"""DGII's schemas as fiscal-kit's ``SchemaSet``: the files vendored in ``adapters.xsd``, pinned and corrected.

The pins (each file's SHA-256) and the corrections are the pack's (``adapters.xsd.published``); loading,
checking the pins and validating are the kit's (``ledger_fiscal_kit.xml.schemas``).
"""

from functools import partial
from importlib.resources import files
from types import MappingProxyType
from typing import Final

from ledger_country_do.channels.ecf_v1.adapters.xsd.published import CORRECTIONS, PUBLISHED, Format
from ledger_fiscal_kit.xml.schemas import PinnedSchema, SchemaSet

_XSD_PACKAGE: Final = "ledger_country_do.channels.ecf_v1.adapters.xsd"


def published_bytes(format_: Format) -> bytes:
    """The schema of ``format_`` exactly as DGII published it."""
    return files(_XSD_PACKAGE).joinpath(format_.value).read_bytes()


DO_SCHEMAS: Final = SchemaSet[Format](
    MappingProxyType(
        {
            format_: PinnedSchema(
                format=format_,
                published=partial(published_bytes, format_),
                sha256=PUBLISHED[format_].sha256,
                corrections=CORRECTIONS.get(format_, ()),
            )
            for format_ in Format
        }
    )
)
"""Every format DGII defines, each file read and checked against its pin at its first use (none at import)."""
