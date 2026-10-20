"""fiscal-kit's ``CompanyDirectory`` as fiscal provides it: over identity's API and fiscal's enrollments.

The receiver's question (``receiving_company``) finds the company a tax id names in any organization, while it
has an enrollment that is not disabled on the channel's environment, in the mode that environment serves.
"""

from typing import Final

import pytest
lazy import httpx

from ledger_fiscal.adapters.postgres.unit import PgUnits
from ledger_fiscal.application.directory import FiscalDirectory
from ledger_fiscal_kit.channels import ChannelRegistry
from ledger_fiscal_kit.ports import is_company_directory
from ledger_identity.adapters.postgres.unit import PgUnits as IdentityUnits
from ledger_identity.application.facade import IdentityFacade
from ledger_kernel.access import CompanyNotFoundError, Principal, PrincipalKind, SubjectId, parse_permission
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import COMPANY_PREFIX, CompanyId, OrganizationId, new_id, parse_id
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.runtime.calls import SystemCall
from ledger_platform.runtime.contexts import AssembledChannels
from ledger_testing.api import TestPrincipal, created, document
lazy from ledger_fiscal_kit.channels import Channel
lazy from ledger_fiscal_kit.ports import ReceivingCompany
lazy from ledger_platform.runtime.app import App
lazy from ledger_testing.issuer import LocalIssuer

RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
OTHER_RNC: Final = TaxId(TaxIdScheme("do.rnc"), "130000012")
SYSTEM: Final = SystemCall(reason="test:receiver", scope=None)
PATH: Final = "/v1/fiscal/enrollments"
FAKE: Final = "do-fake-v1"


@pytest.fixture
def directory(api: App, client: httpx.AsyncClient, fake_channel: Channel) -> FiscalDirectory:
    """The directory over the started app's units of identity and fiscal, and the fake channel."""
    del client  # requested so that the app has started: its pools are open
    channels = AssembledChannels()
    channels.add(FAKE, fake_channel)
    channels.seal()
    return FiscalDirectory(
        identity=IdentityFacade(IdentityUnits(api.bound("identity"))),
        units=PgUnits(api.bound("fiscal")),
        channels=ChannelRegistry(channels),
    )


async def enroll(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, environment: str, mode: str = "test"
) -> str:
    headers = owner.posting(f"e-{environment}", mode) | {"Ledger-Company": company}
    return await created(client, PATH, {"channel": FAKE, "environment": environment}, headers)


async def receiving(
    directory: FiscalDirectory, environment: str, tax_id: TaxId = RNC
) -> ReceivingCompany | None:
    return await directory.receiving_company(SYSTEM, tax_id=tax_id, channel=FAKE, environment=environment)


def test_the_directory_is_fiscal_kit_s_company_directory(directory: FiscalDirectory) -> None:
    assert is_company_directory(directory)


async def test_an_enrolled_company_is_found_by_tax_id_in_the_mode_its_environment_serves(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, directory: FiscalDirectory
) -> None:
    assert await receiving(directory, "testecf") is None  # not enrolled yet
    await enroll(client, owner, company, "testecf")
    found = await receiving(directory, "testecf")
    assert found is not None
    assert (found.company, found.mode, found.legal_name, found.tax_id) == (
        CompanyId(parse_id(COMPANY_PREFIX, company)),
        Mode.TEST,
        "Acme SRL",
        RNC,
    )
    assert await receiving(directory, "certecf") is None  # another environment
    assert await receiving(directory, "ecf") is None  # the live one: no live enrollment
    await enroll(client, owner, company, "ecf", mode="live")
    live = await receiving(directory, "ecf")
    assert live is not None
    assert (live.mode, live.tenant) == (Mode.LIVE, Tenant(found.organization, found.company, Mode.LIVE))
    assert await receiving(directory, "nowhere") is None
    assert await receiving(directory, "testecf", OTHER_RNC) is None  # no company holds it
    unknown = await directory.receiving_company(
        SYSTEM, tax_id=RNC, channel="do-other-v1", environment="testecf"
    )
    assert unknown is None


async def test_a_company_of_another_organization_is_found_by_the_platform(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, directory: FiscalDirectory
) -> None:
    other = TestPrincipal(local_issuer, organization="1002", roles=("ledger.organization.admin",))
    body = {
        "jurisdiction": {"country": "DO"},
        "functional_currency": "DOP",
        "tax_ids": [{"scheme": "do.rnc", "value": OTHER_RNC.value}],
        "legal_name": "Otra SRL",
    }
    theirs = await created(client, "/v1/companies", body, other.posting("c-2"))
    await enroll(client, other, theirs, "certecf")
    found = await receiving(directory, "certecf", OTHER_RNC)
    assert found is not None
    assert (found.company, found.legal_name) == (CompanyId(parse_id(COMPANY_PREFIX, theirs)), "Otra SRL")


async def test_a_disabled_enrollment_or_an_inactive_company_names_no_company(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, directory: FiscalDirectory
) -> None:
    enrollment = await enroll(client, owner, company, "testecf")
    headers = owner.posting("d-1") | {"Ledger-Company": company}
    await client.post(f"{PATH}/{enrollment}/disable", json={"reason": "Off"}, headers=headers)
    assert await receiving(directory, "testecf") is None
    await enroll(client, owner, company, "certecf")
    assert await receiving(directory, "certecf") is not None
    off = await client.post(
        f"/v1/companies/{company}/deactivate", headers=owner.posting("k-off") | {"If-Match": "*"}
    )
    assert off.status_code == 200, document(off)
    assert await receiving(directory, "certecf") is None


async def test_a_company_is_named_in_the_call_s_mode_and_its_permissions_are_identity_s(
    owner: TestPrincipal, company: str, directory: FiscalDirectory
) -> None:
    del owner
    probe = await directory.permissions(
        Principal(
            subject=SubjectId("2001"),
            organization=OrganizationId(new_id()),
            provider_organization="1001",
            kind=PrincipalKind.HUMAN,
        ),
        CompanyId(parse_id(COMPANY_PREFIX, company)),
    )
    assert probe == frozenset()  # another organization: no role there
    identifier = CompanyId(parse_id(COMPANY_PREFIX, company))
    unknown = SystemCall(reason="test", scope=OrganizationScope(OrganizationId(new_id()), None))
    with pytest.raises(InvalidValueError, match=r"call\.scope"):
        await directory.company(unknown, identifier)
    stranger = SystemCall(reason="test", scope=Tenant(OrganizationId(new_id()), identifier, Mode.LIVE))
    with pytest.raises(CompanyNotFoundError):
        await directory.company(stranger, identifier)


async def test_a_company_of_the_call_s_organization_is_named_with_its_primary_tax_id(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, directory: FiscalDirectory
) -> None:
    await enroll(client, owner, company, "testecf")
    found = await receiving(directory, "testecf")
    assert found is not None
    call = SystemCall(reason="test", scope=Tenant(found.organization, found.company, Mode.LIVE))
    named = await directory.company(call, found.company)
    assert (named.mode, named.legal_name, named.tax_id) == (Mode.LIVE, "Acme SRL", RNC)
    principal = Principal(
        subject=SubjectId("2001"),
        organization=found.organization,
        provider_organization="1001",
        kind=PrincipalKind.HUMAN,
    )
    allowed = await directory.permissions(principal, found.company)
    assert parse_permission("fiscal.enrollments.manage") in allowed
