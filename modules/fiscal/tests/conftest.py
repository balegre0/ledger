"""fiscal's tests: the worker's database migrated as production migrates it, fiscal served by ``api``.

fiscal is the module under test of ``ledger_testing``'s module fixtures; the template holds the platform's
schema, identity's (the module its manifest depends on: companies and roles are identity's) and fiscal's, each
brought to head by its revisions. What is fiscal's own is here: who its use cases act as, its principals, a
company, and the channel its enrollments are on.

No real channel is assembled here (the Dominican one assembles in Task 17): the ``api`` app is served with one
channel, ``FakeChannel`` (key ``do-fake-v1``, of the installed ``do`` regime, environments ``testecf``,
``certecf`` and the live ``ecf``), whatever channels are installed. A test sets what it requires (``due``)
and reads the questions it was asked (``asked``). Its schema is never touched: the fake opens no unit, and
nothing in these tests asks the app's readiness.
"""

from datetime import date
from functools import partial
from typing import ClassVar, Final, final, override
lazy from collections.abc import Iterator, Sequence
lazy from datetime import timedelta

import pytest
lazy import httpx

from ledger_fiscal._manifest import MANIFEST
from ledger_fiscal.adapters.postgres.schema import MIGRATIONS
from ledger_fiscal_kit.channels import Channel, ChannelDef, Requirements
from ledger_kernel.errors import LedgerError
from ledger_kernel.regimes import Locale, RegimeKey
from ledger_platform.config.manifest import ChannelEntry
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.data.migrations import SchemaMigrations
from ledger_platform.runtime.app import App
from ledger_platform.runtime.contexts import ChannelPack, ChannelParts
from ledger_platform.runtime.registries import CHANNELS, Discovered
from ledger_testing.api import TestPrincipal, created
from ledger_testing.modules import ModuleUnderTest
lazy from ledger_fiscal_kit.channels import (
    EnrollmentState,
    Issued,
    Representation,
    ResumeReason,
    StatusUpdate,
)
lazy from ledger_fiscal_kit.drafts import DocumentRef, FiscalDraft
lazy from ledger_fiscal_kit.objections import Objection
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_platform.runtime.contexts import ChannelContext
lazy from ledger_platform.runtime.registries import Discover
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.postgres import Template

UNDER_TEST: Final = ModuleUnderTest(MANIFEST, MIGRATIONS)
TEST_ACTOR: Final = Actor("system:test", "fiscal-tests")
"""Who the use cases a test calls itself act as; a request through the app acts as its caller instead."""
ORGANIZATION_ADMIN: Final = "ledger.organization.admin"
"""The identity provider's project role that registers companies (identity's)."""
RNC: Final = "101850043"
COMPANY: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": RNC}],
    "legal_name": "Acme SRL",
}
"""What the ``company`` fixture registers through identity's API."""
FAKE: Final = "do-fake-v1"
ENTRY: Final = ChannelEntry.model_validate(
    {
        "key": FAKE,
        "entry_point": "ledger_country_do.channels.fake:PACK",
        "schema": "do_fake",
        "valid_from": date(2026, 1, 1),
        "environments": ["testecf", "certecf", "ecf"],
        "live_environments": ["ecf"],
        "document_kinds": ["invoice", "credit_note", "debit_note"],
        "directions": ["sales"],
        "tier": "bronze",
    }
)
DEFINITION: Final = ChannelDef.of(
    ENTRY, regime=RegimeKey("do"), fields=(), labels={Locale("en"): "Fake"}, sources=()
)


class UnusedChannelMethodError(LedgerError):
    """Enrollments ask a channel only what it requires: anything else is a defect of the code under test."""

    code: ClassVar[str] = "fiscal_tests_channel_unused"

    def __init__(self) -> None:
        super().__init__("enrollments ask a channel what it requires, and nothing else")


@final
class FakeChannel(Channel):
    """A channel that requires what its test sets, and remembers what it was asked (see the module)."""

    __slots__ = ("asked", "due")

    def __init__(self) -> None:
        self.due = Requirements(currently_due=("do_ecf.range",))
        """What it answers it requires, until a test changes it."""
        self.asked: list[tuple[SystemCall, EnrollmentState]] = []
        """Every question of its requirements, in order."""

    @property
    @override
    def definition(self) -> ChannelDef:
        return DEFINITION

    @override
    async def requirements(self, call: SystemCall, state: EnrollmentState) -> Requirements:
        self.asked.append((call, state))
        return self.due

    @override
    async def check(self, call: SystemCall, draft: FiscalDraft) -> Sequence[Objection]:
        raise UnusedChannelMethodError

    @override
    async def issue(self, call: SystemCall, draft: FiscalDraft, *, sign_within: timedelta | None) -> Issued:
        raise UnusedChannelMethodError

    @override
    async def status(self, call: SystemCall, document: DocumentRef) -> StatusUpdate | None:
        raise UnusedChannelMethodError

    @override
    async def resume(self, call: SystemCall, company: CompanyId, reason: ResumeReason) -> None:
        raise UnusedChannelMethodError

    @override
    async def representation(self, call: SystemCall, document: DocumentRef) -> Representation:
        raise UnusedChannelMethodError


def _same[T](value: T) -> T:
    return value


def with_channel(discover: Discover, channel: FakeChannel) -> Discover:
    """``discover``, its channels replaced by ``channel`` alone."""

    def assemble(context: ChannelContext) -> ChannelParts:
        unused = SchemaMigrations(schema=context.database.schema, scripts="fiscal_tests_unused:migrations")
        return ChannelParts(channel=channel, migrations=unused)

    pack = ChannelPack(entry=ENTRY, regime="do", assemble=assemble)
    only: Final = (Discovered(FAKE, partial(_same, pack)),)

    def found(group: str) -> Sequence[Discovered]:
        return only if group == CHANNELS else discover(group)

    return found


def pytest_ledger_template(template: Template) -> None:
    """The roles and the schemas ``platform``, ``identity`` and ``fiscal``, each at head, relay granted."""
    UNDER_TEST.prepare(template)


@pytest.fixture
def module_under_test() -> ModuleUnderTest:
    """fiscal, for the module fixtures of ``ledger_testing``."""
    return UNDER_TEST


@pytest.fixture(autouse=True)
def acting() -> Iterator[None]:
    """Every test acts as ``TEST_ACTOR``: what its own calls of the use cases audit needs an actor."""
    with acting_as(TEST_ACTOR):
        yield


@pytest.fixture
def fake_channel() -> FakeChannel:
    """The one channel the app serves: it requires a numbering range until the test says otherwise."""
    return FakeChannel()


@pytest.fixture
def api(settings: Settings, module_under_test: ModuleUnderTest, fake_channel: FakeChannel) -> App:
    """The ``api`` app: fiscal and identity mounted, and ``fake_channel`` as the only channel."""
    return App("api", settings=settings, discover=with_channel(module_under_test.discovering(), fake_channel))


@pytest.fixture
def owner(local_issuer: LocalIssuer) -> TestPrincipal:
    """Subject ``2001`` of the provider's organization ``1001``, its administrator: it owns its companies."""
    return TestPrincipal(local_issuer, roles=(ORGANIZATION_ADMIN,))


@pytest.fixture
async def company(client: httpx.AsyncClient, owner: TestPrincipal) -> str:
    """The public id of a company ``owner`` registered through identity's API (``COMPANY``)."""
    return await created(client, "/v1/companies", COMPANY, owner.posting("company-1"))
