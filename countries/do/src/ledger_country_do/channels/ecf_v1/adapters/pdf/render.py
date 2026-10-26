"""A signed e-CF as printed (spec §8.6.7): ``render_pdf`` reads the e-CF itself, ``layout`` draws it.

What is printed comes from the signed e-CF (the archive's bytes, read back against their SHA-256 by the
caller), never from the invoice: the e-CF type's name (regime ``do``'s), its e-NCF, issue date and the last
day of its range (dated types); the issuer's name, RNC and address; the buyer's (on a purchase, the
supplier's) name, RNC or cédula or foreign identifier, and address, when the e-CF names any; each line's
number, name, quantity, unit price, discount, ITBIS (its rate, or ``Exento``) and amount; the totals by rate
(taxed amount and ITBIS), the exempt amount, the ITBIS, the total and what is withheld or perceived; in
another currency, that currency, its rate and the total in it; the e-CF a note modifies; and
``FechaHoraFirma``. What the document recorded when it was signed completes it (``Signature``): its security
code, its QR's URL, whether it was signed in contingency (in any mode but the normal one, which is every
document's until contingency is built: Decision 36, Task 28), and the moment of signing (the PDF's date).

Values are printed as the e-CF writes them (amounts with two decimals, dates ``dd-MM-yyyy``). The e-CF is the
channel's own, schema-valid when it was signed: it is read with fiscal-kit's hardened parser, elements named
in lowercase as its schema declares them (``names``), and one that cannot be read, or lacks what its schema
requires, is the archive's defect (``SignedEcfUnreadableError``).

``PdfPrinting`` renders off the event loop (``asyncio.to_thread``): a thousand lines take a while.
"""

import asyncio
from abc import ABCMeta, abstractmethod
from types import MappingProxyType
from typing import ClassVar, Final, Protocol, Self, final, override
lazy from collections.abc import Mapping, Sequence

from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.pdf.layout import TOTAL, Party, Printed, PrintedLine, draw
from ledger_country_do.channels.ecf_v1.adapters.xml.ecf import FORMAT_OF
from ledger_country_do.channels.ecf_v1.adapters.xml.names import declared
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.application.representation import Printing
from ledger_country_do.channels.ecf_v1.domain.drafts import BillingIndicator, ModificationCode
from ledger_country_do.channels.ecf_v1.domain.ecf import EmissionMode
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.regime import REGIME
from ledger_fiscal_kit.xml.hardened import parser
from ledger_kernel.regimes import Direction, Locale
lazy from ledger_country_do.channels.ecf_v1.application.documents import Signature

_SPANISH: Final = Locale("es")
_TYPES: Final = MappingProxyType({each.code: each for each in REGIME.document_types})
_RATES: Final = ("1", "2", "3")
"""The ITBIS rates' tags in DGII's element names (``MontoGravadoI1``, ``ITBIS1``, ``TotalITBIS1``)."""
_UNTAXED: Final[Mapping[str, str]] = MappingProxyType(
    {str(BillingIndicator.EXEMPT.value): "Exento", str(BillingIndicator.NOT_BILLABLE.value): "No facturable"}
)
_MODIFICATIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        str(ModificationCode.VOID.value): "anula el e-CF modificado",
        str(ModificationCode.CORRECT_TEXT.value): "corrige su texto",
        str(ModificationCode.CORRECT_AMOUNTS.value): "corrige sus montos",
        str(ModificationCode.REPLACE_CONTINGENCY.value): "reemplaza un NCF emitido en contingencia",
        str(ModificationCode.REFERENCE_CONSUMER.value): "se refiere a una factura de consumo",
    }
)
_WITHHELD: Final = (
    ("ITBIS retenido", "totalitbisretenido"),
    ("ISR retenido", "totalisrretencion"),
    ("ITBIS percibido", "totalitbispercepcion"),
    ("ISR percibido", "totalisrpercepcion"),
)


class SignedEcfUnreadableError(EcfError):
    """The archived e-CF cannot be read, or lacks what its schema requires: a defect, never retried."""

    code: ClassVar[str] = "do_ecf_signed_ecf_unreadable"

    def __init__(self) -> None:
        super().__init__("the archived e-CF cannot be read for its printed representation")


class _Node(Protocol, metaclass=ABCMeta):
    """What is read of an element of lxml's: its descendants' text, and its descendants."""

    @abstractmethod
    def findtext(self, path: str, /) -> str | None:
        """The text of the first element at ``path``, or ``None``."""

    @abstractmethod
    def findall(self, path: str, /) -> Sequence[Self]:
        """Every element at ``path``, in order."""


@final
class _Element:
    """An element of the e-CF, its descendants named in lowercase as the e-CF's schema declares them."""

    __slots__ = ("_element", "_names")

    def __init__(self, element: _Node, names: Mapping[str, str]) -> None:
        self._element = element
        self._names = names

    def _path(self, path: tuple[str, ...]) -> str | None:
        """``path`` as an ElementPath, or ``None`` when the schema declares no such element."""
        if any(name not in self._names for name in path):
            return None
        return "/".join(self._names[name] for name in path)

    def optional(self, *path: str) -> str | None:
        """The text at ``path``, or ``None`` when it is absent."""
        found = self._path(path)
        return None if found is None else self._element.findtext(found)

    def required(self, *path: str) -> str:
        """The text at ``path``, which the schema requires (``SignedEcfUnreadableError`` without one)."""
        found = self.optional(*path)
        if not found:
            raise SignedEcfUnreadableError
        return found

    def each(self, *path: str) -> tuple[_Element, ...]:
        """Every element at ``path``, in order."""
        found = self._path(path)
        elements = () if found is None else self._element.findall(found)
        return tuple(_Element(element, self._names) for element in elements)


def _read(signed_ecf: bytes) -> tuple[_Element, EcfType]:
    """The e-CF's root, and its type (see the module)."""
    try:
        root = etree.fromstring(signed_ecf, parser())
    except etree.XMLSyntaxError:
        raise SignedEcfUnreadableError from None
    tag = _Element(root, declared(Format.ECF_31)).optional("encabezado", "iddoc", "tipoecf")
    try:
        ecf_type = EcfType(tag or "")
    except ValueError:  # not one of the ten types: the message never repeats it
        raise SignedEcfUnreadableError from None
    return _Element(root, declared(FORMAT_OF[ecf_type])), ecf_type


def _counterparty(header: _Element) -> Party | None:
    """The buyer (or supplier) the e-CF names, or ``None`` when it names none (an anonymous ``Comprador``)."""
    rnc = header.optional("comprador", "rnccomprador")
    foreign = header.optional("comprador", "identificadorextranjero")
    identity = (
        f"RNC/Cédula: {rnc}"
        if rnc is not None
        else (None if foreign is None else f"Identificador extranjero: {foreign}")
    )
    party = Party(
        name=header.optional("comprador", "razonsocialcomprador"),
        identity=identity,
        address=header.optional("comprador", "direccioncomprador"),
    )
    return None if party == Party(name=None, identity=None, address=None) else party


def _totals(header: _Element) -> tuple[tuple[str, str], ...]:
    """The totals the e-CF states, in pesos, each with its label (see the module)."""
    rated = {tag: f"ITBIS {header.optional('totales', f'itbis{tag}')} %" for tag in _RATES}
    stated = (
        *(
            (f"Monto gravado {rated[tag]}", header.optional("totales", f"montogravadoi{tag}"))
            for tag in _RATES
        ),
        ("Monto exento", header.optional("totales", "montoexento")),
        *((rated[tag], header.optional("totales", f"totalitbis{tag}")) for tag in _RATES),
        ("Total ITBIS", header.optional("totales", "totalitbis")),
        (TOTAL, header.required("totales", "montototal")),
        *((label, header.optional("totales", name)) for label, name in _WITHHELD),
    )
    return tuple((label, amount) for label, amount in stated if amount is not None)


def _currency(header: _Element) -> tuple[str, ...]:
    """In another currency: the currency, the exchange rate and the total in that currency; else nothing."""
    currency = header.optional("otramoneda", "tipomoneda")
    if currency is None:
        return ()
    return (
        f"Moneda: {currency}",
        f"Tasa de cambio: {header.required('otramoneda', 'tipocambio')}",
        f"Monto total en {currency}: {header.required('otramoneda', 'montototalotramoneda')}",
    )


def _line(item: _Element, header: _Element) -> PrintedLine:
    billing = item.required("indicadorfacturacion")
    rate = header.optional("totales", f"itbis{billing}") if billing in _RATES else None
    return PrintedLine(
        number=item.required("numerolinea"),
        description=item.required("nombreitem"),
        quantity=item.required("cantidaditem"),
        unit_price=item.required("preciounitarioitem"),
        discount=item.optional("descuentomonto"),
        tax=_UNTAXED.get(billing, f"{rate} %"),
        amount=item.required("montoitem"),
    )


def _reference(reference: _Element) -> str:
    code = reference.required("codigomodificacion")
    return (
        f"Modifica el e-NCF {reference.required('ncfmodificado')} del "
        f"{reference.required('fechancfmodificado')}: {_MODIFICATIONS.get(code, 'otra modificación')} "
        f"(código {code})."
    )


def printed_of(signed_ecf: bytes, signature: Signature) -> Printed:
    """What the printed representation of ``signed_ecf`` shows (see the module)."""
    root, ecf_type = _read(signed_ecf)
    header = root.each("encabezado")[0]
    expires = header.optional("iddoc", "fechavencimientosecuencia")
    document_type = _TYPES[ecf_type.value]
    return Printed(
        title=document_type.labels[_SPANISH],
        encf=header.required("iddoc", "encf"),
        dates=(
            f"Fecha de emisión: {header.required('emisor', 'fechaemision')}",
            *(() if expires is None else (f"Válida hasta: {expires}",)),
        ),
        issuer=Party(
            name=header.required("emisor", "razonsocialemisor"),
            identity=f"RNC: {header.required('emisor', 'rncemisor')}",
            address=header.optional("emisor", "direccionemisor"),
        ),
        counterparty=_counterparty(header),
        counterparty_title="Proveedor" if document_type.direction is Direction.PURCHASE else "Comprador",
        references=tuple(_reference(each) for each in root.each("informacionreferencia")),
        lines=tuple(_line(item, header) for item in root.each("detallesitems", "item")),
        totals=_totals(header),
        currency=_currency(header),
        security_code=signature.security_code,
        signed=root.required("fechahorafirma"),
        qr_url=signature.qr_url,
        contingency=signature.emission_mode is not EmissionMode.NORMAL,
        created=signature.signed_at,
    )


def render_pdf(signed_ecf: bytes, signature: Signature) -> bytes:
    """``signed_ecf``'s printed representation, as a PDF: the same input, the same bytes (Decision 26)."""
    return draw(printed_of(signed_ecf, signature))


@final
class PdfPrinting(Printing):
    """``Printing`` as ``render_pdf``, off the event loop."""

    __slots__ = ()

    @override
    async def pdf(self, signed_ecf: bytes, signature: Signature) -> bytes:
        return await asyncio.to_thread(render_pdf, signed_ecf, signature)
