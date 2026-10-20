"""Reading what counterparties send: an e-CF, an approval of one of ours, a receipt (spec §3.8, §10; DT-EMI).

Nothing that arrives is trusted. A document is refused unread when it is over ``MAX_INBOUND`` bytes (the
receiver's own body limit), not UTF-8, or declares a DOCTYPE; it is parsed once, by fiscal-kit's hardened
parser (no DTD, no entity, no network, libxml2's limits on depth and amplification, comments dropped); it
holds only elements and text inside its root (``hardened.require_tree``: a processing instruction would cut a
value short); its root must be the format's, without a namespace; and the whole tree must be valid for its
schema (an e-CF's, chosen by its ``TipoeCF`` written as DGII's two-digit code: ``031`` or ``+31``, which
``xs:integer`` reads as 31, are refused; an ACECF's; an ARECF's).

What the schema pins down is read as the schema reads it: integers as integers (``Estado`` ``01`` or ``+1``
is 1), tax ids of nine or eleven ASCII digits, amounts of at most two decimals. What it leaves loose is
checked again: the e-NCF must be one, of the type the document says; dates and moments real days of
``DGII_YEARS`` in ASCII digits; an e-CF's last element its ``Signature``, holding a base64 value; an approval
or a receipt of an e-CF received gives no reason. The values read are the domain's, which check their own
invariants (an approval's total is at most an e-CF's: 16 integer digits). Reading verifies no signature:
that is the caller's next step (the receiver, Task 22; ``BuyerClient``), with the ``INBOUND`` profile and the
trust policy.

Every refusal is ``InboundDocumentInvalidError`` (``do_ecf_inbound_invalid``) with a message in this module's
words, never quoting the document (not even an element's name, which a schema violation's path would carry);
libxml2's own errors, which quote it, are neither chained nor kept as context. Nothing here logs.

``name_inbound`` reads, leniently, what a receipt of a document that cannot be read names (Task 22's
reason 1): its issuer, buyer and e-NCF.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Mapping, Sequence
lazy from datetime import date, datetime

from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.xml.ecf import FORMAT_OF
from ledger_country_do.channels.ecf_v1.adapters.xml.names import declared
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import PUBLISHED, Format
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf, InvalidEncfError
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
from ledger_country_do.channels.ecf_v1.domain.exchanges import (
    CommercialApproval,
    InboundEcf,
    NotReceivedReason,
    Receipt,
    is_tax_id,
)
from ledger_country_do.channels.ecf_v1.domain.notation import parse_dgii_date, parse_dgii_moment
from ledger_country_do.channels.ecf_v1.domain.printed import SECURITY_CODE_LENGTH, security_code
from ledger_fiscal_kit.xml.hardened import (
    DSIG,
    SIGNATURE,
    XmlDocumentError,
    parser,
    require_plain,
    require_readable,
    require_tree,
)

MAX_INBOUND: Final = 2 * 1024 * 1024
"""The largest document read: the receiver's body limit (Task 22). An e-CF of 1,000 lines is far smaller."""
_ECF_TYPES: Final = frozenset(each.value for each in EcfType)
_SIGNATURE_VALUE: Final = f"{{{DSIG}}}SignatureValue"
_BASE64: Final = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_APPROVED: Final = 1
_RECEIVED: Final = 0

type _Path = tuple[str, ...]
"""An element's path from the root, each step the lowercase name its schema declares (``names``)."""

_TYPE: Final[_Path] = ("encabezado", "iddoc", "tipoecf")
_ENCF: Final[_Path] = ("encabezado", "iddoc", "encf")
_ISSUER: Final[_Path] = ("encabezado", "emisor", "rncemisor")
_ISSUER_NAME: Final[_Path] = ("encabezado", "emisor", "razonsocialemisor")
_ISSUED_ON: Final[_Path] = ("encabezado", "emisor", "fechaemision")
_BUYER: Final[_Path] = ("encabezado", "comprador", "rnccomprador")
_TOTAL: Final[_Path] = ("encabezado", "totales", "montototal")
_ITBIS: Final[_Path] = ("encabezado", "totales", "totalitbis")
_SIGNED_AT: Final[_Path] = ("fechahorafirma",)
_ECF_PATHS: Final = (_TYPE, _ENCF, _ISSUER, _ISSUER_NAME, _ISSUED_ON, _BUYER, _TOTAL, _ITBIS, _SIGNED_AT)
_APPROVAL: Final = "detalleaprobacioncomercial"
_A_ISSUER: Final[_Path] = (_APPROVAL, "rncemisor")
_A_ENCF: Final[_Path] = (_APPROVAL, "encf")
_A_ISSUED_ON: Final[_Path] = (_APPROVAL, "fechaemision")
_A_TOTAL: Final[_Path] = (_APPROVAL, "montototal")
_A_BUYER: Final[_Path] = (_APPROVAL, "rnccomprador")
_A_STATE: Final[_Path] = (_APPROVAL, "estado")
_A_REASON: Final[_Path] = (_APPROVAL, "detallemotivorechazo")
_A_AT: Final[_Path] = (_APPROVAL, "fechahoraaprobacioncomercial")
_ACECF_PATHS: Final = (_A_ISSUER, _A_ENCF, _A_ISSUED_ON, _A_TOTAL, _A_BUYER, _A_STATE, _A_REASON, _A_AT)
_RECEIPT: Final = "detalleacusederecibo"
_R_ISSUER: Final[_Path] = (_RECEIPT, "rncemisor")
_R_BUYER: Final[_Path] = (_RECEIPT, "rnccomprador")
_R_ENCF: Final[_Path] = (_RECEIPT, "encf")
_R_STATE: Final[_Path] = (_RECEIPT, "estado")
_R_REASON: Final[_Path] = (_RECEIPT, "codigomotivonorecibido")
_R_AT: Final[_Path] = (_RECEIPT, "fechahoraacuserecibo")
_ARECF_PATHS: Final = (_R_ISSUER, _R_BUYER, _R_ENCF, _R_STATE, _R_REASON, _R_AT)

_MALFORMED: Final = "the document is not well-formed XML"
_NOT_ECF: Final = "the document is not an e-CF"
_NOT_ACECF: Final = "the document is not a commercial approval"
_NOT_ARECF: Final = "the document is not a receipt"
_SCHEMA: Final = "the document is not valid for its schema"
_ENCF_INVALID: Final = "the e-NCF is not one"
_ENCF_OTHER_TYPE: Final = "the e-NCF is of another type than the document says"
_DATE_INVALID: Final = "a date is not a real one of this century in ASCII digits"
_MOMENT_INVALID: Final = "a moment is not a real one of this century in ASCII digits"
_UNSIGNED: Final = "the document does not end with a signature holding a base64 value"
_CONTRADICTORY: Final = "an approval gives a reason for a rejection"
_VALUE_INVALID: Final = "a value is not one its document may hold"


class InboundDocumentInvalidError(EcfInvalidError):
    """A received document refused as it was read (see the module); the message never quotes it."""

    code: ClassVar[str] = "do_ecf_inbound_invalid"

    def __init__(self, problem: str) -> None:
        super().__init__(f"the received document is refused: {problem}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InboundNames:
    """What a receipt of a received document names, read leniently (``name_inbound``)."""

    issuer_rnc: str
    buyer_rnc: str | None
    """``None`` when the document names no buyer (a 43): the receipt names the receiver itself."""
    encf: Encf


@final
@dataclass(frozen=True, slots=True)
class _Tree:
    """What was read of a document valid for its schema: the texts asked for, its last signature's value."""

    texts: Mapping[_Path, str | None]
    signature_value: str | None

    def optional(self, path: _Path) -> str | None:
        return self.texts[path]

    def required(self, path: _Path) -> str:
        """The text of an element its schema requires, so present (were it absent, it would read empty)."""
        return self.texts[path] or ""


def _joined(format_: Format, path: _Path) -> str:
    """``path`` as an ElementPath, in the names ``format_``'s schema declares."""
    names = declared(format_)
    return "/".join(names[name] for name in path)


def _tree(
    document: bytes,
    *,
    root_name: str,
    other_root: str,
    format_of: Callable[[str | None], Format],
    paths: Sequence[_Path],
) -> _Tree:
    """``document`` read hardened, its root ``root_name``, valid for the schema ``format_of`` names.

    ``format_of`` is given the text of an e-CF's ``TipoeCF`` (absent: ``None``), which every e-CF schema
    places alike. The texts at ``paths`` are read; a path whose elements the format does not declare reads as
    absent.
    """
    try:
        require_readable(document, limit=MAX_INBOUND)
    except XmlDocumentError as error:
        raise InboundDocumentInvalidError(str(error)) from error
    try:
        root = etree.fromstring(document, parser())
    except etree.XMLSyntaxError:
        raise InboundDocumentInvalidError(_MALFORMED) from None
    try:
        require_plain(root.getroottree().docinfo)
        require_tree(root)
    except XmlDocumentError as error:
        raise InboundDocumentInvalidError(str(error)) from error
    if root.tag != root_name:
        raise InboundDocumentInvalidError(other_root)
    format_ = format_of(root.findtext(_joined(Format.ECF_31, _TYPE)))
    if not DO_SCHEMAS.schema(format_).validate(root):
        raise InboundDocumentInvalidError(_SCHEMA)
    names = declared(format_)
    texts = {
        path: root.findtext(_joined(format_, path)) if all(name in names for name in path) else None
        for path in paths
    }
    last = root[-1]  # every schema read here requires at least one element under the root
    value = last.findtext(_SIGNATURE_VALUE) if last.tag == SIGNATURE else None
    return _Tree(MappingProxyType(texts), value)


def _tax_id(text: str) -> str:
    """A tax id as DGII's schemas read it (an ACECF's is an ``xs:integer``: whitespace around collapsed)."""
    return text.strip()


def _integer(text: str) -> int:
    """An ``xs:integer`` its schema accepted, as the schema reads it: ``01``, ``+1`` and `` 1 `` are 1."""
    return int(text.strip())


def _encf(text: str) -> Encf:
    try:
        return Encf.parse(text)
    except InvalidEncfError:
        raise InboundDocumentInvalidError(_ENCF_INVALID) from None


def _date(text: str) -> date:
    found = parse_dgii_date(text)
    if found is None:
        raise InboundDocumentInvalidError(_DATE_INVALID)
    return found


def _moment(text: str) -> datetime:
    found = parse_dgii_moment(text)
    if found is None:
        raise InboundDocumentInvalidError(_MOMENT_INVALID)
    return found


def _amount(text: str) -> Decimal:
    """An amount its schema's decimal type accepted: digits, at most two decimals, never an exponent."""
    return Decimal(text.strip())


def _built[T](build: Callable[[], T]) -> T:
    """``build()``, the domain's value; one the domain refuses is a document refused."""
    try:
        return build()
    except EcfInvalidError:
        raise InboundDocumentInvalidError(_VALUE_INVALID) from None


def _ecf_format(type_text: str | None) -> Format:
    stripped = (type_text or "").strip()
    if stripped not in _ECF_TYPES:
        raise InboundDocumentInvalidError(_SCHEMA)
    return FORMAT_OF[EcfType(stripped)]


def _security_code(value: str | None) -> str:
    """The security code of a signature value: base64 (whitespace aside) of at least its six characters."""
    compact = "".join((value or "").split())
    if _BASE64.fullmatch(compact) is None or len(compact) < SECURITY_CODE_LENGTH:
        raise InboundDocumentInvalidError(_UNSIGNED)
    return security_code(compact)


def read_inbound_ecf(document: bytes) -> InboundEcf:
    """What reception reads of ``document``, a received e-CF (see the module)."""
    tree = _tree(
        document,
        root_name=PUBLISHED[Format.ECF_31].root,
        other_root=_NOT_ECF,
        format_of=_ecf_format,
        paths=_ECF_PATHS,
    )
    ecf_type = EcfType(tree.required(_TYPE).strip())  # one of the types: ``_ecf_format`` took it
    encf = _encf(tree.required(_ENCF))
    if encf.ecf_type is not ecf_type:
        raise InboundDocumentInvalidError(_ENCF_OTHER_TYPE)
    buyer, itbis = tree.optional(_BUYER), tree.optional(_ITBIS)
    code = _security_code(tree.signature_value)
    issued_on, signed_at = _date(tree.required(_ISSUED_ON)), _moment(tree.required(_SIGNED_AT))
    return _built(
        lambda: InboundEcf(
            ecf_type=ecf_type,
            encf=encf,
            issuer_rnc=_tax_id(tree.required(_ISSUER)),
            issuer_name=tree.required(_ISSUER_NAME),
            buyer_rnc=None if buyer is None else _tax_id(buyer),
            issued_on=issued_on,
            total=_amount(tree.required(_TOTAL)),
            itbis=None if itbis is None else _amount(itbis),
            signed_at=signed_at,
            security_code=code,
        )
    )


def _one_format(format_: Format) -> Callable[[str | None], Format]:
    def chosen(type_text: str | None) -> Format:
        del type_text  # a format with no type: one schema
        return format_

    return chosen


def read_inbound_acecf(document: bytes) -> CommercialApproval:
    """What reception reads of ``document``, a buyer's approval or rejection (see the module).

    A blank reason reads as none; an approval that gives a reason is refused, as contradicting itself; a
    total beyond any e-CF's (``MontoTotal``'s type lets 17 or 18 integer digits through when the cents are
    zero) is refused by ``CommercialApproval``.
    """
    tree = _tree(
        document,
        root_name=PUBLISHED[Format.ACECF].root,
        other_root=_NOT_ACECF,
        format_of=_one_format(Format.ACECF),
        paths=_ACECF_PATHS,
    )
    given = tree.optional(_A_REASON)
    reason = given if given is not None and given.strip() else None
    approved = _integer(tree.required(_A_STATE)) == _APPROVED
    if approved and reason is not None:
        raise InboundDocumentInvalidError(_CONTRADICTORY)
    encf, issued_on = _encf(tree.required(_A_ENCF)), _date(tree.required(_A_ISSUED_ON))
    at = _moment(tree.required(_A_AT))
    return _built(
        lambda: CommercialApproval(
            issuer_rnc=_tax_id(tree.required(_A_ISSUER)),
            buyer_rnc=_tax_id(tree.required(_A_BUYER)),
            encf=encf,
            issued_on=issued_on,
            total=_amount(tree.required(_A_TOTAL)),
            approved=approved,
            reason=reason,
            at=at,
        )
    )


def read_inbound_arecf(document: bytes) -> Receipt:
    """What delivery reads of ``document``, a buyer's receipt (see the module).

    A receipt of an e-CF received that gives a reason is refused (``Receipt``), as contradicting itself; one
    of an e-CF not received may give none, as DGII's schema allows. Its e-NCF must be one (the schema's
    ``eNCFType`` also takes 9 or 11 characters, which name no e-CF).
    """
    tree = _tree(
        document,
        root_name=PUBLISHED[Format.ARECF].root,
        other_root=_NOT_ARECF,
        format_of=_one_format(Format.ARECF),
        paths=_ARECF_PATHS,
    )
    code = tree.optional(_R_REASON)
    encf, at = _encf(tree.required(_R_ENCF)), _moment(tree.required(_R_AT))
    return _built(
        lambda: Receipt(
            issuer_rnc=_tax_id(tree.required(_R_ISSUER)),
            buyer_rnc=_tax_id(tree.required(_R_BUYER)),
            encf=encf,
            received=_integer(tree.required(_R_STATE)) == _RECEIVED,
            reason=None if code is None else NotReceivedReason(_integer(code)),
            at=at,
        )
    )


def name_inbound(document: bytes) -> InboundNames | None:
    """What ``document`` names for a receipt of it when it cannot be read (issuer, buyer, e-NCF), or ``None``.

    Lenient: the document is read hardened (``MAX_INBOUND``, no DOCTYPE, elements and text only) with no
    schema, and its root must be ``ECF``; the issuer must be an RNC or cédula as written, the buyer one or
    absent, the e-NCF one (an ARECF can name nothing else this channel answers). ``None`` when any is not:
    the receiver then answers no receipt (Task 22: 400).
    """
    try:
        require_readable(document, limit=MAX_INBOUND)
        root = etree.fromstring(document, parser())
        require_plain(root.getroottree().docinfo)
        require_tree(root)
    except XmlDocumentError, etree.XMLSyntaxError:
        return None
    if root.tag != PUBLISHED[Format.ECF_31].root:
        return None
    issuer = root.findtext(_joined(Format.ECF_31, _ISSUER)) or ""
    buyer = root.findtext(_joined(Format.ECF_31, _BUYER))
    try:
        encf = Encf.parse(root.findtext(_joined(Format.ECF_31, _ENCF)) or "")
    except InvalidEncfError:
        return None
    if not is_tax_id(issuer) or (buyer is not None and not is_tax_id(buyer)):
        return None
    return InboundNames(issuer_rnc=issuer, buyer_rnc=buyer, encf=encf)
