"""The names of DGII's elements, as each schema declares them, looked up by their lowercase spelling.

Code that writes a DGII document names each element in lowercase (``rncemisor``) and gets the name its schema
declares (``RNCEmisor``): every name written is one DGII's schema holds, so a misspelled one fails where it is
written (``UndeclaredElementError``), not later as a schema violation, and DGII's Spanish names live in DGII's
files only. Two names of one schema that differ only in case (``SubTotalImpuestoAdicional`` and
``SubtotalImpuestoAdicional``, additional taxes the spike does not write) cannot be looked up this way and are
left out.
"""

from collections import Counter
from functools import cache
from types import MappingProxyType
from typing import ClassVar, Final
lazy from collections.abc import Mapping

from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_fiscal_kit.xml.hardened import parser
lazy from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format

_ELEMENT: Final = "{http://www.w3.org/2001/XMLSchema}element"


class UndeclaredElementError(EcfError):
    """Code named an element its schema does not declare: a defect of the writer."""

    code: ClassVar[str] = "do_ecf_element_undeclared"

    def __init__(self, format_: Format, name: str) -> None:
        super().__init__(f"DGII's schema {format_.value} declares no element {name}")


@cache
def declared(format_: Format) -> Mapping[str, str]:
    """The elements ``format_``'s schema declares, by their lowercase names (see the module)."""
    root = etree.fromstring(DO_SCHEMAS.corrected(format_), parser())
    names = {name for found in root.iter(_ELEMENT) if (name := found.get("name")) is not None}
    spellings = Counter(name.lower() for name in names)
    return MappingProxyType({name.lower(): name for name in names if spellings[name.lower()] == 1})


def element(format_: Format, name: str) -> str:
    """The element ``format_`` declares as ``name`` in lowercase; ``UndeclaredElementError`` if none."""
    found = declared(format_).get(name)
    if found is None:
        raise UndeclaredElementError(format_, name)
    return found
