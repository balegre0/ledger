from typing import Final
from uuid import UUID
lazy from collections.abc import AsyncGenerator

import grpc
import pytest
lazy import httpx

from ledger.identity.v1.identity_query_pb2 import (
    COMPANY_STATUS_ACTIVE,
    COMPANY_STATUS_INACTIVE,
    GetCompanyRequest,
    GetPermissionsRequest,
)
from ledger.identity.v1.identity_query_pb2_grpc import IdentityQueryServiceStub
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.ids import CompanyId
from ledger_platform.authentication import organization_of
from ledger_platform.authorization import CachedPermissions
from ledger_platform.grpc_client import (
    RemoteDefectError,
    RemoteNotFoundError,
    RemoteUnavailableError,
    call_unary,
)
from ledger_platform.identity_client import RemotePermissions
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.service import Service
lazy from ledger_testing.api import Caller

GET_COMPANY: Final = "/ledger.identity.v1.IdentityQueryService/GetCompany"
GET_PERMISSIONS: Final = "/ledger.identity.v1.IdentityQueryService/GetPermissions"
ORGANIZATION: Final = str(organization_of("1001"))
STRANGERS: Final = str(organization_of("1002"))


@pytest.fixture
async def channel(service: Service[IdentitySettings]) -> AsyncGenerator[grpc.aio.Channel]:
    assert service.grpc_port is not None
    async with grpc.aio.insecure_channel(f"127.0.0.1:{service.grpc_port}") as opened:
        yield opened


async def permissions_asked(
    channel: grpc.aio.Channel, organization: str, company: str, subject: str
) -> list[str]:
    request = GetPermissionsRequest(organization_id=organization, company_id=company, subject=subject)
    answer = await call_unary(IdentityQueryServiceStub(channel).GetPermissions, request, rpc=GET_PERMISSIONS)
    return list(answer.permissions)


async def test_a_company_is_answered_as_identity_holds_it(company: str, channel: grpc.aio.Channel) -> None:
    request = GetCompanyRequest(organization_id=ORGANIZATION, company_id=company)
    answer = await call_unary(IdentityQueryServiceStub(channel).GetCompany, request, rpc=GET_COMPANY)
    found = answer.company
    assert (found.id, found.organization_id, found.tax_id, found.legal_name, found.status, found.version) == (
        company,
        ORGANIZATION,
        "101850043",
        "Duarte",
        COMPANY_STATUS_ACTIVE,
        1,
    )


async def test_a_deactivated_company_is_answered_as_inactive_and_lets_others_read_only(
    client: httpx.AsyncClient, owner: Caller, company: str, channel: grpc.aio.Channel
) -> None:
    headers = owner.posting("off") | {"If-Match": '"1"'}
    assert (await client.post(f"/v1/companies/{company}/deactivate", headers=headers)).status_code == 200
    request = GetCompanyRequest(organization_id=ORGANIZATION, company_id=company)
    answer = await call_unary(IdentityQueryServiceStub(channel).GetCompany, request, rpc=GET_COMPANY)
    assert (answer.company.status, answer.company.version) == (COMPANY_STATUS_INACTIVE, 2)
    assert await permissions_asked(channel, ORGANIZATION, company, "2001") == [
        "company.read",
        "invoice.read",
        "membership.read",
    ]


async def test_a_company_of_another_organization_is_not_found(
    company: str, channel: grpc.aio.Channel
) -> None:
    request = GetCompanyRequest(organization_id=STRANGERS, company_id=company)
    with pytest.raises(RemoteNotFoundError) as caught:
        await call_unary(IdentityQueryServiceStub(channel).GetCompany, request, rpc=GET_COMPANY)
    assert caught.value.remote_code == "company_not_found"


@pytest.mark.parametrize(
    ("organization", "company_id", "subject", "code"),
    [
        ("not-an-id", "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f", "2001", "invalid_id"),
        (ORGANIZATION, "", "2001", "invalid_id"),
        (ORGANIZATION, "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f", "not a subject", "invalid_subject"),
    ],
    ids=["organization", "company", "subject"],
)
async def test_a_question_that_cannot_be_read_is_an_invalid_argument(
    channel: grpc.aio.Channel, organization: str, company_id: str, subject: str, code: str
) -> None:
    with pytest.raises(RemoteDefectError) as caught:
        await permissions_asked(channel, organization, company_id, subject)
    assert (caught.value.status, caught.value.remote_code) == (grpc.StatusCode.INVALID_ARGUMENT, code)


@pytest.mark.parametrize(
    ("organization", "company_id", "code"),
    [
        ("not-an-id", "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f", "invalid_id"),
        (ORGANIZATION, "", "invalid_id"),
        (ORGANIZATION, "not-an-id", "invalid_id"),
    ],
    ids=["organization", "empty company", "company"],
)
async def test_a_company_asked_by_a_malformed_id_is_an_invalid_argument(
    channel: grpc.aio.Channel, organization: str, company_id: str, code: str
) -> None:
    request = GetCompanyRequest(organization_id=organization, company_id=company_id)
    with pytest.raises(RemoteDefectError) as caught:
        await call_unary(IdentityQueryServiceStub(channel).GetCompany, request, rpc=GET_COMPANY)
    assert (caught.value.status, caught.value.remote_code) == (grpc.StatusCode.INVALID_ARGUMENT, code)


async def test_a_deadline_that_passes_is_an_unavailable_identity(
    company: str, channel: grpc.aio.Channel
) -> None:
    request = GetCompanyRequest(organization_id=ORGANIZATION, company_id=company)
    with pytest.raises(RemoteUnavailableError) as caught:
        # No answer fits in a microsecond: the deadline passes whatever identity is doing.
        await call_unary(
            IdentityQueryServiceStub(channel).GetCompany, request, rpc=GET_COMPANY, deadline=0.000_001
        )
    assert (caught.value.status, caught.value.remote_code) == (grpc.StatusCode.DEADLINE_EXCEEDED, "unknown")


async def test_permissions_are_the_role_s_in_the_company_and_none_elsewhere(
    company: str, channel: grpc.aio.Channel
) -> None:
    assert await permissions_asked(channel, ORGANIZATION, company, "2001") == sorted(
        permission.value for permission in Permission
    )
    assert await permissions_asked(channel, ORGANIZATION, company, "2002") == []
    assert await permissions_asked(channel, STRANGERS, company, "2001") == []


async def test_the_platform_s_own_client_reads_identity_s_answers(
    client: httpx.AsyncClient, owner: Caller, company: str, channel: grpc.aio.Channel
) -> None:
    company_id = CompanyId(UUID(company))
    founder = Principal(
        subject=SubjectId("2001"), organization=organization_of("1001"), provider_organization="1001"
    )
    cached = CachedPermissions(RemotePermissions(channel))
    assert await cached.permissions(founder, company_id) == frozenset(Permission)
    granted = await client.put(
        f"/v1/companies/{company}/memberships/3001", json={"role": "issuer"}, headers=owner.bearer()
    )
    assert granted.status_code == 201
    issuer = Principal(
        subject=SubjectId("3001"), organization=founder.organization, provider_organization="1001"
    )
    assert await cached.permissions(issuer, company_id) == frozenset(
        {Permission.COMPANY_READ, Permission.INVOICE_READ, Permission.INVOICE_ISSUE}
    )
