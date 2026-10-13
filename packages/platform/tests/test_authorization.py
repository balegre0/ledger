import asyncio
from typing import Final, final, override

import pytest

from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.authorization import (
    CachedPermissions,
    CompanyAccess,
    InvalidAuthorizationOptionError,
    NoCompanyAccessError,
    PermissionDeniedError,
    PermissionSource,
)
from ledger_testing.clock import Moment

ORGANIZATION: Final = OrganizationId(new_id())
OTHER_ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
OTHER_COMPANY: Final = CompanyId(new_id())
ALICE: Final = Principal(subject=SubjectId("alice"), organization=ORGANIZATION, provider_organization="1001")
BOB: Final = Principal(subject=SubjectId("bob"), organization=ORGANIZATION, provider_organization="1001")
ALICE_ELSEWHERE: Final = Principal(
    subject=SubjectId("alice"), organization=OTHER_ORGANIZATION, provider_organization="1002"
)
"""A namesake: the same subject id, in another organization."""
READ: Final = frozenset({Permission.COMPANY_READ})
MANAGE: Final = frozenset({Permission.COMPANY_READ, Permission.COMPANY_MANAGE})


@final
class Counting(PermissionSource):
    """Answers from a table and counts the questions; can be held mid-answer."""

    def __init__(self) -> None:
        self.granted: dict[tuple[OrganizationId, SubjectId, CompanyId], frozenset[Permission]] = {}
        self.asked = 0
        self.hold: asyncio.Event | None = None
        self.answering = asyncio.Event()

    def grant(self, principal: Principal, company: CompanyId, permissions: frozenset[Permission]) -> None:
        self.granted[principal.organization, principal.subject, company] = permissions

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        self.asked += 1
        answer = self.granted.get((principal.organization, principal.subject, company), frozenset())
        if self.hold is not None:
            self.answering.set()
            await self.hold.wait()
        return answer


def test_access_allows_what_it_holds_and_refuses_the_rest() -> None:
    access = CompanyAccess(ALICE, Tenant(ORGANIZATION, COMPANY), READ)
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


async def test_an_answer_is_kept_for_its_ttl() -> None:
    source, moment = Counting(), Moment()
    source.grant(ALICE, COMPANY, READ)
    cache = CachedPermissions(source, ttl=30.0, clock=moment)
    assert await cache.permissions(ALICE, COMPANY) == READ
    moment.now = 29.9
    assert await cache.permissions(ALICE, COMPANY) == READ
    assert source.asked == 1
    source.grant(ALICE, COMPANY, MANAGE)
    moment.now = 30.0
    assert await cache.permissions(ALICE, COMPANY) == MANAGE
    assert source.asked == 2


async def test_an_answer_is_kept_for_its_ttl_from_when_it_was_asked_for() -> None:
    source, moment = Counting(), Moment()
    source.grant(ALICE, COMPANY, READ)
    source.hold = asyncio.Event()
    cache = CachedPermissions(source, ttl=30.0, clock=moment)
    slow = asyncio.create_task(cache.permissions(ALICE, COMPANY))
    await source.answering.wait()
    moment.now = 20.0  # the source took twenty seconds to answer
    source.hold.set()
    assert await slow == READ
    source.hold = None
    moment.now = 30.0  # thirty seconds after the question, ten after the answer
    await cache.permissions(ALICE, COMPANY)
    assert source.asked == 2


async def test_answers_are_kept_per_principal_and_company() -> None:
    source = Counting()
    source.grant(ALICE, COMPANY, READ)
    cache = CachedPermissions(source)
    assert await cache.permissions(ALICE, COMPANY) == READ
    assert await cache.permissions(BOB, COMPANY) == frozenset()
    assert await cache.permissions(ALICE, OTHER_COMPANY) == frozenset()
    assert source.asked == 3


async def test_an_answer_is_never_handed_to_a_namesake_of_another_organization() -> None:
    source = Counting()
    source.grant(ALICE, COMPANY, MANAGE)
    cache = CachedPermissions(source)
    assert await cache.permissions(ALICE, COMPANY) == MANAGE
    assert await cache.permissions(ALICE_ELSEWHERE, COMPANY) == frozenset()
    assert source.asked == 2


async def test_forgetting_drops_the_answer_at_once() -> None:
    source = Counting()
    source.grant(ALICE, COMPANY, MANAGE)
    cache = CachedPermissions(source)
    await cache.permissions(ALICE, COMPANY)
    source.grant(ALICE, COMPANY, READ)
    cache.forget(ORGANIZATION, COMPANY, SubjectId("alice"))
    assert await cache.permissions(ALICE, COMPANY) == READ


async def test_an_answer_fetched_before_a_change_is_not_kept_after_it() -> None:
    source = Counting()
    source.grant(ALICE, COMPANY, MANAGE)
    source.hold = asyncio.Event()
    cache = CachedPermissions(source)
    stale = asyncio.create_task(cache.permissions(ALICE, COMPANY))
    await source.answering.wait()
    cache.forget(ORGANIZATION, COMPANY, SubjectId("alice"))  # the membership changed meanwhile
    source.hold.set()
    assert await stale == MANAGE  # the caller still gets the answer it asked for
    source.hold = None
    source.grant(ALICE, COMPANY, READ)
    assert await cache.permissions(ALICE, COMPANY) == READ  # but it was not kept
    assert source.asked == 2


async def test_forgetting_a_company_drops_every_answer_about_it_and_keeps_the_rest() -> None:
    """A deactivated company: every principal's answer about it goes, with a role or without."""
    source = Counting()
    for principal, company in ((ALICE, COMPANY), (ALICE, OTHER_COMPANY), (ALICE_ELSEWHERE, COMPANY)):
        source.grant(principal, company, MANAGE)
    cache = CachedPermissions(source)
    for principal, company in (
        (ALICE, COMPANY),
        (BOB, COMPANY),  # no role: kept apart
        (ALICE, OTHER_COMPANY),
        (ALICE_ELSEWHERE, COMPANY),  # the same company id under another organization
    ):
        await cache.permissions(principal, company)
    source.grant(ALICE, COMPANY, READ)
    source.grant(BOB, COMPANY, READ)
    cache.forget_company(ORGANIZATION, COMPANY)
    assert (await cache.permissions(ALICE, COMPANY), await cache.permissions(BOB, COMPANY)) == (READ, READ)
    assert source.asked == 6
    await cache.permissions(ALICE, OTHER_COMPANY)
    await cache.permissions(ALICE_ELSEWHERE, COMPANY)
    assert source.asked == 6


async def test_an_answer_fetched_before_its_company_was_forgotten_is_not_kept_after_it() -> None:
    source = Counting()
    source.grant(ALICE, COMPANY, MANAGE)
    source.hold = asyncio.Event()
    cache = CachedPermissions(source)
    stale = asyncio.create_task(cache.permissions(ALICE, COMPANY))
    await source.answering.wait()
    cache.forget_company(ORGANIZATION, COMPANY)  # the company was deactivated meanwhile
    source.hold.set()
    assert await stale == MANAGE  # the caller still gets the answer it asked for
    source.hold = None
    source.grant(ALICE, COMPANY, READ)
    assert await cache.permissions(ALICE, COMPANY) == READ  # but it was not kept
    assert source.asked == 2


async def test_forgetting_everything_drops_every_answer_with_a_role_or_without() -> None:
    source = Counting()
    source.grant(ALICE, COMPANY, MANAGE)
    cache = CachedPermissions(source)
    await cache.permissions(ALICE, COMPANY)
    await cache.permissions(BOB, COMPANY)  # no role: kept apart
    source.grant(ALICE, COMPANY, READ)
    source.grant(BOB, COMPANY, READ)
    cache.forget_all()
    assert (await cache.permissions(ALICE, COMPANY), await cache.permissions(BOB, COMPANY)) == (READ, READ)
    assert source.asked == 4


async def test_an_answer_fetched_before_everything_was_forgotten_is_not_kept_after_it() -> None:
    source = Counting()
    source.grant(ALICE, COMPANY, MANAGE)
    source.hold = asyncio.Event()
    cache = CachedPermissions(source)
    stale = asyncio.create_task(cache.permissions(ALICE, COMPANY))
    await source.answering.wait()
    cache.forget_all()  # the connection to NATS was lost meanwhile
    source.hold.set()
    assert await stale == MANAGE
    source.hold = None
    source.grant(ALICE, COMPANY, READ)
    assert await cache.permissions(ALICE, COMPANY) == READ
    assert source.asked == 2


async def test_the_least_recently_used_answers_go_beyond_the_capacity() -> None:
    source = Counting()
    for principal, company in ((ALICE, COMPANY), (BOB, COMPANY), (ALICE, OTHER_COMPANY)):
        source.grant(principal, company, READ)
    cache = CachedPermissions(source, capacity=2)
    await cache.permissions(ALICE, COMPANY)
    await cache.permissions(BOB, COMPANY)
    await cache.permissions(ALICE, COMPANY)  # used again: Bob's is now the oldest
    await cache.permissions(ALICE, OTHER_COMPANY)  # one too many: Bob's goes
    assert source.asked == 3
    await cache.permissions(ALICE, COMPANY)
    assert source.asked == 3
    await cache.permissions(BOB, COMPANY)
    assert source.asked == 4


async def test_companies_without_a_role_never_push_out_the_answers_of_others() -> None:
    source = Counting()
    source.grant(ALICE, COMPANY, READ)
    cache = CachedPermissions(source, capacity=1, empty_capacity=2)
    await cache.permissions(ALICE, COMPANY)
    for _ in range(10):  # random company ids: no role in any of them
        await cache.permissions(BOB, CompanyId(new_id()))
    assert await cache.permissions(ALICE, COMPANY) == READ
    assert source.asked == 11


async def test_an_answer_without_a_role_is_kept_within_its_own_bound() -> None:
    source = Counting()
    cache = CachedPermissions(source, empty_capacity=2)
    await cache.permissions(ALICE, COMPANY)
    await cache.permissions(BOB, COMPANY)
    await cache.permissions(ALICE, COMPANY)  # kept, and used again: Bob's is now the oldest
    assert source.asked == 2
    await cache.permissions(ALICE, OTHER_COMPANY)  # one too many: Bob's goes
    await cache.permissions(BOB, COMPANY)
    assert source.asked == 4


async def test_an_answer_moves_between_the_bounds_as_the_role_comes_and_goes() -> None:
    source, moment = Counting(), Moment()
    cache = CachedPermissions(source, ttl=30.0, clock=moment)
    assert await cache.permissions(ALICE, COMPANY) == frozenset()
    source.grant(ALICE, COMPANY, READ)
    cache.forget(ORGANIZATION, COMPANY, SubjectId("alice"))
    assert await cache.permissions(ALICE, COMPANY) == READ
    source.grant(ALICE, COMPANY, frozenset())
    moment.now = 30.0
    assert await cache.permissions(ALICE, COMPANY) == frozenset()
    source.grant(ALICE, COMPANY, MANAGE)
    moment.now = 60.0
    assert await cache.permissions(ALICE, COMPANY) == MANAGE  # each answer moved between the two bounds
    assert await cache.permissions(ALICE, COMPANY) == MANAGE
    assert source.asked == 4


@pytest.mark.parametrize(
    ("ttl", "capacity", "empty_capacity", "option"),
    [
        (0.0, 10, 10, "ttl"),
        (float("inf"), 10, 10, "ttl"),
        (float("nan"), 10, 10, "ttl"),
        (30.0, 0, 10, "capacity"),
        (30.0, 10, 0, "empty_capacity"),
    ],
)
def test_a_cache_that_cannot_work_is_refused(
    ttl: float, capacity: int, empty_capacity: int, option: str
) -> None:
    with pytest.raises(InvalidAuthorizationOptionError, match=f"authorization option {option} must be"):
        CachedPermissions(Counting(), ttl=ttl, capacity=capacity, empty_capacity=empty_capacity)
