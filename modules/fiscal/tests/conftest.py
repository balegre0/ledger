"""fiscal's tests: the worker's database migrated as production migrates it, fiscal served by ``api``.

fiscal is the module under test of ``ledger_testing``'s module fixtures; the template holds the platform's
schema, identity's (the module its manifest depends on: companies and roles are identity's) and fiscal's, each
brought to head by its revisions. What is fiscal's own is here: who its use cases act as, its principals, a
company, and the channel its enrollments are on.

No real channel is assembled here (a module's suite assembles none; the Dominican one is tested with fiscal in
its country's suite): the ``api`` app is served with one channel, ``FakeChannel`` (key ``do-fake-v1``, of the
installed ``do`` regime, environments ``testecf``, ``certecf`` and the live ``ecf``), whatever channels are
installed. A test sets what it requires (``due``), what it is (``defined``), how asking it fails
(``failure``), what it objects to and how it issues and renders, and reads what it was asked. Its schema is
never touched: the fake opens no unit, and nothing here asks the app's readiness. The template holds
invoicing's schema too: fiscal depends on it (its ``IssuePolicy`` and ``Renderer``).
"""

from datetime import UTC, date, datetime
from functools import partial
from typing import ClassVar, Final, final, override
lazy from collections.abc import Awaitable, Callable, Iterator, Sequence
lazy from datetime import timedelta

import pytest
lazy import httpx

from ledger_fiscal._manifest import MANIFEST
from ledger_fiscal.adapters.postgres.schema import MIGRATIONS
from ledger_fiscal_kit.channels import Channel, ChannelDef, Requirements
from ledger_fiscal_kit.objections import NumberUnavailableError
from ledger_fiscal_kit.status import FiscalStatus, Severity
from ledger_kernel.errors import LedgerError, UnavailableError
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
lazy from ledger_platform.data.codec import JsonValue
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
    """fiscal asks a channel what it requires, checks, issues and renders: nothing else of it."""

    code: ClassVar[str] = "fiscal_tests_channel_unused"

    def __init__(self) -> None:
        super().__init__("fiscal never asks a channel this")


class NoNumberError(NumberUnavailableError):
    """The fake channel's own "no number now" (a channel raises a subclass of the kit's)."""

    code: ClassVar[str] = "fiscal_tests_no_number"

    def __init__(self) -> None:
        super().__init__("the fake channel has no number to give now")


class NotPrintableError(UnavailableError):
    """The fake channel cannot render a representation now."""

    code: ClassVar[str] = "fiscal_tests_not_printable"

    def __init__(self) -> None:
        super().__init__("the fake channel renders nothing yet")


type BeforeIssue = Callable[[FiscalDraft], Awaitable[None]]
"""What a test runs when the channel is asked to issue a draft, before it numbers anything."""


@final
class FakeChannel(Channel):
    """A channel as fiscal uses it, in memory (see the module).

    It requires what its test sets (``due``); objects ``objections`` to every draft (``checked``); numbers a
    draft once per document (``E31`` and a counter, ``numbered``), answering the document it numbered to a
    second call; signs it at once when asked within a budget (version 2, a security code) unless ``slow``;
    raises ``NoNumberError`` while ``no_number`` (keeping nothing) and runs ``before_issue`` first; renders
    ``printed`` when set, else is unavailable. ``issued`` holds every ``issue`` call.
    """

    __slots__ = (
        "asked",
        "before_issue",
        "checked",
        "defined",
        "documents",
        "due",
        "failure",
        "issued",
        "no_number",
        "numbered",
        "objections",
        "printed",
        "slow",
    )

    def __init__(self) -> None:
        self.due = Requirements(currently_due=("do_ecf.range",))
        """What it answers it requires, until a test changes it."""
        self.asked: list[tuple[SystemCall, EnrollmentState]] = []
        """Every question of its requirements, in order (failed ones too)."""
        self.defined = DEFINITION
        """What it is, until a test changes it (its dates, say)."""
        self.failure: LedgerError | None = None
        """What asking its requirements raises, when a test sets it."""
        self.objections: list[Objection] = []
        self.checked: list[tuple[SystemCall, FiscalDraft]] = []
        self.issued: list[tuple[SystemCall, FiscalDraft, timedelta | None]] = []
        self.documents: dict[DocumentRef, StatusUpdate] = {}
        self.numbered = 0
        self.slow = False
        self.no_number = False
        self.before_issue: BeforeIssue | None = None
        self.printed: Representation | None = None

    @property
    @override
    def definition(self) -> ChannelDef:
        return self.defined

    @override
    async def requirements(self, call: SystemCall, state: EnrollmentState) -> Requirements:
        self.asked.append((call, state))
        if self.failure is not None:
            raise self.failure
        return self.due

    @override
    async def check(self, call: SystemCall, draft: FiscalDraft) -> Sequence[Objection]:
        self.checked.append((call, draft))
        return tuple(self.objections)

    def update(self, draft: FiscalDraft, *, version: int, signed: bool) -> StatusUpdate:
        """The status of ``draft``'s document at ``version``: numbered, or signed with a security code."""
        number = f"E31{self.numbered:010d}"
        details: dict[str, JsonValue] = {"encf": number, "security_code": "Ab12Cd" if signed else None}
        return StatusUpdate(
            document=draft.document,
            channel=FAKE,
            environment=draft.environment,
            state="signed" if signed else "numbered",
            status=FiscalStatus.PENDING,
            severity=Severity.INFO,
            version=version,
            authority_number=number,
            messages=(),
            details={"do": details},
            occurred_at=datetime.now(UTC),
        )

    @override
    async def issue(self, call: SystemCall, draft: FiscalDraft, *, sign_within: timedelta | None) -> Issued:
        self.issued.append((call, draft, sign_within))
        if self.before_issue is not None:
            await self.before_issue(draft)
        found = self.documents.get(draft.document)
        if found is not None:
            return Issued(status=found, signed=found.version > 1)
        if self.no_number:
            raise NoNumberError
        self.numbered += 1
        signs = sign_within is not None and not self.slow
        kept = self.update(draft, version=2 if signs else 1, signed=signs)
        self.documents[draft.document] = kept
        return Issued(status=kept, signed=signs)

    @override
    async def status(self, call: SystemCall, document: DocumentRef) -> StatusUpdate | None:
        raise UnusedChannelMethodError

    @override
    async def resume(self, call: SystemCall, company: CompanyId, reason: ResumeReason) -> None:
        raise UnusedChannelMethodError

    @override
    async def representation(self, call: SystemCall, document: DocumentRef) -> Representation:
        if self.printed is None:
            raise NotPrintableError
        return self.printed


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
