"""Writing DGII's XML: one element after another in the schema's order, never an empty one by accident.

DT-EMI: no XML may hold an empty tag, and an unused tag is left out; so a field without a value is never
written (the one exception, an anonymous buyer's ``Comprador``, is the builder's explicit choice: F4).
Elements are named in lowercase and written as their schema declares them (``names.element``). The tree is the
standard library's ``ElementTree``, used here to build and serialize only (never to parse): its ``Element`` is
public and typed.
"""

from io import BytesIO
from typing import final
from xml.etree.ElementTree import Element, ElementTree, SubElement
lazy from decimal import Decimal

from ledger_country_do.channels.ecf_v1.adapters.xml.names import element
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_amount, dgii_number
lazy from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format


@final
class Node:
    """An element being written: its children are added in the order the schema wants them."""

    __slots__ = ("element", "format")

    def __init__(self, format_: Format, written: Element) -> None:
        self.format: Format = format_
        self.element: Element = written

    @classmethod
    def root(cls, format_: Format, name: str) -> Node:
        """A document's root element, of ``format_``'s schema."""
        return cls(format_, Element(element(format_, name)))

    def child(self, name: str) -> Node:
        """A new child element, last."""
        return Node(self.format, SubElement(self.element, element(self.format, name)))

    def text(self, name: str, value: str | None) -> None:
        """A child with ``value`` as its text; nothing when there is no value."""
        if value is not None:
            SubElement(self.element, element(self.format, name)).text = value

    def amount(self, name: str, value: Decimal | None) -> None:
        """A child with an amount (two decimals); nothing when there is none."""
        self.text(name, None if value is None else dgii_amount(value))

    def number(self, name: str, value: Decimal | int | None) -> None:
        """A child with a number written as it is; nothing when there is none."""
        if value is not None:
            self.text(name, str(value) if isinstance(value, int) else dgii_number(value))

    def document(self) -> bytes:
        """The whole document, UTF-8, with its XML declaration."""
        written = BytesIO()
        ElementTree(self.element).write(written, encoding="utf-8", xml_declaration=True)
        return written.getvalue()
