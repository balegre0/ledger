"""Reading what counterparties send: a received e-CF, an approval of one of ours (spec §3.8, §10; DT-EMI).

Nothing that arrives is trusted. A document is refused unread when it is over ``MAX_INBOUND`` bytes (the
receiver's own body limit), not UTF-8, or declares a DOCTYPE; it is parsed once, by fiscal-kit's hardened
parser (no DTD, no entity, no network, libxml2's limits on depth and amplification); its root must be the
format's, without a namespace; and the whole tree must be valid for its schema (an e-CF's, chosen by its
``TipoeCF``, with the signature it must end with; an ACECF's). What the schema pins down (tax ids of nine or
eleven ASCII digits, amounts with two decimals at most) is read as the schema reads it; what it leaves loose
is checked again: the e-NCF must be one, and of the type the document says; dates and moments real ones; an
e-CF's last element its ``Signature``, holding a value; an approval gives no reason. The values read are the
domain's, which check their own invariants again (tax ids, an aware moment). Reading verifies no signature:
that is the receiver's next step (Task 22), with the ``INBOUND`` profile and the trust policy.

Every refusal is ``InboundDocumentInvalidError`` (``do_ecf_inbound_invalid``), its cause chained: its message
says what was wrong in this module's words, never quoting the document (not even an element's name, which a
schema violation's path would carry). Nothing here logs.
"""

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
from ledger_country_do.channels.ecf_v1.domain.exchanges import CommercialApproval, InboundEcf
from ledger_country_do.channels.ecf_v1.domain.notation import parse_dgii_date, parse_dgii_moment
from ledger_country_do.channels.ecf_v1.domain.printed import SecurityCodeError, security_code
from ledger_fiscal_kit.xml.hardened import (
    DSIG,
    SIGNATURE,
    MalformedDocumentError,
    XmlDocumentError,
    parser,
    require_plain,
    require_readable,
)

MAX_INBOUND: Final = 2 * 1024 * 1024
"""The largest document read: the receiver's body limit (Task 22). An e-CF of 1,000 lines is far smaller."""
_ECF_TYPES: Final = frozenset(each.value for each in EcfType)
_SIGNATURE_VALUE: Final = f"{{{DSIG}}}SignatureValue"
_APPROVED: Final = "1"

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
_DETAIL: Final = "detalleaprobacioncomercial"
_A_ISSUER: Final[_Path] = (_DETAIL, "rncemisor")
_A_ENCF: Final[_Path] = (_DETAIL, "encf")
_A_ISSUED_ON: Final[_Path] = (_DETAIL, "fechaemision")
_A_TOTAL: Final[_Path] = (_DETAIL, "montototal")
_A_BUYER: Final[_Path] = (_DETAIL, "rnccomprador")
_A_STATE: Final[_Path] = (_DETAIL, "estado")
_A_REASON: Final[_Path] = (_DETAIL, "detallemotivorechazo")
_A_AT: Final[_Path] = (_DETAIL, "fechahoraaprobacioncomercial")
_ACECF_PATHS: Final = (_A_ISSUER, _A_ENCF, _A_ISSUED_ON, _A_TOTAL, _A_BUYER, _A_STATE, _A_REASON, _A_AT)

_NOT_ECF: Final = "the document is not an e-CF"
_NOT_ACECF: Final = "the document is not a commercial approval"
_SCHEMA: Final = "the document is not valid for its schema"
_ENCF_INVALID: Final = "the e-NCF is not one"
_ENCF_OTHER_TYPE: Final = "the e-NCF is of another type than the document says"
_DATE_INVALID: Final = "a date is not a real one"
_MOMENT_INVALID: Final = "a moment is not a real one"
_UNSIGNED: Final = "the document does not end with a signature holding a value"
_CONTRADICTORY: Final = "an approval gives a reason for a rejection"


class InboundDocumentInvalidError(EcfInvalidError):
    """A received document refused as it was read (see the module); the message never quotes it."""

    code: ClassVar[str] = "do_ecf_inbound_invalid"

    def __init__(self, problem: str) -> None:
        super().__init__(f"the received document is refused: {problem}")


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
        try:
            root = etree.fromstring(document, parser())
        except etree.XMLSyntaxError as error:
            raise MalformedDocumentError from error
        require_plain(root.getroottree().docinfo)
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
    last = root[-1]  # the schema requires at least one element under the root
    value = last.findtext(_SIGNATURE_VALUE) if last.tag == SIGNATURE else None
    return _Tree(MappingProxyType(texts), value)


def _tax_id(text: str) -> str:
    """A tax id as DGII's schemas read it (an ACECF's is an ``xs:integer``: whitespace around collapsed)."""
    return text.strip()


def _encf(text: str) -> Encf:
    try:
        return Encf.parse(text)
    except InvalidEncfError as error:
        raise InboundDocumentInvalidError(_ENCF_INVALID) from error


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


def _ecf_format(type_text: str | None) -> Format:
    stripped = (type_text or "").strip()
    if stripped not in _ECF_TYPES:
        raise InboundDocumentInvalidError(_SCHEMA)
    return FORMAT_OF[EcfType(stripped)]


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
    try:
        code = security_code(tree.signature_value or "")
    except SecurityCodeError as error:
        raise InboundDocumentInvalidError(_UNSIGNED) from error
    return InboundEcf(
        ecf_type=ecf_type,
        encf=encf,
        issuer_rnc=_tax_id(tree.required(_ISSUER)),
        issuer_name=tree.required(_ISSUER_NAME),
        buyer_rnc=None if buyer is None else _tax_id(buyer),
        issued_on=_date(tree.required(_ISSUED_ON)),
        total=_amount(tree.required(_TOTAL)),
        itbis=None if itbis is None else _amount(itbis),
        signed_at=_moment(tree.required(_SIGNED_AT)),
        security_code=code,
    )


def _acecf_format(type_text: str | None) -> Format:
    del type_text  # an ACECF has no type: one format
    return Format.ACECF


def read_inbound_acecf(document: bytes) -> CommercialApproval:
    """What reception reads of ``document``, a buyer's approval or rejection (see the module).

    A blank reason reads as none; an approval that gives a reason is refused, as contradicting itself.
    """
    tree = _tree(
        document,
        root_name=PUBLISHED[Format.ACECF].root,
        other_root=_NOT_ACECF,
        format_of=_acecf_format,
        paths=_ACECF_PATHS,
    )
    given = tree.optional(_A_REASON)
    reason = given if given is not None and given.strip() else None
    approved = tree.required(_A_STATE).strip() == _APPROVED
    if approved and reason is not None:
        raise InboundDocumentInvalidError(_CONTRADICTORY)
    # What the schema and the checks above let through, ``CommercialApproval`` accepts: its tax ids are the
    # schema's pattern, its total ``MontoTotal``'s type (two decimals, sixteen integer digits at most).
    return CommercialApproval(
        issuer_rnc=_tax_id(tree.required(_A_ISSUER)),
        buyer_rnc=_tax_id(tree.required(_A_BUYER)),
        encf=_encf(tree.required(_A_ENCF)),
        issued_on=_date(tree.required(_A_ISSUED_ON)),
        total=_amount(tree.required(_A_TOTAL)),
        approved=approved,
        reason=reason,
        at=_moment(tree.required(_A_AT)),
    )
