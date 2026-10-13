from typing import Final

import pytest

from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.auth.access import CompanyAccess, NoCompanyAccessError, PermissionDeniedError

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
ALICE: Final = Principal(subject=SubjectId("alice"), organization=ORGANIZATION, provider_organization="1001")
READ: Final = frozenset({Permission.COMPANY_READ})


def test_access_allows_what_it_holds_and_refuses_the_rest() -> None:
    access = CompanyAccess(ALICE, Tenant(ORGANIZATION, COMPANY, None), READ)
    assert access.allows(Permission.COMPANY_READ)
    assert not access.allows(Permission.COMPANY_MANAGE)
    access.require(Permission.COMPANY_READ)
    with pytest.raises(PermissionDeniedError, match=r"needs the permission company\.manage") as caught:
        access.require(Permission.COMPANY_MANAGE)
    assert (caught.value.kind, caught.value.code, caught.value.permission) == (
        ErrorKind.FORBIDDEN,
        "permission_denied",
        Permission.COMPANY_MANAGE,
    )


def test_no_access_at_all_is_a_company_not_found() -> None:
    error = NoCompanyAccessError(COMPANY)
    assert (error.kind, error.code, str(error)) == (
        ErrorKind.NOT_FOUND,
        "company_not_found",
        f"company {COMPANY} was not found",
    )
