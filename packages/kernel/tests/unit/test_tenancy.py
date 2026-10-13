from uuid import uuid7

from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
lazy from ledger_kernel.tenancy import Scope

ORGANIZATION = OrganizationId(uuid7())
COMPANY = CompanyId(uuid7())


def parts(scope: Scope) -> tuple[OrganizationId, CompanyId | None, Mode | None]:
    return scope.organization, scope.company, scope.mode


def test_a_tenant_names_its_organization_company_and_mode() -> None:
    assert parts(Tenant(ORGANIZATION, COMPANY, Mode.LIVE)) == (ORGANIZATION, COMPANY, Mode.LIVE)


def test_a_tenant_without_a_mode_reaches_only_what_both_modes_share() -> None:
    assert parts(Tenant(ORGANIZATION, COMPANY, None)) == (ORGANIZATION, COMPANY, None)


def test_an_organization_scope_has_no_company() -> None:
    assert parts(OrganizationScope(ORGANIZATION, Mode.TEST)) == (ORGANIZATION, None, Mode.TEST)


def test_modes_are_the_words_rows_store() -> None:
    assert [mode.value for mode in Mode] == ["test", "live"]
