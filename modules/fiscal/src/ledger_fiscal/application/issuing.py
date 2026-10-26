"""fiscal's ``IssuePolicy`` (§8.5 steps 2 and 4, ADR 0011), and routing a document to its channels.

``check`` runs on a document as it will be issued, with no unit open, reading locally only: the company's
enrollments (those a channel marked ``review_due`` reviewed first, Decision 34); one pending that would take
the document objects ``fiscal_enrollment_pending`` (Decision 5), naming what it still requires; each channel
that routes it is asked its objections on the draft (``Channel.check``, codes kept). A field of the regime's
namespace the regime does not declare is the channels' to judge (Decision 24: DO's ``check`` objects
``do_ecf_field_unknown`` or ``do_ecf_field_invalid``, withholdings keyed by a line the document lacks
included); with no channel routing the document, nothing may read one, and each is ``field_unknown``.

``after_issue`` routes the issued document to each channel within what is left of the request's budget,
and ``Router.recover`` (the consumer of ``invoicing.invoice.issued``, the recovery path) does the same
without signing, for every issued document: each step is idempotent by (document, channel), so whichever
runs second finds the first's work and adds nothing. Routing one document to one enrollment's channel:

1. invoicing's mirror gets ``pending`` at version ``ROUTED_VERSION`` (1) **before** any number is
   allocated: a document voided first answers ``DOCUMENT_VOIDED``, and nothing is numbered or routed (so a
   document a channel may number can no longer be voided, only credited);
2. the routing is kept (``fiscal.routings``, the first one of (document, channel) wins);
3. the channel issues the draft (``Channel.issue``: a second call answers the document it numbered,
   Decision 35), signing it within ``sign_within`` when given;
4. invoicing's mirror gets the channel's status at its version + 1 (``mirrored_version``): fiscal's
   ``pending`` is the mirror's version 1, so the channel's first status (numbered, its version 1) is newer.

A channel with no number to give now (fiscal-kit's ``NumberUnavailableError``: a range ran out between
``check`` and numbering) keeps nothing. ``after_issue`` passes over it (never a client's 409: the document is
issued, its ``fiscal[]`` pending); the recovery path parks a ``fiscal.route`` job for it, woken when the
channel's requirements of the company change (``fiscal_channel.requirements.changed``: a range registered),
which routes the document again.

Every call to a channel or to invoicing is a ``SystemCall`` scoped to the document's tenant, with the reason
of the step; the consumer's carries its event's id (rule 4). No unit is open while either is called (rule 13).
"""

import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, final, override
lazy from collections.abc import Callable, Mapping, Sequence
lazy from uuid import UUID

from ledger_fiscal.application.drafts import fiscal_draft, undeclared
from ledger_fiscal.application.routing import installed, pending, routes
from ledger_fiscal.domain.routing import Routing
from ledger_fiscal_kit.objections import NumberUnavailableError
from ledger_invoicing.api import (
    Applied,
    AuthorityMessageInfo,
    FiscalStatusInfo,
    FiscalStatusName,
    InvoiceId,
    IssuePolicy,
    Objection,
)
from ledger_kernel.errors import InvalidValueError
from ledger_platform.runtime.calls import SystemCall
from ledger_platform.runtime.facade import facade
lazy from ledger_fiscal.application.enrolling import Enrolling
lazy from ledger_fiscal.application.ports import FiscalUnits
lazy from ledger_fiscal.domain.enrollments import Enrollment
lazy from ledger_fiscal_kit.channels import Channel, ChannelRegistry, StatusUpdate
lazy from ledger_invoicing.api import InvoiceInfo, InvoicingApi
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_platform.runtime.calls import AnyCall, Call
lazy from ledger_platform.runtime.registries import Regimes

CHECK_REASON: Final = "fiscal.check"
"""The reason of the calls ``check`` makes."""
ISSUE_REASON: Final = "fiscal.issue"
"""The reason of the calls ``after_issue`` makes."""
ROUTE_REASON: Final = "fiscal.route"
"""The reason of the calls the ``fiscal.route`` job makes."""
PENDING: Final = "fiscal_enrollment_pending"
"""The objection of an enrollment not yet active that would take the document (Decision 5)."""
FIELD_UNKNOWN: Final = "field_unknown"
"""The objection of an undeclared field no channel routing the document may read (Decision 24)."""
ROUTED_VERSION: Final = 1
"""The mirror's version of the ``pending`` fiscal applies before a channel numbers the document."""
_NOTHING: Final = timedelta(0)
_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
"""A field's name an objection may name as it is: anything else is named by its namespace only."""


def tenant_of(document: InvoiceInfo) -> Tenant:
    """The company and mode of ``document``: the scope of every call about it."""
    tenant = document.tenant
    if tenant.mode is None:
        raise InvalidValueError(field="document.tenant", requirement="a company in a mode")
    return tenant


def mirrored_version(update: StatusUpdate) -> int:
    """The mirror's version of a channel's ``update``: one more than the channel's (see the module)."""
    return update.version + 1


def mirrored(update: StatusUpdate, regime: str) -> FiscalStatusInfo:
    """``update`` as invoicing's mirror keeps it: its details under ``regime`` (none unless an object)."""
    details = update.details.get(regime)
    kept: Mapping[str, JsonValue] = details if isinstance(details, dict) else {}
    return FiscalStatusInfo(
        channel=update.channel,
        environment=update.environment,
        status=FiscalStatusName(update.status.value),
        authority_number=update.authority_number,
        messages=tuple(
            AuthorityMessageInfo(code=message.code, message=message.message, lang=message.lang)
            for message in update.messages
        ),
        regime=regime,
        details=kept,
        updated_at=update.occurred_at,
    )


def _pending_objection(enrollment: Enrollment) -> Objection:
    due = enrollment.currently_due
    still = f": it still requires {', '.join(due)}" if due else ""
    return Objection(
        code=PENDING,
        field=None,
        message=f"the company's enrollment on {enrollment.channel} is not active yet{still}",
    )


def _unknown(name: str, regime: str) -> Objection:
    """``field_unknown`` for ``name``: its path when it is a plain name, else its namespace (never echoed)."""
    namespace = f"fiscal.{regime}"
    path = f"{namespace}.{name}" if _NAME.fullmatch(name) is not None else namespace
    return Objection(
        code=FIELD_UNKNOWN,
        field=path,
        message=f"{namespace} holds a field neither regime {regime} nor a channel routing the document reads",
    )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Target:
    """A channel a document goes to, for one of the company's enrollments."""

    enrollment: Enrollment
    channel: Channel


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Router:
    """Routing a document to its channels (see the module).

    The steps ``after_issue``, the recovery path and the ``fiscal.route`` job share.
    """

    units: FiscalUnits
    channels: ChannelRegistry
    """The installed channels, read at call time."""
    invoicing: InvoicingApi
    enrolling: Enrolling
    regimes: Regimes
    clock: Clock

    async def enrollments(self, tenant: Tenant, *, traceparent: str | None) -> tuple[Enrollment, ...]:
        """``tenant``'s enrollments those marked for review reviewed (``Enrolling.current``)."""
        return await self.enrolling.current(tenant, traceparent=traceparent)

    async def targets(self, call: SystemCall, document: InvoiceInfo) -> tuple[Target, ...]:
        """Where ``document`` goes, one channel each.

        The channels it was routed to already (for the enrollment it was routed for, whatever became of it
        since), then those its active enrollments route it to now.
        """
        tenant = tenant_of(document)
        enrollments = await self.enrollments(tenant, traceparent=call.traceparent)
        async with self.units.unit(tenant) as unit:
            earlier = await unit.routings.of(document.id)
        by_id = {enrollment.id: enrollment for enrollment in enrollments}
        chosen: dict[str, Enrollment] = {}
        for routing in earlier:  # an enrollment is never deleted: the one a routing names is there
            chosen[routing.channel] = by_id[routing.enrollment]
        for enrollment in routes(document, enrollments, self.channels):
            _ = chosen.setdefault(enrollment.channel, enrollment)
        found: list[Target] = []
        for key, enrollment in chosen.items():
            channel = installed(self.channels, key)
            if channel is not None:
                found.append(Target(enrollment=enrollment, channel=channel))
        return tuple(found)

    async def corrected(self, call: AnyCall, document: InvoiceInfo) -> InvoiceInfo | None:
        """The invoice ``document`` corrects, read now; ``None`` for an invoice."""
        corrects = document.corrects
        return None if corrects is None else await self.invoicing.document(call, corrects)

    async def issue_to(
        self,
        call: SystemCall,
        document: InvoiceInfo,
        target: Target,
        *,
        sign_within: Callable[[], timedelta] | None,
    ) -> None:
        """Route ``document`` to ``target``'s channel (see the module, steps 1 to 4).

        ``sign_within``: what is left of the budget when the channel is asked (``None``: the channel's job
        signs). ``NumberUnavailableError`` when the channel has no number to give it now.
        """
        enrollment, channel = target.enrollment, target.channel
        regime = channel.definition.regime
        routed = FiscalStatusInfo(
            channel=enrollment.channel,
            environment=enrollment.environment,
            status=FiscalStatusName.PENDING,
            authority_number=None,
            messages=(),
            regime=regime,
            details={},
            updated_at=self.clock(),
        )
        applied = await self.invoicing.apply_fiscal_status(call, document.id, routed, ROUTED_VERSION)
        if applied is Applied.DOCUMENT_VOIDED:  # voided first: nothing reaches the channel
            return
        async with self.units.unit(tenant_of(document)) as unit:
            _ = await unit.routings.add(
                Routing(
                    invoice=document.id,
                    channel=enrollment.channel,
                    enrollment=enrollment.id,
                    routed_at=unit.now,
                )
            )
        draft = fiscal_draft(
            document,
            enrollment=enrollment,
            regime=self.regimes.get(document.regime),
            corrected=await self.corrected(call, document),
        )
        within = None if sign_within is None else max(sign_within(), _NOTHING)
        issued = await channel.issue(call, draft, sign_within=within)
        update = issued.status
        _ = await self.invoicing.apply_fiscal_status(
            call, document.id, mirrored(update, regime), mirrored_version(update)
        )

    async def recover(self, call: SystemCall, invoice: UUID) -> None:
        """The recovery path for an issued ``invoice`` (see the module): every channel it goes to, unsigned.

        A channel with no number to give it now gets a ``fiscal.route`` job, parked until it may.
        """
        document = await self.invoicing.document(call, InvoiceId(invoice))
        for target in await self.targets(call, document):
            try:
                await self.issue_to(call, document, target, sign_within=None)
            except NumberUnavailableError:
                async with self.units.unit(tenant_of(document)) as unit:
                    unit.jobs.route_later(document.id, target.enrollment.channel)

    async def retry(self, tenant: Tenant, invoice: UUID, channel: str) -> bool:
        """The ``fiscal.route`` job: route ``invoice`` to ``channel`` again; whether it got its number."""
        call = SystemCall(reason=ROUTE_REASON, scope=tenant)
        document = await self.invoicing.document(call, InvoiceId(invoice))
        found = [
            target for target in await self.targets(call, document) if target.enrollment.channel == channel
        ]
        for target in found:
            try:
                await self.issue_to(call, document, target, sign_within=None)
            except NumberUnavailableError:
                return False
        return True


@final
class FiscalPolicy(IssuePolicy):
    """invoicing's ``IssuePolicy``, as fiscal implements it (see the module)."""

    __slots__ = ("_monotonic", "_regimes", "_router")

    def __init__(
        self, router: Router, *, regimes: Regimes, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._router = router
        self._regimes = regimes
        self._monotonic = monotonic

    @override
    @facade
    async def check(self, call: Call, document: InvoiceInfo) -> Sequence[Objection]:
        tenant = tenant_of(document)
        router = self._router
        enrollments = await router.enrollments(tenant, traceparent=call.traceparent)
        found = [_pending_objection(each) for each in pending(document, enrollments, router.channels)]
        routed = routes(document, enrollments, router.channels)
        regime = self._regimes.get(document.regime)
        if not routed:
            return (*found, *(_unknown(name, regime.key) for name in undeclared(document, regime)))
        system = SystemCall(reason=CHECK_REASON, scope=tenant, traceparent=call.traceparent)
        corrected = await router.corrected(call, document)
        for enrollment in routed:
            channel = router.channels.get(enrollment.channel)
            draft = fiscal_draft(document, enrollment=enrollment, regime=regime, corrected=corrected)
            found.extend(
                Objection(code=objection.code, field=objection.field, message=objection.message)
                for objection in await channel.check(system, draft)
            )
        return tuple(found)

    @override
    @facade
    async def after_issue(self, call: Call, document: InvoiceInfo, *, budget: timedelta) -> None:
        started = self._monotonic()

        def left() -> timedelta:
            return budget - timedelta(seconds=self._monotonic() - started)

        system = SystemCall(reason=ISSUE_REASON, scope=tenant_of(document), traceparent=call.traceparent)
        for target in await self._router.targets(system, document):
            try:
                await self._router.issue_to(system, document, target, sign_within=left)
            except NumberUnavailableError:  # issued, its fiscal[] pending: the recovery path parks it
                continue
