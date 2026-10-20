"""invoicing's extension points (ADR 0011): its ``IssuePolicy`` and ``Renderer`` chains, as it runs them.

Before a document is issued, every policy checks it (no unit open) and their objections are concatenated in
chain order: any refuses it (422 ``invalid_document``, ``errors[]``). Once it is issued, each policy's
``after_issue`` runs in chain order within what is left of the issue budget (§12: 1.5 s for all of them); a
policy that runs out of time or fails is logged by the type of its error and passed over, never the request's
failure (the recovery consumer finishes, §8.5). A representation is the first renderer's that answers one.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import Final, final, override
lazy from collections.abc import Sequence

import pytest

from ledger_invoicing.api import (
    ISSUE_BUDGET,
    ISSUE_POLICY,
    RENDERER,
    EstablishmentOfIssue,
    InvoiceInfo,
    IssuePolicy,
    Objection,
    RenderedDocument,
    Renderer,
)
from ledger_invoicing.application.issuing import IssuePolicies, Renderers
from ledger_invoicing.application.ports import Log
from ledger_invoicing.domain.errors import DocumentInvalidError, RepresentationUnavailableError
from ledger_invoicing.domain.ids import InvoiceId
from ledger_invoicing.domain.invoice import InvoiceStatus
from ledger_kernel.access import Principal, PrincipalKind, SubjectId
from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.money import Currency, Money
from ledger_kernel.regimes import Direction, DocumentKind, RegimeKey, Totals
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.runtime.calls import Call
from ledger_platform.runtime.keys import BindingTypeError

NOW: Final = datetime(2026, 10, 20, 12, tzinfo=UTC)
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)
CALL: Final = Call(
    principal=Principal(
        subject=SubjectId("2001"),
        organization=TENANT.organization,
        provider_organization="1001",
        kind=PrincipalKind.HUMAN,
    ),
    scope=TENANT,
    request_id="req-1",
)
DOP: Final = Currency.of("DOP")
ZERO: Final = Money(Decimal(0), DOP)
DOCUMENT: Final = InvoiceInfo(
    id=InvoiceId(new_id()),
    tenant=TENANT,
    kind=DocumentKind.INVOICE,
    direction=Direction.SALES,
    status=InvoiceStatus.ISSUED,
    version=1,
    external_reference=None,
    corrects=None,
    regime=RegimeKey("do"),
    establishment=EstablishmentOfIssue(code="MAIN", time_zone="America/Santo_Domingo"),
    currency=DOP,
    exchange_rate=None,
    issue_date=date(2026, 10, 20),
    tax_point=date(2026, 10, 20),
    seller=None,
    buyer=None,
    lines=(),
    totals=Totals(net=ZERO, categories=(), tax=ZERO, gross=ZERO),
    functional_totals=None,
    fields={},
    created_at=NOW,
    updated_at=NOW,
    issued_at=NOW,
    voided_at=None,
    void_reason=None,
)


def objection(code: str, path: str | None = None) -> Objection:
    return Objection(code=code, field=path, message=f"the rule {code}")


@final
class Clock:
    """A monotonic clock a test moves: what the budget left is measured with."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@final
@dataclass
class Logged(Log):
    lines: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

    @override
    def warning(self, event: str, /, **fields: str) -> None:
        self.lines.append(("warning", event, fields))

    @override
    def exception(self, event: str, /, **fields: str) -> None:
        self.lines.append(("error", event, fields))


@final
@dataclass(kw_only=True)
class Policy(IssuePolicy):
    """A policy that objects as told, and after the issue spends time, blocks or fails as told."""

    name: str
    seen: list[tuple[str, str, timedelta | None]]
    objections: Sequence[Objection] = ()
    clock: Clock | None = None
    spends: float = 0.0
    blocks: bool = False
    fails: bool = False
    cancelled: bool = field(default=False, init=False)

    @override
    async def check(self, call: Call, document: InvoiceInfo) -> Sequence[Objection]:
        assert (call, document) == (CALL, DOCUMENT)
        self.seen.append((self.name, "check", None))
        return self.objections

    @override
    async def after_issue(self, call: Call, document: InvoiceInfo, *, budget: timedelta) -> None:
        assert (call, document) == (CALL, DOCUMENT)
        self.seen.append((self.name, "after_issue", budget))
        if self.clock is not None:
            self.clock.now += self.spends
        if self.fails:
            raise LookupError(self.name)
        if self.blocks:
            try:
                await asyncio.Event().wait()  # the signer that never answers
            except asyncio.CancelledError:
                self.cancelled = True
                raise


def chained(*policies: IssuePolicy, clock: Clock | None = None, log: Log | None = None) -> IssuePolicies:
    return IssuePolicies(
        chain=lambda: policies, log=log or Logged(), monotonic=clock or Clock(), budget=ISSUE_BUDGET
    )


async def test_objections_are_concatenated_in_chain_order_and_refuse_the_document() -> None:
    seen: list[tuple[str, str, timedelta | None]] = []
    first = Policy(name="first", seen=seen, objections=[objection("fiscal_enrollment_pending")])
    second = Policy(name="second", seen=seen, objections=[objection("do_ecf_range_missing", "type")])
    with pytest.raises(DocumentInvalidError) as caught:
        await chained(first, second).check(CALL, DOCUMENT)
    assert seen == [("first", "check", None), ("second", "check", None)]
    error = caught.value
    assert (error.kind, error.code) == (ErrorKind.INVALID, "invalid_document")
    assert str(error) == "the document breaks 2 rules of the fiscal channels it is issued through"
    assert error.problem_members() == {
        "errors": [
            {
                "code": "fiscal_enrollment_pending",
                "field": None,
                "message": "the rule fiscal_enrollment_pending",
            },
            {"code": "do_ecf_range_missing", "field": "type", "message": "the rule do_ecf_range_missing"},
        ],
        "omitted_errors": 0,
    }


async def test_a_problem_lists_twenty_objections_and_counts_the_rest() -> None:
    many = Policy(name="many", seen=[], objections=[objection(f"rule_{index}") for index in range(23)])
    with pytest.raises(DocumentInvalidError) as caught:
        await chained(many).check(CALL, DOCUMENT)
    members = caught.value.problem_members()
    errors = members["errors"]
    assert isinstance(errors, list)
    assert (len(errors), members["omitted_errors"]) == (20, 3)
    assert str(caught.value) == "the document breaks 23 rules of the fiscal channels it is issued through"
    one = Policy(name="one", seen=[], objections=[objection("rule")])
    with pytest.raises(DocumentInvalidError, match=r"^the document breaks 1 rule of the fiscal"):
        await chained(one).check(CALL, DOCUMENT)


async def test_a_chain_without_objections_or_policies_lets_the_document_be_issued() -> None:
    seen: list[tuple[str, str, timedelta | None]] = []
    await chained(Policy(name="quiet", seen=seen)).check(CALL, DOCUMENT)
    await chained().check(CALL, DOCUMENT)
    assert seen == [("quiet", "check", None)]
    assert chained(Policy(name="quiet", seen=seen)).judges_fields()
    assert not chained().judges_fields()  # Decision 24: no chain, no field beyond the regime's


async def test_after_issue_runs_each_policy_in_order_with_what_is_left_of_the_budget() -> None:
    clock = Clock()
    seen: list[tuple[str, str, timedelta | None]] = []
    log = Logged()
    policies = (
        Policy(name="first", seen=seen, clock=clock, spends=0.5),
        Policy(name="second", seen=seen, clock=clock, spends=2.0),
        Policy(name="third", seen=seen, clock=clock),
    )
    assert await chained(*policies, clock=clock, log=log).after_issue(CALL, DOCUMENT)
    assert seen == [
        ("first", "after_issue", ISSUE_BUDGET),
        ("second", "after_issue", timedelta(seconds=1)),
        ("third", "after_issue", timedelta(0)),  # never negative
    ]
    assert log.lines == []
    assert not await chained(log=log).after_issue(CALL, DOCUMENT)  # nothing ran
    assert timedelta(milliseconds=1500) == ISSUE_BUDGET


async def test_a_policy_that_fails_or_runs_out_of_time_is_logged_by_type_and_passed_over() -> None:
    seen: list[tuple[str, str, timedelta | None]] = []
    log = Logged()
    failing = Policy(name="failing", seen=seen, fails=True)
    blocked = Policy(name="blocked", seen=seen, blocks=True)
    last = Policy(name="last", seen=seen)
    policies = IssuePolicies(
        chain=lambda: (failing, blocked, last),
        log=log,
        monotonic=Clock(),
        budget=timedelta(milliseconds=20),  # what times the blocked one out: a short real wait
    )
    assert await policies.after_issue(CALL, DOCUMENT)
    assert [name for name, _, _ in seen] == ["failing", "blocked", "last"]
    assert blocked.cancelled
    assert log.lines == [
        ("error", "invoicing.after_issue_failed", {"policy": "Policy", "error": "LookupError"}),
        ("warning", "invoicing.after_issue_timed_out", {"policy": "Policy"}),
    ]


async def test_a_policy_with_no_time_left_is_timed_out_at_once() -> None:
    clock = Clock()
    seen: list[tuple[str, str, timedelta | None]] = []
    log = Logged()
    slow = Policy(name="slow", seen=seen, clock=clock, spends=5.0)
    blocked = Policy(name="blocked", seen=seen, blocks=True)
    assert await chained(slow, blocked, clock=clock, log=log).after_issue(CALL, DOCUMENT)
    assert seen[-1] == ("blocked", "after_issue", timedelta(0))
    assert blocked.cancelled
    assert log.lines == [("warning", "invoicing.after_issue_timed_out", {"policy": "Policy"})]


@final
class Rendering(Renderer):
    def __init__(self, answer: RenderedDocument | None, seen: list[str]) -> None:
        self.answer = answer
        self.seen = seen

    @override
    async def render(self, call: Call, document: InvoiceInfo, channel: str) -> RenderedDocument | None:
        assert (call, document, channel) == (CALL, DOCUMENT, "do-ecf-v1")
        self.seen.append("rendered" if self.answer is not None else "declined")
        return self.answer


PDF: Final = RenderedDocument(media_type="application/pdf", content=b"%PDF-1.7", filename="E310000000001.pdf")


async def test_the_first_renderer_that_answers_renders_the_document() -> None:
    seen: list[str] = []
    renderers = Renderers(chain=lambda: (Rendering(None, seen), Rendering(PDF, seen), Rendering(PDF, seen)))
    assert await renderers.render(CALL, DOCUMENT, "do-ecf-v1") == PDF
    assert seen == ["declined", "rendered"]


async def test_no_renderer_that_answers_is_no_representation() -> None:
    seen: list[str] = []
    chains: tuple[tuple[Renderer, ...], ...] = ((), (Rendering(None, seen),))
    for chain in chains:
        with pytest.raises(RepresentationUnavailableError) as caught:
            await Renderers(chain=partial(tuple, chain)).render(CALL, DOCUMENT, "do-ecf-v1")
        assert (caught.value.kind, caught.value.code) == (ErrorKind.NOT_FOUND, "representation_unavailable")
        assert str(caught.value) == "no fiscal channel renders this document"
    assert seen == ["declined"]


def test_an_objection_and_a_rendered_document_are_checked_when_made() -> None:
    with pytest.raises(InvalidValueError, match=r"^objection\.code must be a snake_case code"):
        Objection(code="Not-snake", field=None, message="x")
    with pytest.raises(InvalidValueError, match=r"^objection\.field must be"):
        Objection(code="rule", field="", message="x")
    with pytest.raises(InvalidValueError, match=r"^objection\.message must be"):
        Objection(code="rule", field=None, message="")
    with pytest.raises(InvalidValueError, match=r"^rendered_document\.media_type must be"):
        RenderedDocument(media_type="pdf", content=b"", filename="a.pdf")
    for name in ("", "a b.pdf", 'a".pdf', "../a.pdf", "a" * 129):
        with pytest.raises(InvalidValueError, match=r"^rendered_document\.filename must be"):
            RenderedDocument(media_type="application/pdf", content=b"", filename=name)
    assert "PDF-1.7" not in repr(PDF)


def test_the_chains_keys_name_the_points_and_recognize_their_implementations() -> None:
    assert (ISSUE_POLICY.name, RENDERER.name) == ("invoicing.IssuePolicy", "invoicing.Renderer")
    policy = Policy(name="p", seen=[])
    assert ISSUE_POLICY.take(policy) is policy
    renderer = Rendering(None, [])
    assert RENDERER.take(renderer) is renderer
    with pytest.raises(BindingTypeError):
        ISSUE_POLICY.take(renderer)
    with pytest.raises(BindingTypeError):
        RENDERER.take(policy)
