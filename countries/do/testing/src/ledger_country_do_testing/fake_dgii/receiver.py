"""DGII's directory of electronic receivers and its emisor-receptor simulator (DT-DGII, DT-EMI).

* ``GET {env}/consultadirectorio/api/consultas/listado`` and ``.../obtenerdirectorioporrnc?RNC=``:
  ``[{nombre, rnc, urlRecepcion, urlAceptacion, urlOpcional}]``. In TesteCF DGII answers its own simulator's
  URLs ("se retornarán las URLs de prueba del servicio de emisor receptor habilitado por DGII"), so any RNC
  not registered with the fake resolves to it; in eCF only registered receivers exist; CerteCF has no
  directory (DT-DGII lists TesteCF and eCF only). The URLs are bases (F5).
* The simulator, under ``{env}/emisorreceptor``: ``GET /fe/autenticacion/api/semilla``, ``POST
  /fe/autenticacion/api/validacioncertificado`` (a token of its own, never DGII's), ``POST
  /fe/recepcion/api/ecf`` (bearer when it publishes authentication) answering a **signed ARECF** (*Formato
  Acuse de Recibo v1.0*: ``Estado`` 0 received, 1 not, with ``CodigoMotivoNoRecibido`` 1 specification
  error, 2 signature error, 3 duplicate, 4 buyer RNC not this receiver's), and ``POST
  /fe/aprobacioncomercial/api/ecf`` (a signed ACECF: 200, else 400).

ARECFs are written here as text and signed by signxml with the simulator's own certificate, so the pack's
verifier is tested against a signer that shares no code with its own.
"""

import re
from http import HTTPStatus
from typing import Final, final
from xml.sax.saxutils import escape
lazy from datetime import datetime

from starlette.responses import JSONResponse, Response
lazy from starlette.requests import Request

from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO
from ledger_country_do_testing.fake_dgii.emitter import (
    ECF_FORMATS,
    NOT_A_FILE,
    bad_request,
    fields,
    readable,
    schema_problems,
    seed_answer,
    upload,
    validate_seed,
)
from ledger_country_do_testing.fake_dgii.state import DirectoryEntry, Received, Status
from ledger_country_do_testing.signatures import sign_with_signxml
lazy from ledger_country_do_testing.fake_dgii.emitter import Handler
lazy from ledger_country_do_testing.fake_dgii.state import FakeState

EMISOR_RECEPTOR: Final = "emisorreceptor"
SIMULATOR_NAME: Final = "EMISOR RECEPTOR DGII"
WITHOUT_DIRECTORY: Final = "certecf"
"""DT-DGII publishes ``consultadirectorio`` (as ``consultatrackids``) for TesteCF and eCF only."""
SPECIFICATION_ERROR: Final = 1
SIGNATURE_ERROR: Final = 2
DUPLICATE: Final = 3
NOT_THIS_BUYER: Final = 4
_MOMENT: Final = "%d-%m-%Y %H:%M:%S"
_ARECF_RNC: Final = re.compile(r"[0-9]{9}|[0-9]{11}")
_ARECF_ENCF: Final = re.compile(r"[a-zA-Z0-9]{13}|[a-zA-Z0-9]{11}|[a-zA-Z0-9]{9}")
"""The ARECF's ``RNCType`` and ``eNCFType`` (*Formato Acuse de Recibo v1.0*): what an ARECF can name."""
NO_ACKNOWLEDGEMENT: Final = "El RNC Emisor, el RNC Comprador o el eNCF no son válidos para un acuse de recibo"
"""The fake's words: DGII documents no answer to an e-CF its acknowledgement could not name."""


def arecf(issuer_rnc: str, buyer_rnc: str, encf: str, *, reason: int | None, at: datetime) -> bytes:
    """An unsigned ARECF: received when ``reason`` is ``None``, else not received for ``reason``."""
    state, why = (
        ("0", "") if reason is None else ("1", f"<CodigoMotivoNoRecibido>{reason}</CodigoMotivoNoRecibido>")
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?><ARECF><DetalleAcusedeRecibo><Version>1.0</Version>'
        f"<RNCEmisor>{escape(issuer_rnc)}</RNCEmisor><RNCComprador>{escape(buyer_rnc)}</RNCComprador>"
        f"<eNCF>{escape(encf)}</eNCF><Estado>{state}</Estado>{why}"
        f"<FechaHoraAcuseRecibo>{at.astimezone(SANTO_DOMINGO).strftime(_MOMENT)}</FechaHoraAcuseRecibo>"
        "</DetalleAcusedeRecibo></ARECF>"
    ).encode()


@final
class ReceiverServices:
    """The directory and the simulator over a ``FakeState`` (see the module)."""

    __slots__ = ("_seen", "_state")

    def __init__(self, state: FakeState) -> None:
        self._state = state
        self._seen: set[tuple[str, str, str]] = set()
        """``(environment, issuer, e-NCF)`` received: each environment is its own (TesteCF, CerteCF)."""

    def handlers(self) -> dict[tuple[str, str], Handler]:
        """Each service's handler by method and lower-case path after the environment's segment."""
        simulator = f"{EMISOR_RECEPTOR}/fe"
        return {
            ("GET", "consultadirectorio/api/consultas/listado"): self._listing,
            ("GET", "consultadirectorio/api/consultas/obtenerdirectorioporrnc"): self._by_rnc,
            ("GET", f"{simulator}/autenticacion/api/semilla"): self._seed,
            ("POST", f"{simulator}/autenticacion/api/validacioncertificado"): self._validate,
            ("POST", f"{simulator}/recepcion/api/ecf"): self._closed(self._reception),
            ("POST", f"{simulator}/aprobacioncomercial/api/ecf"): self._closed(self._approval),
        }

    def _closed(self, handler: Handler) -> Handler:
        async def authorized(request: Request, environment: str) -> Response:
            granted = self._state.receiver_credentials.valid(request.headers.get("authorization", ""))
            if self._state.receiver_requires_auth and not granted:
                return Response(status_code=HTTPStatus.UNAUTHORIZED)
            return await handler(request, environment)

        return authorized

    def _simulator(self, environment: str, rnc: str) -> DirectoryEntry:
        base = f"{self._state.public_url}/{environment}/{EMISOR_RECEPTOR}"
        authentication = base if self._state.receiver_requires_auth else None
        return DirectoryEntry(
            name=SIMULATOR_NAME, rnc=rnc, reception=base, approval=base, authentication=authentication
        )

    @staticmethod
    def _listed(entries: list[DirectoryEntry]) -> Response:
        return JSONResponse(
            [
                {
                    "nombre": entry.name,
                    "rnc": entry.rnc,
                    "urlRecepcion": entry.reception,
                    "urlAceptacion": entry.approval,
                    "urlOpcional": entry.authentication,
                }
                for entry in entries
            ]
        )

    async def _listing(self, request: Request, environment: str) -> Response:
        del request
        if environment == WITHOUT_DIRECTORY:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        return self._listed(list(self._state.directory.values()))

    async def _by_rnc(self, request: Request, environment: str) -> Response:
        if environment == WITHOUT_DIRECTORY:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        rnc = request.query_params.get("RNC", "")
        registered = self._state.directory.get(rnc)
        if registered is not None:
            return self._listed([registered])
        return self._listed([self._simulator(environment, rnc)] if environment == "testecf" else [])

    async def _seed(self, request: Request, environment: str) -> Response:
        del request, environment
        return seed_answer(*self._state.receiver_credentials.seed())

    async def _validate(self, request: Request, environment: str) -> Response:
        del environment
        return await validate_seed(request, self._state.check, self._state.receiver_credentials)

    def _reason(self, document: bytes, environment: str, named: tuple[str, str, str, str]) -> int | None:
        ecf_type, issuer, buyer, encf = named
        format_ = ECF_FORMATS.get(ecf_type)
        if format_ is None or schema_problems(format_, document):
            return SPECIFICATION_ERROR
        if not self._state.check.verify(document):
            return SIGNATURE_ERROR
        if (environment, issuer, encf) in self._seen:
            return DUPLICATE
        accepted = self._state.receiver_rncs
        if accepted is not None and buyer not in accepted:
            return NOT_THIS_BUYER
        return None

    async def _reception(self, request: Request, environment: str) -> Response:
        sent = await upload(request)
        if sent is None or not readable(sent[0]):
            return bad_request(NOT_A_FILE)
        document, file_name = sent
        ecf_type, issuer, buyer, encf = fields(
            document,
            "Encabezado/IdDoc/TipoeCF",
            "Encabezado/Emisor/RNCEmisor",
            "Encabezado/Comprador/RNCComprador",
            "Encabezado/IdDoc/eNCF",
        )
        if not (_ARECF_RNC.fullmatch(issuer) and _ARECF_RNC.fullmatch(buyer) and _ARECF_ENCF.fullmatch(encf)):
            return bad_request(NO_ACKNOWLEDGEMENT)
        reason = self._reason(document, environment, (ecf_type, issuer, buyer, encf))
        if reason is None:
            self._seen.add((environment, issuer, encf))
        now = self._state.clock()
        self._state.received.append(
            Received(
                track_id=None,
                environment=environment,
                host=request.headers.get("host", ""),
                kind="buyer",
                file_name=file_name,
                rnc=issuer,
                encf=encf,
                security_code="",
                document=document,
                received_at=now,
                status=Status.ACCEPTED if reason is None else Status.REJECTED,
                messages=(),
                polls_left=0,
                sequence_used=True,
            )
        )
        signed = sign_with_signxml(arecf(issuer, buyer, encf, reason=reason, at=now), self._state.receiver)
        if self._state.tamper_arecf:
            signed = signed.replace(b"<Estado>0</Estado>", b"<Estado>1</Estado>")
        return Response(signed, media_type="text/xml")

    async def _approval(self, request: Request, environment: str) -> Response:
        del environment
        sent = await upload(request)
        if sent is None or not readable(sent[0]) or schema_problems(Format.ACECF, sent[0]):
            return Response(status_code=HTTPStatus.BAD_REQUEST)
        if not self._state.check.verify(sent[0]):
            return Response(status_code=HTTPStatus.BAD_REQUEST)
        self._state.approvals.append(sent[0])
        return Response(status_code=HTTPStatus.OK)
