"""``ledger.identity.v1.IdentityQueryService``: what other services ask identity, synchronously (§3, §8).

Every request names the organization it is about, and each answer is read in a transaction scoped to that
organization: a company of another organization does not exist for it. Callers are other services, which the
mesh authenticates (Linkerd, deployment milestone); until then nothing here authenticates a caller, so the
port must never be reachable from outside the services' own network (an accepted risk the spec records). The
ids of a request are checked like any client's input, and a malformed one is ``INVALID_ARGUMENT`` with its
code. The permissions answered are what other services may let the principal do: in a deactivated company,
reading only.
"""

from typing import Final, final, override
lazy from collections.abc import Mapping

lazy import grpc

import ledger.identity.v1.identity_query_pb2 as query
from ledger.identity.v1.identity_query_pb2_grpc import (
    IdentityQueryServiceServicer,
    add_IdentityQueryServiceServicer_to_server,
)
from ledger_identity.application import queries
from ledger_identity.domain.company import CompanyStatus
from ledger_kernel.access import parse_subject
from ledger_kernel.ids import CompanyId, OrganizationId, parse_uuid
from ledger_kernel.tenancy import OrganizationScope, Tenant
lazy from ledger_identity.api.context import BindUnit
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.outbox import Outbox
lazy from ledger_platform.service import GrpcServices, Runtime

_STATUS: Final[Mapping[CompanyStatus, query.CompanyStatus.ValueType]] = {
    CompanyStatus.ACTIVE: query.COMPANY_STATUS_ACTIVE,
    CompanyStatus.INACTIVE: query.COMPANY_STATUS_INACTIVE,
}

type _CompanyContext = grpc.aio.ServicerContext[query.GetCompanyRequest, query.GetCompanyResponse]
type _PermissionsContext = grpc.aio.ServicerContext[query.GetPermissionsRequest, query.GetPermissionsResponse]


def _wire(company: Company) -> query.Company:
    return query.Company(
        id=str(company.id),
        organization_id=str(company.organization),
        tax_id=str(company.tax_id),
        legal_name=company.legal_name,
        status=_STATUS[company.status],
        version=company.version,
    )


@final
class IdentityQuery(IdentityQueryServiceServicer):
    """identity's answers to other services."""

    def __init__(self, database: Database, outbox: Outbox, bind: BindUnit) -> None:
        self._database = database
        self._outbox = outbox
        self._bind = bind

    @override
    async def GetCompany(
        self, request: query.GetCompanyRequest, context: _CompanyContext
    ) -> query.GetCompanyResponse:
        del context
        tenant = Tenant(
            OrganizationId(parse_uuid(request.organization_id)),
            CompanyId(parse_uuid(request.company_id)),
            None,
        )
        async with self._database.transaction(tenant) as transaction:
            company = await queries.company(self._bind(transaction, self._outbox), tenant.company)
        return query.GetCompanyResponse(company=_wire(company))

    @override
    async def GetPermissions(
        self, request: query.GetPermissionsRequest, context: _PermissionsContext
    ) -> query.GetPermissionsResponse:
        del context
        organization = OrganizationId(parse_uuid(request.organization_id))
        company = CompanyId(parse_uuid(request.company_id))
        subject = parse_subject(request.subject)
        async with self._database.transaction(OrganizationScope(organization, None)) as transaction:
            permissions = await queries.permissions_of(
                self._bind(transaction, self._outbox), company, subject
            )
        return query.GetPermissionsResponse(
            permissions=sorted(permission.value for permission in permissions)
        )


def services(bind: BindUnit) -> GrpcServices[IdentitySettings]:
    """What identity's gRPC server serves, once the service runs."""

    def register(runtime: Runtime[IdentitySettings], server: grpc.aio.Server) -> None:
        add_IdentityQueryServiceServicer_to_server(
            IdentityQuery(runtime.database, runtime.outbox, bind), server
        )

    return register
