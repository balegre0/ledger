from typing import Final

import pytest
lazy from google.protobuf.message import Message

from ledger.events.identity.v1.events_pb2 import (
    DESCRIPTOR as EVENTS_DESCRIPTOR,
)
from ledger.events.identity.v1.events_pb2 import (
    ROLE_ADMIN,
    ROLE_OWNER,
    ROLE_UNSPECIFIED,
    CompanyCreated,
    CompanyDeactivated,
    CompanyUpdated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger.identity.v1.identity_query_pb2 import (
    COMPANY_STATUS_ACTIVE,
    Company,
    GetCompanyRequest,
    GetCompanyResponse,
    GetPermissionsRequest,
    GetPermissionsResponse,
)
from ledger.identity.v1.identity_query_pb2 import (
    DESCRIPTOR as QUERY_DESCRIPTOR,
)
from ledger_contracts.envelope import Envelope
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id

TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
EVENTS: Final[tuple[Message, ...]] = (
    CompanyCreated(),
    CompanyUpdated(),
    CompanyDeactivated(),
    MembershipGranted(),
    MembershipRevoked(),
)


@pytest.mark.parametrize("event", EVENTS, ids=[type(event).__name__ for event in EVENTS])
def test_every_identity_event_is_a_message_of_the_versioned_events_package(event: Message) -> None:
    assert event.DESCRIPTOR.full_name == f"ledger.events.identity.v1.{type(event).__name__}"


def test_an_identity_event_travels_in_an_envelope_and_back() -> None:
    event = MembershipGranted(
        company_id=str(TENANT.company),
        organization_id=str(TENANT.organization),
        subject="394763379928989699",
        role=ROLE_ADMIN,
        previous_role=ROLE_OWNER,
        version=2,
    )
    envelope = Envelope.wrap(event, source="identity", tenant=TENANT)
    read = Envelope.from_headers(envelope.headers(), envelope.data)
    assert read.type == "ledger.events.identity.v1.MembershipGranted"
    assert read.tenant == TENANT
    assert read.unwrap(MembershipGranted) == event


def test_a_first_grant_has_no_previous_role() -> None:
    assert MembershipGranted(role=ROLE_OWNER).previous_role == ROLE_UNSPECIFIED


def test_membership_events_carry_a_version_so_readers_can_order_a_principals_changes() -> None:
    granted = MembershipGranted(version=3)
    revoked = MembershipRevoked(version=4)
    assert (granted.version, revoked.version) == (3, 4)
    assert MembershipRevoked.FromString(revoked.SerializeToString()).version == 4


def test_roles_are_named_as_identity_names_them() -> None:
    assert [value.name for value in EVENTS_DESCRIPTOR.enum_types_by_name["Role"].values] == [
        "ROLE_UNSPECIFIED",
        "ROLE_OWNER",
        "ROLE_ADMIN",
        "ROLE_ACCOUNTANT",
        "ROLE_ISSUER",
        "ROLE_VIEWER",
    ]


def test_the_query_service_answers_companies_and_permissions() -> None:
    service = QUERY_DESCRIPTOR.services_by_name["IdentityQueryService"]
    assert [(method.name, method.input_type.name, method.output_type.name) for method in service.methods] == [
        ("GetCompany", "GetCompanyRequest", "GetCompanyResponse"),
        ("GetPermissions", "GetPermissionsRequest", "GetPermissionsResponse"),
    ]


def test_queries_and_answers_round_trip() -> None:
    company = Company(
        id=str(TENANT.company),
        organization_id=str(TENANT.organization),
        tax_id="101850043",
        legal_name="Ferretería Duarte, S.R.L.",
        status=COMPANY_STATUS_ACTIVE,
        version=3,
    )
    messages: tuple[Message, ...] = (
        GetCompanyRequest(organization_id=str(TENANT.organization), company_id=str(TENANT.company)),
        GetCompanyResponse(company=company),
        GetPermissionsRequest(organization_id="o", company_id="c", subject="s"),
        GetPermissionsResponse(permissions=["company.read", "invoice.issue"]),
    )
    for message in messages:
        assert type(message).FromString(message.SerializeToString()) == message
