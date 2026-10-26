"""``/v1/companies``: register, list, read, change and deactivate companies.

A route checks, in this order: the caller (401), its access to the company (404) and its permission there
(403), as dependencies; then what the request must carry (``Ledger-Mode``, ``Idempotency-Key``, ``If-Match``)
and its body (400, 422, 428). A ``POST`` acts in the request's mode and claims its ``Idempotency-Key`` in that
mode (``Idempotency.once``) before any statement of its own, in the unit of its work: a retry replays, a twin
waits for the first and replays, a failure frees the key. A change names the versions it may apply to in
``If-Match``: the use case locks the company, judges the caller on the role it holds then, and compares and
sets; a version that moved on meanwhile answers 412.
"""

from http import HTTPStatus
from typing import Annotated
lazy from collections.abc import Sequence

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.adapters.http.models import CompanyView, RegisteredCompany, RegistrationWarning
from ledger_identity.adapters.http.public import company_ref
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import commands, queries
from ledger_identity.domain.company import FiscalProfile, Keep, require_address_fits, require_fits
from ledger_identity.domain.errors import DuplicateTaxIdError, StaleVersionError
from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_identity.domain.roles import COMPANIES_MANAGE, COMPANIES_READ
from ledger_identity.domain.text import TimeZone, parse_address, parse_legal_name, parse_time_zone
from ledger_kernel.jurisdiction import parse_jurisdiction
from ledger_kernel.money import Currency
from ledger_kernel.tax_ids import TaxIdCheck
from ledger_kernel.tenancy import OrganizationScope, Tenant
from ledger_platform.http import (
    CURRENT_VERSION,
    REPLAYED_HEADER,
    Page,
    allowed,
    answer,
    attempt_of,
    etag,
    holding,
    idempotency_key,
    if_match,
    if_match_required,
    in_organization,
    json_response,
    page_of_items,
    page_query,
    precondition_checked,
    problems,
    success,
)
lazy from ledger_identity.adapters.http.context import ApiContext
lazy from ledger_identity.adapters.http.models import AddressBody, CompanyChanges, NewCompany, TaxIdBody
lazy from ledger_identity.domain.company import Company
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.addresses import FiscalAddress
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_kernel.regimes import ParsedTaxId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.auth import CompanyAccess
lazy from ledger_platform.http import IfMatch, StoredResponse


def stored(
    status: HTTPStatus, company: Company, warnings: Sequence[RegistrationWarning] | None = None
) -> StoredResponse:
    """A company's answer, as kept for replays: its view and version, and where it lives once created.

    A registration (``warnings`` given, perhaps none) also says what it accepted with a warning.
    """
    headers = {"ETag": etag(company.version)}
    if status is HTTPStatus.CREATED:
        headers["Location"] = f"/v1/companies/{company_ref(company.id)}"
    view = CompanyView.of(company) if warnings is None else RegisteredCompany.registered(company, warnings)
    return json_response(status, view, headers)


def tax_ids_of(context: ApiContext, sent: Sequence[TaxIdBody]) -> list[ParsedTaxId]:
    """The tax ids a request lists, each read by its scheme; one listed twice is refused (once normalized)."""
    read = [context.tax_ids.parse(item.scheme, item.value) for item in sent]
    if len({item.tax_id for item in read}) != len(read):
        raise DuplicateTaxIdError
    return read


def address_of(sent: AddressBody | None) -> FiscalAddress | None:
    """The fiscal address a request states, its line cleaned and its codes shaped; ``None`` for none."""
    if sent is None:
        return None
    return parse_address(sent.line, municipality=sent.municipality, province=sent.province)


def router(context: ApiContext) -> APIRouter:
    """The companies' routes."""
    companies = APIRouter(prefix="/v1/companies", tags=["companies"])
    caller, database, mode_of = context.caller, context.database, context.mode
    in_company = context.in_company
    reader, manager = (
        allowed(in_company, COMPANIES_READ),
        allowed(in_company, COMPANIES_MANAGE),
    )
    registrar, member = holding(caller, ORGANIZATION_ADMIN), in_organization(caller)

    @companies.post(
        "",
        status_code=HTTPStatus.CREATED,
        response_model=RegisteredCompany,
        responses=problems(400, 401, 403, 409, 422)
        | success(HTTPStatus.CREATED, "Location", "ETag", REPLAYED_HEADER),
    )
    async def create_company(
        principal: Annotated[Principal, Depends(registrar)],
        mode: Annotated[Mode, Depends(mode_of)],
        body: NewCompany,
        request: Request,
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Response:
        """Register a company of the caller's organization; the caller becomes its owner.

        The caller must hold the identity provider's project role ``ledger.organization.admin``. The request
        acts in ``Ledger-Mode``: its ``Idempotency-Key`` is kept per mode. The company is both modes'. Its
        fiscal profile must fit the installed regime of its jurisdiction: one no regime governs is refused
        (``jurisdiction_unsupported``), as are a functional currency the regime does not let it keep its books
        in (``currency_unsupported``) and a tax id of another country's scheme where the regime allows its own
        country's only (``tax_id_foreign``). A fiscal address is optional: its regime says what it takes (in
        the Dominican Republic a line of up to 100 characters, ``invalid_name`` beyond, and codes of DGII's
        catalogue, ``address_code_unknown`` otherwise).
        """
        read = tax_ids_of(context, body.tax_ids)
        jurisdiction = parse_jurisdiction(body.jurisdiction.country, body.jurisdiction.subdivision)
        profile = FiscalProfile(
            jurisdiction=jurisdiction,
            functional_currency=Currency.of(body.functional_currency),
            tax_ids=tuple(item.tax_id for item in read),
        )
        regime = require_fits(profile, context.regime_of(jurisdiction))
        address = address_of(body.address)
        require_address_fits(address, regime)
        time_zone = TimeZone(regime.time_zone) if body.time_zone is None else parse_time_zone(body.time_zone)
        warnings = [
            RegistrationWarning(code="tax_id_checksum_mismatch", field=f"tax_ids.{position}")
            for position, item in enumerate(read)
            if item.check is TaxIdCheck.CHECKSUM_MISMATCH
        ]
        legal_name = parse_legal_name(body.legal_name)
        attempt = attempt_of(request, principal, key, body, company=None)
        async with database.unit(OrganizationScope(principal.organization, mode)) as unit:

            async def register() -> StoredResponse:
                created = await commands.create_company(
                    bind(unit),
                    principal,
                    commands.Registration(
                        profile=profile,
                        legal_name=legal_name,
                        time_zone=time_zone,
                        api_version=CURRENT_VERSION,
                        address=address,
                    ),
                )
                return stored(HTTPStatus.CREATED, created, warnings)

            outcome = await context.idempotency.once(unit, attempt, register)
        return answer(outcome)

    @companies.get("", response_model=Page[CompanyView], responses=problems(401, 422))
    async def list_companies(
        principal: Annotated[Principal, Depends(member)],
        page: Annotated[PageRequest, Depends(page_query)],
    ) -> Page[CompanyView]:
        """The companies of the caller's organization in which it holds a role."""
        async with database.unit(OrganizationScope(principal.organization, None)) as unit:
            found = await queries.companies_of(bind(unit), principal.subject, page)
        return page_of_items(found, CompanyView.of)

    @companies.get(
        "/{company_id}",
        response_model=CompanyView,
        responses=problems(401, 403, 404, 422) | success(HTTPStatus.OK, "ETag"),
    )
    async def read_company(
        access: Annotated[CompanyAccess, Depends(reader)], response: Response
    ) -> CompanyView:
        """A company, with its version as ``ETag``."""
        async with database.unit(access.tenant) as unit:
            found = await queries.company(bind(unit), access.tenant.company)
        response.headers["ETag"] = etag(found.version)
        return CompanyView.of(found)

    @companies.patch(
        "/{company_id}",
        response_model=CompanyView,
        responses=problems(401, 403, 404, 409, 412, 422, 428) | success(HTTPStatus.OK, "ETag"),
        openapi_extra=if_match_required(),
    )
    async def change_company(
        access: Annotated[CompanyAccess, Depends(manager)],
        condition: Annotated[IfMatch, Depends(if_match)],
        body: CompanyChanges,
        response: Response,
    ) -> CompanyView:
        """Change a company, if it is at a version ``If-Match`` names: what the request names changes.

        Its fiscal profile (jurisdiction, functional currency, tax ids) changes only until it is locked (409
        ``fiscal_profile_locked``), and only to one the installed regime of its jurisdiction allows, as at
        registration. ``tax_ids`` is the whole list: an id it no longer lists is released, and one it lists
        anew that an active company holds refuses the change (409 ``tax_id_taken``). A new jurisdiction names
        the zone the MAIN establishment keeps (``time_zone``, 422 ``time_zone_required`` without it), and is
        refused to a company with other establishments (409 ``establishments_exist``). ``address`` is the
        fiscal address as a whole, judged by the regime as at registration, at any time (a locked profile
        does not hold it); ``null`` clears it.
        """
        jurisdiction = body.jurisdiction
        changes = commands.Changes(
            legal_name=None if body.legal_name is None else parse_legal_name(body.legal_name),
            jurisdiction=(
                None
                if jurisdiction is None
                else parse_jurisdiction(jurisdiction.country, jurisdiction.subdivision)
            ),
            functional_currency=None
            if body.functional_currency is None
            else Currency.of(body.functional_currency),
            tax_ids=None
            if body.tax_ids is None
            else tuple(item.tax_id for item in tax_ids_of(context, body.tax_ids)),
            regime_of=context.regime_of,
            time_zone=None if body.time_zone is None else parse_time_zone(body.time_zone),
            address=address_of(body.address) if "address" in body.model_fields_set else Keep.KEEP,
        )
        with precondition_checked(StaleVersionError):
            async with database.unit(access.tenant) as unit:
                changed = await commands.change_company(
                    bind(unit),
                    access.tenant.company,
                    changes,
                    admits=condition.admits,
                    actor=access.principal.subject,
                )
        response.headers["ETag"] = etag(changed.version)
        return CompanyView.of(changed)

    @companies.post(
        "/{company_id}/deactivate",
        response_model=CompanyView,
        responses=problems(400, 401, 403, 404, 409, 412, 422, 428)
        | success(HTTPStatus.OK, "ETag", REPLAYED_HEADER),
        openapi_extra=if_match_required(),
    )
    async def deactivate_company(
        access: Annotated[CompanyAccess, Depends(manager)],
        mode: Annotated[Mode, Depends(mode_of)],
        key: Annotated[str, Depends(idempotency_key)],
        condition: Annotated[IfMatch, Depends(if_match)],
        request: Request,
    ) -> Response:
        """Deactivate a company, if it is at a version ``If-Match`` names: it changes no more.

        The request acts in ``Ledger-Mode``: its ``Idempotency-Key`` is kept per mode.
        """
        attempt = attempt_of(request, access.principal, key, company=access.tenant.company)
        tenant = Tenant(access.tenant.organization, access.tenant.company, mode)
        with precondition_checked(StaleVersionError):
            async with database.unit(tenant) as unit:

                async def deactivate() -> StoredResponse:
                    inactive = await commands.deactivate_company(
                        bind(unit),
                        access.tenant.company,
                        admits=condition.admits,
                        actor=access.principal.subject,
                    )
                    return stored(HTTPStatus.OK, inactive)

                outcome = await context.idempotency.once(unit, attempt, deactivate)
        return answer(outcome)

    return companies
