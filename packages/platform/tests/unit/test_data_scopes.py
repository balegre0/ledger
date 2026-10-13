import uuid

import pytest

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.data.scopes import scope_of

ORGANIZATION, COMPANY = uuid.uuid7(), uuid.uuid7()


@pytest.mark.parametrize(
    ("company", "mode", "expected"),
    [
        (COMPANY, "live", Tenant(OrganizationId(ORGANIZATION), CompanyId(COMPANY), Mode.LIVE)),
        (COMPANY, "test", Tenant(OrganizationId(ORGANIZATION), CompanyId(COMPANY), Mode.TEST)),
        (COMPANY, None, Tenant(OrganizationId(ORGANIZATION), CompanyId(COMPANY), None)),
        (None, None, OrganizationScope(OrganizationId(ORGANIZATION), None)),
        (None, "live", OrganizationScope(OrganizationId(ORGANIZATION), Mode.LIVE)),
    ],
)
def test_a_rows_envelope_columns_name_its_scope(
    company: uuid.UUID | None, mode: str | None, expected: Tenant | OrganizationScope
) -> None:
    assert scope_of(ORGANIZATION, company, mode) == expected


@pytest.mark.parametrize("mode", ["test", None])
def test_a_row_without_an_organization_has_no_scope(mode: str | None) -> None:
    assert scope_of(None, COMPANY, mode) is None
    assert scope_of(None, None, mode) is None


@pytest.mark.parametrize("organization", [ORGANIZATION, None])
def test_a_mode_that_is_neither_test_nor_live_is_refused(organization: uuid.UUID | None) -> None:
    with pytest.raises(InvalidValueError, match=r"row\.mode"):
        scope_of(organization, COMPANY, "prod")
