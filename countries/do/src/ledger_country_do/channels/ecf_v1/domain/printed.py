"""What a printed representation shows of a signed e-CF: its security code and its QR URL (IT §18.2.3).

The security code is the first six characters of the ``SignatureValue`` (base64, as written): IT's "los
primeros seis (6) dígitos del hash generado en el SignatureValue", as DGII's examples show (DT-DGII's
``dcp79q``, ``uabnyh``). The QR names DGII's ``consultatimbre`` with ``RncEmisor``, ``RncComprador`` (left out
without one: F8), ``ENCF``, ``FechaEmision`` (``dd-MM-yyyy``), ``MontoTotal`` (two decimals), ``FechaFirma``
(``dd-MM-yyyy HH:mm:ss``, the Dominican Republic's time) and ``CodigoSeguridad``; an RFCE's names
``consultatimbrefc`` on the ``fc`` host with ``RncEmisor``, ``ENCF``, ``MontoTotal`` and ``CodigoSeguridad``.
Parameter names are IT's spelling (DT-DGII's examples write them in lower case; DGII's paths and queries are
case-insensitive). Values are percent-encoded except ``-``, ``:`` and ``.`` (IT's example writes
``dd-MM-yyyy%20HH:mm:ss``), so a security code's base64 ``+``, ``/`` and ``=`` are encoded. QR version 8.
"""

from dataclasses import dataclass
from typing import ClassVar, Final, final
from urllib.parse import quote
lazy from datetime import date, datetime
lazy from decimal import Decimal

from ledger_country_do.channels.ecf_v1.domain.environments import OFFICIAL_ECF_HOST, OFFICIAL_FC_HOST
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_amount, dgii_date, dgii_moment
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment

SECURITY_CODE_LENGTH: Final = 6
_KEPT: Final = "-:."


class SecurityCodeError(EcfError):
    """A signature value too short to give a security code: the signer answered something it never answers."""

    code: ClassVar[str] = "do_ecf_security_code"

    def __init__(self) -> None:
        super().__init__(f"a signature value holds at least {SECURITY_CODE_LENGTH} characters")


def security_code(signature_value: str) -> str:
    """The first six characters of ``signature_value``, whitespace removed."""
    compact = "".join(signature_value.split())
    if len(compact) < SECURITY_CODE_LENGTH:
        raise SecurityCodeError
    return compact[:SECURITY_CODE_LENGTH]


@final
@dataclass(frozen=True, slots=True)
class QrHosts:
    """Where the QR's queries are answered: DGII's two hosts, or a fake's."""

    ecf: str
    fc: str


OFFICIAL_QR: Final = QrHosts(ecf=OFFICIAL_ECF_HOST, fc=OFFICIAL_FC_HOST)


@final
@dataclass(frozen=True, slots=True)
class EcfQrData:
    """What the QR of an e-CF sent in full carries (see the module); ``buyer_rnc`` is ``None`` without one."""

    issuer_rnc: str
    buyer_rnc: str | None
    encf: Encf
    issued_on: date
    total: Decimal
    signed_at: datetime
    security_code: str


@final
@dataclass(frozen=True, slots=True)
class RfceQrData:
    """What the QR of a consumer invoice sent as an RFCE carries (see the module)."""

    issuer_rnc: str
    encf: Encf
    total: Decimal
    security_code: str


def _query(parameters: tuple[tuple[str, str], ...]) -> str:
    return "&".join(f"{name}={quote(value, safe=_KEPT)}" for name, value in parameters)


def qr_url(data: EcfQrData, *, environment: Environment, hosts: QrHosts = OFFICIAL_QR) -> str:
    """The QR URL of an e-CF sent in full (see the module)."""
    parameters = (
        ("RncEmisor", data.issuer_rnc),
        *((("RncComprador", data.buyer_rnc),) if data.buyer_rnc is not None else ()),
        ("ENCF", str(data.encf)),
        ("FechaEmision", dgii_date(data.issued_on)),
        ("MontoTotal", dgii_amount(data.total)),
        ("FechaFirma", dgii_moment(data.signed_at)),
        ("CodigoSeguridad", data.security_code),
    )
    return f"{hosts.ecf}/{environment.value}/consultatimbre?{_query(parameters)}"


def rfce_qr_url(data: RfceQrData, *, environment: Environment, hosts: QrHosts = OFFICIAL_QR) -> str:
    """The QR URL of a consumer invoice sent as an RFCE (see the module)."""
    parameters = (
        ("RncEmisor", data.issuer_rnc),
        ("ENCF", str(data.encf)),
        ("MontoTotal", dgii_amount(data.total)),
        ("CodigoSeguridad", data.security_code),
    )
    return f"{hosts.fc}/{environment.value}/consultatimbrefc?{_query(parameters)}"
