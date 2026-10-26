"""What invoicing asks outside itself: identity through its API, and the regime that governs a company."""

from dataclasses import replace
from typing import Final, final, override

import pytest

from ledger_identity.api import CompanyInfo, EstablishmentInfo, IdentityApi
from ledger_invoicing.adapters.identity import IdentityApiLookup
from ledger_invoicing.adapters.regimes import regime_of
from ledger_invoicing.domain.pricing import EstablishmentRef
from ledger_kernel.access import CompanyNotFoundError, Principal, PrincipalKind, SubjectId
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.jurisdiction import parse_jurisdiction
from ledger_kernel.money import Currency
from ledger_kernel.regimes import RegimeKey
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.runtime.calls import Call
from ledger_platform.runtime.registries import Regimes
lazy from ledger_kernel.access import Permission
lazy from ledger_kernel.regimes import RegimeDef
lazy from ledger_platform.runtime.calls import AnyCall

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
PRINCIPAL: Final = Principal(
    subject=SubjectId("2001"),
    organization=ORGANIZATION,
    provider_organization="1001",
    kind=PrincipalKind.HUMAN,
)
CALL: Final = Call(principal=PRINCIPAL, scope=Tenant(ORGANIZATION, COMPANY, Mode.TEST), request_id="r-1")
ACME: Final = CompanyInfo(
    id=COMPANY,
    organization=ORGANIZATION,
    jurisdiction=parse_jurisdiction("DO"),
    functional_currency=Currency.of("DOP"),
    tax_ids=(TaxId(TaxIdScheme("do.rnc"), "101850043"),),
    legal_name="Acme SRL",
    address=None,
    active=True,
    fiscal_profile_locked=False,
    version=1,
)
BRANCH: Final = EstablishmentInfo(code="SUC1", name="Santiago", time_zone="America/Santo_Domingo")


@final
class Identity(IdentityApi):
    """identity's API as a test answers it: one company, one establishment; what it was asked, in order."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, AnyCall, CompanyId, str | None]] = []

    @override
    async def company(self, call: AnyCall, company: CompanyId) -> CompanyInfo:
        self.asked.append(("company", call, company, None))
        if company != COMPANY:
            raise CompanyNotFoundError(company)
        return ACME

    @override
    async def company_by_tax_id(self, call: AnyCall, tax_id: TaxId) -> CompanyInfo | None:
        raise NotImplementedError

    @override
    async def permissions(
        self, call: AnyCall, principal: Principal, company: CompanyId
    ) -> frozenset[Permission]:
        raise NotImplementedError

    @override
    async def establishment(
        self, call: AnyCall, company: CompanyId, code: str | None
    ) -> EstablishmentInfo | None:
        self.asked.append(("establishment", call, company, code))
        return BRANCH if code == "SUC1" else None

    @override
    async def lock_fiscal_profile(self, call: object, company: CompanyId) -> CompanyInfo:
        raise NotImplementedError


async def test_identity_is_asked_for_the_company_and_its_establishment_with_the_call() -> None:
    identity = Identity()
    lookup = IdentityApiLookup(identity)
    assert await lookup.company(CALL, COMPANY) == ACME
    assert await lookup.establishment(CALL, COMPANY, "SUC1") == EstablishmentRef(
        code="SUC1", time_zone="America/Santo_Domingo"
    )
    assert await lookup.establishment(CALL, COMPANY, None) is None
    assert identity.asked == [
        ("company", CALL, COMPANY, None),
        ("establishment", CALL, COMPANY, "SUC1"),
        ("establishment", CALL, COMPANY, None),
    ]
    with pytest.raises(CompanyNotFoundError):
        await lookup.company(CALL, CompanyId(new_id()))


def test_a_company_is_governed_by_its_subdivision_s_regime_else_its_country_s(regime: RegimeDef) -> None:
    local = replace(
        regime, key=RegimeKey("do-01"), jurisdiction=parse_jurisdiction("DO", "DO-01"), tax_categories=()
    )
    governing = regime_of(Regimes([regime, local]))
    assert governing(parse_jurisdiction("DO")) is regime
    assert governing(parse_jurisdiction("DO", "DO-02")) is regime
    assert governing(parse_jurisdiction("DO", "DO-01")) is local
    assert governing(parse_jurisdiction("US")) is None
