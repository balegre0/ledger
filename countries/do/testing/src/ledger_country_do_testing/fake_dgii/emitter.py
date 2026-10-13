"""DGII's services for emitters, answered as DT-DGII (02-01-2026) documents them.

* ``GET {env}/autenticacion/api/autenticacion/semilla``: a ``SemillaModel``; ``POST .../validarsemilla``
  takes it back signed (multipart ``xml``) and answers ``{token, expira, expedido}`` (one hour).
* ``POST {env}/recepcion/api/facturaselectronicas`` (bearer): ``{trackId, error, mensaje}``; a type 32
  below RD$250,000 is refused there (F3: the fake answers 400).
* ``GET {env}/consultaresultado/api/consultas/estado?trackid=`` (bearer): ``{trackId, codigo, estado, rnc,
  eNCF, secuenciaUtilizada, fechaRecepcion, mensajes[{valor, codigo}]}``.
* ``GET {env}/consultatrackids/api/trackids/consulta?rncemisor=&encf=`` (bearer; not in CerteCF, DT-DGII
  28-12-2020): ``[{trackId, estado, fechaRecepcion}]``, its inputs validated as DT-DGII lists.
* ``POST {env}/recepcionfc/api/recepcion/ecf`` (bearer; DGII's ``fc`` host): decided at once, ``{codigo,
  estado, mensajes[{codigo, valor}], encf, secuenciaUtilizada}``, no track id.
* ``GET {env}/consultarfce/api/Consultas/Consulta?RNC_Emisor=&ENCF=&Cod_Seguridad_eCF=`` (bearer): ``{rnc,
  encf, secuenciaUtilizada, codigo, estado, mensajes[{valor, codigo}]}``, its ``codigo`` a *string* of its
  own set (``RFCE_QUERY_CODES``), not ``consultaresultado``'s.

DGII is an ASP.NET service: paths (``app``) and query parameter names (``query``) are read whatever their
case.

A fake with teeth: each document is checked against DGII's XSD by ``xmlschema`` (sharing no code with the
pack's libxml2 validation) and its signature by a ``SignatureCheck`` (by default the pack's verifier, strict,
and signxml); a document that fails is rejected with ``secuenciaUtilizada = false``, as DGII rejects it (an
invalid structure or signature releases the number). Otherwise it answers what the ``Script`` says.
"""

import re
import secrets
from datetime import UTC
from decimal import Decimal, InvalidOperation
from functools import cache
from http import HTTPStatus
from types import MappingProxyType
from typing import Final, final
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from datetime import datetime

import xmlschema
from starlette.datastructures import UploadFile
from starlette.responses import JSONResponse, Response
lazy from starlette.requests import Request

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import UnreadableDocumentError, texts
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import corrected
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO
from ledger_country_do_testing.fake_dgii.state import STATUS_WORDS, Received, Status
lazy from ledger_country_do_testing.fake_dgii.state import Credentials, FakeState, SignatureCheck

type Handler = Callable[[Request, str], Awaitable[Response]]

WITHOUT_TRACK_IDS: Final = "certecf"
RFCE_THRESHOLD: Final = Decimal("250000.00")
NOT_A_FILE: Final = "Tipo de archivo no válido, favor proveer un XML"
BAD_SIGNATURE: Final = "La firma del XML no es válida"
NOT_A_TYPE: Final = "El tipo de comprobante no es válido"
SUMMARY_ONLY: Final = "Las facturas de consumo menores a RD$250,000.00 se envían como resumen (RFCE)"
SUMMARY_TOO_LARGE: Final = "Las facturas de consumo de RD$250,000.00 o más se envían completas por recepción"
RNC_REQUIRED: Final = "El campo RNC Emisor es requerido."
ENCF_REQUIRED: Final = "El campo ENCF es requerido."
RNC_LENGTH: Final = "La longitud del RNC Emisor es inválida."
ENCF_LENGTH: Final = "La longitud del ENCF es inválida."
ENCF_INVALID: Final = "El ENCF es inválido."
"""DT-DGII's validations of ``consultatrackids``, in its words."""
ECF_FORMATS: Final = {
    "31": Format.ECF_31,
    "32": Format.ECF_32,
    "33": Format.ECF_33,
    "34": Format.ECF_34,
    "41": Format.ECF_41,
    "43": Format.ECF_43,
    "44": Format.ECF_44,
    "45": Format.ECF_45,
    "46": Format.ECF_46,
    "47": Format.ECF_47,
}
RFCE_QUERY_CODES: Final[Mapping[Status, str]] = MappingProxyType(
    {
        Status.NOT_FOUND: "0",
        Status.ACCEPTED: "1",
        Status.REJECTED: "2",
        Status.IN_PROCESS: "3",
        Status.ACCEPTED_CONDITIONALLY: "4",
    }
)
"""``consultarfce``'s ``codigo``, a string. DT-DGII documents 0 (No encontrado: not, or not yet, reported), 1
and 2, and "temporarily" shows Aceptado Condicional without giving its code (the fake answers "4", as the
RFCE reception does; F2). "3" is never DGII's here: an RFCE is decided at once, so only a script gets one."""
RNC_LENGTHS: Final = frozenset({9, 11})
"""An RNC (9 digits) or a cédula (11)."""
ENCF_LENGTH_OF: Final = 13
_ENCF: Final = re.compile(r"E\d{12}", re.IGNORECASE)
_UTC: Final = "%Y-%m-%dT%H:%M:%SZ"
_REPORTED: Final = 5


@cache
def _schema(format_: Format) -> xmlschema.XMLSchema10:
    return xmlschema.XMLSchema10(corrected(format_).decode("utf-8"))


def schema_problems(format_: Format, document: bytes) -> tuple[tuple[str, str], ...]:
    """The objections xmlschema raises to ``document``, as DGII-like messages (code 1), at most five."""
    errors = list(_schema(format_).iter_errors(document.decode("utf-8")))[:_REPORTED]
    return tuple(("1", f"El XML no cumple con el XSD: {error.path}") for error in errors)


def fields(document: bytes, *paths: str) -> tuple[str, ...]:
    """The text at each path, stripped; empty where absent."""
    return tuple((found or "").strip() for found in texts(document, *paths))


def readable(document: bytes) -> bool:
    """Whether ``document`` is XML the hardened parser reads."""
    try:
        texts(document)
    except UnreadableDocumentError:
        return False
    return True


def query(request: Request, name: str) -> str:
    """The query parameter ``name``, its case ignored as ASP.NET's binding ignores it; empty if absent."""
    wanted = name.lower()
    return next((value for key, value in request.query_params.multi_items() if key.lower() == wanted), "")


async def upload(request: Request) -> tuple[bytes, str] | None:
    """The multipart field ``xml`` (DGII's one input) and its file name; ``None`` without it."""
    async with request.form() as form:
        found = form.get("xml")
        if not isinstance(found, UploadFile):
            return None
        return await found.read(), found.filename or ""


def bad_request(message: str) -> Response:
    """DGII's 400 answer to a document it does not take: ``{trackId: null, error, mensaje}``."""
    return JSONResponse(
        {"trackId": None, "error": "400", "mensaje": message}, status_code=HTTPStatus.BAD_REQUEST
    )


def refused(message: str) -> Response:
    """A query DGII refuses: 400 with its message (DT-DGII gives the words, not the body: assumed)."""
    return JSONResponse({"error": "400", "mensaje": message}, status_code=HTTPStatus.BAD_REQUEST)


def utc(moment: datetime) -> str:
    """DGII's token dates: UTC, ``yyyy-MM-ddTHH:mm:ssZ``."""
    return moment.astimezone(UTC).strftime(_UTC)


def seed_answer(value: str, at: datetime) -> Response:
    """A ``SemillaModel`` as DGII writes it."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<SemillaModel xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        f"<valor>{value}</valor><fecha>{at.astimezone(SANTO_DOMINGO).isoformat()}</fecha></SemillaModel>"
    )
    return Response(body, media_type="text/xml")


async def validate_seed(request: Request, check: SignatureCheck, credentials: Credentials) -> Response:
    """A signed seed exchanged for a token: DGII's ``validarsemilla``, a receiver's ``validacioncertificado``.

    The seed must be one handed out, unexpired and unused, and ``check`` must trust its signature.
    """
    sent = await upload(request)
    if sent is None or not readable(sent[0]) or schema_problems(Format.SEED, sent[0]):
        return bad_request(NOT_A_FILE)
    (value,) = fields(sent[0], "valor")
    if not credentials.take_seed(value) or not check.verify(sent[0]):
        return bad_request(BAD_SIGNATURE)
    token, issued_at, expires = credentials.token()
    return JSONResponse({"token": token, "expira": utc(expires), "expedido": utc(issued_at)})


def _below_threshold(total: str) -> bool:
    try:
        return Decimal(total) < RFCE_THRESHOLD
    except InvalidOperation:
        return False


def _messages(received: Received) -> list[dict[str, object]]:
    """``mensajes`` as the result queries write them: ``{valor, codigo}``, the code a number."""
    return [
        {"valor": text, "codigo": int(code) if code.isdigit() else code} for code, text in received.messages
    ]


def _track_ids_problem(rnc: str, encf: str) -> str | None:
    if not rnc:
        return RNC_REQUIRED
    if not encf:
        return ENCF_REQUIRED
    if len(rnc) not in RNC_LENGTHS:
        return RNC_LENGTH
    if len(encf) != ENCF_LENGTH_OF:
        return ENCF_LENGTH
    if _ENCF.fullmatch(encf) is None:
        return ENCF_INVALID
    return None


@final
class EmitterServices:
    """DGII's emitter-facing services over a ``FakeState`` (see the module)."""

    __slots__ = ("_state",)

    def __init__(self, state: FakeState) -> None:
        self._state = state

    def handlers(self) -> dict[tuple[str, str], Handler]:
        """Each service's handler by method and path after the environment's segment (lower case)."""
        return {
            ("GET", "autenticacion/api/autenticacion/semilla"): self._seed,
            ("POST", "autenticacion/api/autenticacion/validarsemilla"): self._validate,
            ("POST", "recepcion/api/facturaselectronicas"): self._closed(self._reception),
            ("GET", "consultaresultado/api/consultas/estado"): self._closed(self._result),
            ("GET", "consultatrackids/api/trackids/consulta"): self._closed(self._track_ids),
            ("POST", "recepcionfc/api/recepcion/ecf"): self._closed(self._summary),
            ("GET", "consultarfce/api/consultas/consulta"): self._closed(self._summary_status),
        }

    def _closed(self, handler: Handler) -> Handler:
        async def authorized(request: Request, environment: str) -> Response:
            if not self._state.dgii.valid(request.headers.get("authorization", "")):
                return Response(status_code=HTTPStatus.UNAUTHORIZED)
            return await handler(request, environment)

        return authorized

    async def _seed(self, request: Request, environment: str) -> Response:
        del request, environment
        return seed_answer(*self._state.dgii.seed())

    async def _validate(self, request: Request, environment: str) -> Response:
        del environment
        return await validate_seed(request, self._state.check, self._state.dgii)

    def record(
        self, request: Request, environment: str, *, kind: str, format_: Format, sent: tuple[bytes, str]
    ) -> Received:
        """``sent`` checked (schema, signature), scripted, and remembered."""
        document, file_name = sent
        problems = schema_problems(format_, document)
        if not problems and not self._state.check.verify(document):
            problems = (("2", BAD_SIGNATURE),)
        encf, rnc, code = fields(
            document, "Encabezado/IdDoc/eNCF", "Encabezado/Emisor/RNCEmisor", "Encabezado/CodigoSeguridadeCF"
        )
        scripted = self._state.script.by_encf.get(encf, self._state.script.default)
        rejected = bool(problems)
        tracked = kind == "ecf"
        received = Received(
            track_id=secrets.token_hex(16) if tracked else None,
            environment=environment,
            host=request.headers.get("host", ""),
            kind=kind,
            file_name=file_name,
            rnc=rnc,
            encf=encf,
            security_code=code,
            document=document,
            received_at=self._state.clock(),
            status=Status.REJECTED if rejected else scripted.status,
            messages=problems or scripted.messages,
            polls_left=scripted.processing if tracked and not rejected else 0,
            sequence_used=not rejected and scripted.sequence_used,
        )
        self._state.received.append(received)
        return received

    async def _reception(self, request: Request, environment: str) -> Response:
        sent = await upload(request)
        if sent is None or not readable(sent[0]):
            return bad_request(NOT_A_FILE)
        ecf_type, total = fields(sent[0], "Encabezado/IdDoc/TipoeCF", "Encabezado/Totales/MontoTotal")
        format_ = ECF_FORMATS.get(ecf_type)
        if format_ is None:
            return bad_request(NOT_A_TYPE)
        if format_ is Format.ECF_32 and _below_threshold(total):
            return bad_request(SUMMARY_ONLY)
        received = self.record(request, environment, kind="ecf", format_=format_, sent=sent)
        return JSONResponse({"trackId": received.track_id, "error": None, "mensaje": None})

    async def _result(self, request: Request, environment: str) -> Response:
        del environment
        track_id = query(request, "trackid")
        found = next((each for each in self._state.received if each.track_id == track_id), None)
        if found is None:
            words = STATUS_WORDS[Status.NOT_FOUND]
            return JSONResponse({"trackId": track_id, "codigo": 0, "estado": words, "mensajes": []})
        status = found.status
        if found.polls_left:
            found.polls_left -= 1
            status = Status.IN_PROCESS
        settled = status is not Status.IN_PROCESS
        return JSONResponse(
            {
                "trackId": track_id,
                "codigo": int(status),
                "estado": STATUS_WORDS[status],
                "rnc": found.rnc,
                "eNCF": found.encf,
                "secuenciaUtilizada": found.sequence_used if settled else None,
                "fechaRecepcion": found.received_at.isoformat(),
                "mensajes": _messages(found) if settled else [],
            }
        )

    async def _track_ids(self, request: Request, environment: str) -> Response:
        if environment == WITHOUT_TRACK_IDS:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        rnc, encf = query(request, "rncemisor"), query(request, "encf")
        if (problem := _track_ids_problem(rnc, encf)) is not None:
            return refused(problem)
        wanted = ("ecf", rnc, encf.upper())
        found = [each for each in self._state.received if (each.kind, each.rnc, each.encf) == wanted]
        return JSONResponse(
            [
                {
                    "trackId": each.track_id,
                    "estado": STATUS_WORDS[each.status],
                    "fechaRecepcion": each.received_at.isoformat(),
                }
                for each in found
            ]
        )

    async def _summary(self, request: Request, environment: str) -> Response:
        sent = await upload(request)
        if sent is None or not readable(sent[0]):
            return bad_request(NOT_A_FILE)
        (total,) = fields(sent[0], "Encabezado/Totales/MontoTotal")
        received = self.record(request, environment, kind="rfce", format_=Format.RFCE, sent=sent)
        if received.status is not Status.REJECTED and not _below_threshold(total):
            received.status, received.messages, received.sequence_used = (
                Status.REJECTED,
                (("3", SUMMARY_TOO_LARGE),),
                True,
            )
        return JSONResponse(
            {
                "codigo": int(received.status),
                "estado": STATUS_WORDS[received.status],
                "mensajes": [{"codigo": code, "valor": text} for code, text in received.messages],
                "encf": received.encf,
                "secuenciaUtilizada": received.sequence_used,
            }
        )

    async def _summary_status(self, request: Request, environment: str) -> Response:
        del environment
        rnc, encf = query(request, "RNC_Emisor"), query(request, "ENCF")
        code = query(request, "Cod_Seguridad_eCF")
        found = next(
            (
                each
                for each in self._state.received
                if (each.kind, each.rnc, each.encf, each.security_code) == ("rfce", rnc, encf.upper(), code)
            ),
            None,
        )
        status = Status.NOT_FOUND if found is None else found.status
        return JSONResponse(
            {
                "rnc": rnc,
                "encf": encf,
                "secuenciaUtilizada": None if found is None else found.sequence_used,
                "codigo": RFCE_QUERY_CODES[status],
                "estado": STATUS_WORDS[status],
                "mensajes": [] if found is None else _messages(found),
            }
        )
