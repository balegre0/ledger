"""``/v1/certificates``: a company's signing certificates, installed, read and retired through the signer.

Served by ``api``, which holds no key and opens no bundle (spec §3.7): every route forwards to the signer's
gRPC service (``application.service.CertificateService``) and shows what it answers. The company is the
``Ledger-Company`` header's (§9.7) and the mode ``Ledger-Mode``'s (or the API client's). Every route requires,
as its first dependency, the caller (401) with a role in the company (404) holding the route's permission
(``signer.certificates.read`` or ``.manage``, 403): the signer trusts the company and the actor the api
forwards (until mTLS), so the api asserts them here, before any call.

An import (``POST``) takes ``Idempotency-Key`` and passes it through to ``ImportCertificate``: the key is
claimed and its answer kept by the signer, in the unit that installs the certificate (spec §9.5, plan
Decision 9), scoped there to the organization, the request's mode, the caller and one operation, *import*,
which both import routes are: the same key used for a bundle and for a credential is
``idempotency_key_reused``. A retry of a request that succeeded answers its certificate again with
``Idempotent-Replayed: true``. Unlike a key the module keeps itself, the api cannot ask for an earlier answer
before the call, so a retry also needs ``signer.certificates.manage`` still (a caller downgraded since is
refused 403, never answered).

``POST /v1/certificates`` takes the PKCS#12 bundle as the raw body (``application/x-pkcs12``, else 415), at
most 2 MiB (``larger_body``: 413 as it is read), its password in ``Ledger-Certificate-Password``
(``UNLOCK_HEADER``) and the modes in the query. The password travels as base64 (standard alphabet, padded)
of its UTF-8: a header is Latin-1 to the server, so a password with letters past ASCII (``contraseña``) would
otherwise reach the bundle changed; anything else is 422 ``invalid_request`` naming the header. A header keeps
the body free to stream; it is also what proxies and APM agents log, so each must redact it (ADR 0010,
accepted risks). The body is streamed to the signer as it arrives (``request.stream()``), never buffered whole
nor parsed here; a client gone before it ended is a 400. What the request's headers and query say is judged
before the body's type: a request missing its password or its key is 422 even with the wrong type, which is
415 otherwise. ``POST /v1/certificates/remote`` takes the credential of a certification entity.

Modes follow the request's (ADR 0007: a test-mode credential is safe to hand out): a test-mode request
installs for test alone, a live one for live or both (``certificate_modes_not_allowed``, 422, before the
bundle is read; the signer refuses them too). ``DELETE`` retires a certificate for every mode, naturally
idempotent (no key, Decision 8); like a read, it finds the certificate among those of the request's mode, and
a test-mode request never retires one that serves live (``certificate_serves_live``, 409, the signer's).
What the signer answers is ``problems.answered``'s.
"""

import base64
import binascii
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, ClassVar, Final, final
lazy from collections.abc import AsyncGenerator, Callable, Coroutine

from fastapi import APIRouter, Depends, Header, Query
from pydantic import AfterValidator
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
lazy from fastapi import Request, Response

from ledger_kernel.access import parse_permission
from ledger_kernel.ids import new_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.http import (
    BadRequestError,
    Page,
    allowed,
    company_in_header,
    idempotency_key,
    ledger_mode_of,
    problems,
    success,
)
from ledger_platform.http.idempotency import REPLAYED_HEADER
from ledger_platform.http.limits import larger_body
from ledger_platform.http.problems import current_request_id
from ledger_platform.runtime.calls import Call
from ledger_signer.adapters.http.models import CertificateResource, modes_of, parse_certificate_ref
from ledger_signer.adapters.http.problems import RETRY_AFTER_SECONDS, answered
from ledger_signer.api import CertificateNotFoundError
from ledger_signer.application.importing import ImportRequest, require_import_modes
from ledger_signer.application.ports import Secret
lazy from ledger_kernel.access import Permission
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.auth import CompanyAccess, PermissionSource
lazy from ledger_platform.http import Authentication, InCompany
lazy from ledger_platform.http.mode import ModeDependency
lazy from ledger_platform.http.openapi import Answers
lazy from ledger_signer.adapters.http.models import ModeWord, RemoteImport
lazy from ledger_signer.api import CertificateView
lazy from ledger_signer.application.ports import ImportOutcome
lazy from ledger_signer.application.service import CertificateService

PREFIX: Final = "/v1/certificates"
PKCS12: Final = "application/x-pkcs12"
UNLOCK_HEADER: Final = "Ledger-Certificate-Password"
"""The header that carries a bundle's password: forwarded to the signer, never kept, logged or shown."""
MAX_UNLOCK: Final = 1368
"""The longest header: base64 of a password of up to 1024 bytes of UTF-8."""
CERTIFICATES_READ: Final = parse_permission("signer.certificates.read")
CERTIFICATES_MANAGE: Final = parse_permission("signer.certificates.manage")
_RETRY_AFTER: Final[dict[str, object]] = {
    "Retry-After": {
        "description": f"When to ask again, in seconds ({RETRY_AFTER_SECONDS} for the signer out of reach).",
        "schema": {"type": "string"},
    }
}
_BUNDLE: Final[dict[str, object]] = {
    "requestBody": {
        "required": True,
        "description": "A PKCS#12 bundle (.p12, .pfx): the signing certificate and its RSA key, 2 MiB.",
        "content": {PKCS12: {"schema": {"type": "string", "format": "binary"}}},
    }
}
"""The raw body ``POST /v1/certificates`` reads itself, documented (FastAPI sees no body parameter)."""

type ActingDependency = Callable[[CompanyAccess, Mode], Coroutine[object, object, Acting]]
"""The dependency ``_acting`` makes: the caller, allowed what the route requires, in the request's mode."""


def _refusals(*statuses: int) -> Answers:
    """The problems a route answers, its 503 (a signer out of reach, say) with ``Retry-After``."""
    answers = problems(*statuses)
    unavailable = HTTPStatus.SERVICE_UNAVAILABLE.value
    answers[unavailable] = answers[unavailable] | {"headers": _RETRY_AFTER}
    return answers


READ: Final = _refusals(400, 401, 403, 404, 422)
RETIRE: Final = _refusals(400, 401, 403, 404, 409, 422)
IMPORT: Final = _refusals(400, 401, 403, 404, 408, 409, 413, 415, 422) | success(
    HTTPStatus.CREATED, "Location", REPLAYED_HEADER
)
IMPORT_REMOTE: Final = _refusals(400, 401, 403, 404, 409, 422) | success(
    HTTPStatus.CREATED, "Location", REPLAYED_HEADER
)


class BodyIncompleteError(BadRequestError):
    """The client went away before its body ended: nothing was installed."""

    code: ClassVar[str] = "request_body_incomplete"

    def __init__(self) -> None:
        super().__init__("the request's body ended before it was whole")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Routes:
    """What every route of the certificates shares, built once by the assembly."""

    service: CertificateService
    mode: ModeDependency
    """The request's mode, settled once its caller is proven (``ledger_mode_of``)."""
    in_company: InCompany
    """The company of ``Ledger-Company`` and what the caller may do there."""

    @classmethod
    def build(
        cls, *, authentication: Authentication, permissions: PermissionSource, service: CertificateService
    ) -> Routes:
        """The routes' context: the chassis's caller, its access in the company as ``permissions`` say."""
        caller = authentication.caller

        async def permission_source() -> PermissionSource:
            return permissions

        return cls(
            service=service,
            mode=ledger_mode_of(caller),
            in_company=company_in_header(caller, permission_source),
        )


@final
@dataclass(frozen=True, slots=True)
class Acting:
    """The caller, allowed what the route requires in the request's company, and the mode it acts in."""

    access: CompanyAccess
    mode: Mode

    @property
    def tenant(self) -> Tenant:
        """The request's company, in the request's mode."""
        return Tenant(self.access.tenant.organization, self.access.tenant.company, self.mode)

    @property
    def call(self) -> Call:
        """The call the request makes of the signer: its caller is the actor the signer audits."""
        request_id = current_request_id() or str(new_id())
        return Call(principal=self.access.principal, scope=self.tenant, request_id=request_id)

    def importing(self, key: str, modes: list[ModeWord] | None) -> ImportRequest:
        """An import for the request's company under ``key``, for ``modes`` (by default the request's).

        ``ImportModesRefusedError`` for modes the request's mode may not install, before anything is sent.
        """
        named = modes_of(modes, default=self.mode)
        require_import_modes(self.mode, named)
        return ImportRequest(tenant=self.tenant, modes=named, idempotency_key=key)


class UnlockUnreadableError(ValueError):
    """A password header that is not base64 of UTF-8: pydantic's ``value_error``, its message our own."""

    def __init__(self) -> None:
        super().__init__("the password must be base64 of its UTF-8")


def unlocked(sent: str) -> str:
    """The password ``sent`` carries: base64 (standard alphabet, padded) of its UTF-8; else ``ValueError``.

    The error never repeats what was sent.
    """
    try:
        return base64.b64decode(sent.encode("ascii"), validate=True).decode("utf-8")
    except UnicodeError, binascii.Error:
        raise UnlockUnreadableError from None


def _acting(routes: Routes, permission: Permission) -> ActingDependency:
    """The dependency giving the caller, if it holds ``permission`` in the company, and the request's mode."""
    holds, mode_of = allowed(routes.in_company, permission), routes.mode

    async def acting(
        access: Annotated[CompanyAccess, Depends(holds)], mode: Annotated[Mode, Depends(mode_of)]
    ) -> Acting:
        return Acting(access, mode)

    return acting


def _created(outcome: ImportOutcome, mode: Mode) -> Response:
    """An import's answer: 201, the certificate and where it lives, marked when it is a replay."""
    made = CertificateResource.of(outcome.view, mode=mode)
    headers = {"Location": f"{PREFIX}/{made.id}"}
    if outcome.replayed:
        headers[REPLAYED_HEADER] = "true"
    return Response(
        content=made.model_dump_json(),
        status_code=HTTPStatus.CREATED,
        headers=headers,
        media_type="application/json",
    )


def _is_pkcs12(content_type: str | None) -> bool:
    """Whether ``content_type`` names a PKCS#12 bundle, whatever its parameters and case."""
    return content_type is not None and content_type.partition(";")[0].strip().lower() == PKCS12


async def _streamed(request: Request) -> AsyncGenerator[bytes]:
    """The request's body as it arrives; a client gone before it ended is ``BodyIncompleteError``."""
    try:
        async for chunk in request.stream():
            yield chunk
    except ClientDisconnect:
        raise BodyIncompleteError from None


async def _found(service: CertificateService, caller: Acting, certificate_id: str) -> CertificateView:
    """The certificate ``certificate_id`` names among those of the request's mode, retired ones too."""
    certificate = parse_certificate_ref(certificate_id)
    with answered():
        listed = await service.describe(caller.call, caller.tenant, include_retired=True)
    for view in listed:
        if view.id == certificate:
            return view
    raise CertificateNotFoundError


def _imports(certificates: APIRouter, service: CertificateService, manager: ActingDependency) -> None:
    """The routes that install a certificate: from a PKCS#12 bundle, or from an entity's credential."""

    @certificates.post(
        "",
        status_code=HTTPStatus.CREATED,
        response_model=CertificateResource,
        responses=IMPORT,
        dependencies=[Depends(larger_body())],
        openapi_extra=_BUNDLE,
    )
    async def import_certificate(
        caller: Annotated[Acting, Depends(manager)],
        key: Annotated[str, Depends(idempotency_key)],
        unlock: Annotated[
            str,
            AfterValidator(unlocked),
            Header(
                alias=UNLOCK_HEADER,
                min_length=1,
                max_length=MAX_UNLOCK,
                description=(
                    "The bundle's password, as base64 (standard alphabet, padded) of its UTF-8: it opens the "
                    "bundle in the signer, and is never kept. Redact this header at every proxy, WAF and APM "
                    "agent."
                ),
                json_schema_extra={"format": "password", "writeOnly": True},
            ),
        ],
        request: Request,
        modes: Annotated[
            list[ModeWord] | None,
            Query(
                description="The modes it serves: test, live or both (repeated). By default the request's."
            ),
        ] = None,
    ) -> Response:
        """Install the company's signing certificate from a PKCS#12 bundle, for one mode or both.

        Needs ``signer.certificates.manage``. The certificate replaces, for each mode it serves, the company's
        active one, which keeps the modes no new certificate took and is retired ``replaced`` once it serves
        none. Refused 422 when the bundle cannot be opened with the password
        (``certificate_bundle_unreadable``, whichever was wrong), holds no RSA key with its certificate
        (``certificate_bundle_incomplete``), or its certificate cannot sign: a key under 2048 bits, no
        digital signature usage, a CA, not valid now or for too short a time. The same key with another
        bundle or other modes is ``idempotency_key_reused``. A test-mode request installs for test alone
        (``certificate_modes_not_allowed``). Its outcome can be confirmed with ``GET /v1/certificates``, which
        needs only ``signer.certificates.read``.
        """
        if not _is_pkcs12(request.headers.get("content-type")):
            raise HTTPException(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        imported = caller.importing(key, modes)
        with answered():
            outcome = await service.import_local(caller.call, imported, Secret(unlock), _streamed(request))
        return _created(outcome, caller.mode)

    @certificates.post(
        "/remote", status_code=HTTPStatus.CREATED, response_model=CertificateResource, responses=IMPORT_REMOTE
    )
    async def import_remote_certificate(
        caller: Annotated[Acting, Depends(manager)],
        key: Annotated[str, Depends(idempotency_key)],
        body: RemoteImport,
    ) -> Response:
        """Install the certificate a certification entity signs with for the company's credential.

        Needs ``signer.certificates.manage``; the deployment must offer the remote backend (else 422
        ``backend_not_offered``). It replaces the active certificates as an import of a bundle does.
        """
        imported, credential = caller.importing(key, body.modes), Secret(body.credential.get_secret_value())
        with answered():
            outcome = await service.import_remote(caller.call, imported, credential)
        return _created(outcome, caller.mode)


def _reads(
    certificates: APIRouter, service: CertificateService, reader: ActingDependency, manager: ActingDependency
) -> None:
    """The routes that list, read and retire the company's certificates."""

    @certificates.get("", response_model=Page[CertificateResource], responses=READ)
    async def list_certificates(
        caller: Annotated[Acting, Depends(reader)],
        *,
        include_retired: Annotated[
            bool, Query(description="Whether retired certificates that served the mode are listed too.")
        ] = False,
    ) -> Page[CertificateResource]:
        """The certificates serving the request's mode, active first, the latest first: one page.

        A company holds a handful, so the list is never cut (``has_more`` is false).
        """
        with answered():
            listed = await service.describe(caller.call, caller.tenant, include_retired=include_retired)
        return Page[CertificateResource](
            items=[CertificateResource.of(view, mode=caller.mode) for view in listed],
            has_more=False,
            next_cursor=None,
            previous_cursor=None,
        )

    @certificates.get("/{certificate_id}", response_model=CertificateResource, responses=READ)
    async def read_certificate(
        caller: Annotated[Acting, Depends(reader)], certificate_id: str
    ) -> CertificateResource:
        """A certificate that serves, or served, the request's mode; else 404 ``certificate_not_found``."""
        return CertificateResource.of(await _found(service, caller, certificate_id), mode=caller.mode)

    @certificates.delete("/{certificate_id}", response_model=CertificateResource, responses=RETIRE)
    async def retire_certificate(
        caller: Annotated[Acting, Depends(manager)], certificate_id: str
    ) -> CertificateResource:
        """Retire a certificate of the request's mode for every mode: it never signs again.

        Needs ``signer.certificates.manage``. One retired already is answered as it is. The company then has
        no certificate in the modes it served until it installs another. A test-mode request never retires a
        certificate that serves live: 409 ``certificate_serves_live``.
        """
        view = await _found(service, caller, certificate_id)
        with answered():
            retired = await service.retire(caller.call, caller.tenant, view.id)
        return CertificateResource.of(retired, mode=caller.mode)


def router(routes: Routes) -> APIRouter:
    """The routes of the certificates."""
    certificates = APIRouter(prefix=PREFIX, tags=["certificates"])
    manager = _acting(routes, CERTIFICATES_MANAGE)
    _imports(certificates, routes.service, manager)
    _reads(certificates, routes.service, _acting(routes, CERTIFICATES_READ), manager)
    return certificates
