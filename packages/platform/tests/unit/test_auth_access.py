from typing import Final

import pytest

from ledger_kernel.access import (
    CompanyNotFoundError,
    Permission,
    PermissionDeniedError,
    Principal,
    SubjectId,
    parse_permission,
)
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Tenant
from ledger_platform import auth
from ledger_platform.auth.access import CompanyAccess

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
ALICE: Final = Principal(subject=SubjectId("alice"), organization=ORGANIZATION, provider_organization="1001")
COMPANIES_READ: Final = parse_permission("identity.companies.read")
COMPANIES_MANAGE: Final = parse_permission("identity.companies.manage")
READ: Final = frozenset({COMPANIES_READ})


def test_access_allows_what_it_holds_and_refuses_the_rest() -> None:
    access = CompanyAccess(ALICE, Tenant(ORGANIZATION, COMPANY, None), READ)
    assert access.allows(COMPANIES_READ)
    assert not access.allows(COMPANIES_MANAGE)
    access.require(COMPANIES_READ)
    with pytest.raises(
        PermissionDeniedError, match=r"^this needs the permission identity\.companies\.manage$"
    ) as caught:
        access.require(COMPANIES_MANAGE)
    assert (caught.value.kind, caught.value.code, caught.value.permission) == (
        ErrorKind.FORBIDDEN,
        "permission_denied",
        COMPANIES_MANAGE,
    )


def test_the_access_errors_are_the_kernel_s_one_class_per_code() -> None:
    """``CompanyAccess.require`` raises the kernel's error, as identity's domain does: one class per code."""
    assert not hasattr(auth, "PermissionDeniedError")
    assert not hasattr(auth, "NoCompanyAccessError")
    assert {"CompanyNotFoundError", "PermissionDeniedError"}.isdisjoint(auth.__all__)
    error = CompanyNotFoundError(COMPANY)
    assert (error.kind, error.code, str(error)) == (
        ErrorKind.NOT_FOUND,
        "company_not_found",
        f"company {COMPANY} was not found",
    )
    assert Permission("x.y.read") == "x.y.read"
