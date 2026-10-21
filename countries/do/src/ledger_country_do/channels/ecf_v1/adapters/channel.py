"""do-ecf-v1 as fiscal routes to it: fiscal-kit's ``Channel`` over the channel's use cases (spec §8.3).

fiscal calls it with a ``SystemCall`` scoped to the tenant the call is about, outside any unit; it opens its
own. ``definition`` is what the channel is (its manifest entry, DGII's sources, the format-level fields it
reads); ``requirements`` what an enrollment still needs (``application.requirements``), in the channel's
words; ``check`` and ``issue`` a draft's objections and its document, numbered, and signed within the
request's budget (``application.issuing``), answered as the kit's ``StatusUpdate`` (``statuses``). A draft
is the call's tenant's, in an environment of its mode (``InvalidValueError`` otherwise: a defect of fiscal's
routing). The rest arrives with the tasks that build it (``status``, ``resume``, ``representation``): until
then each is ``ChannelNotReadyError``.
"""

from typing import ClassVar, Final, final, override
lazy from collections.abc import Sequence
lazy from datetime import timedelta

from ledger_country_do.channels.ecf_v1.adapters.statuses import update_of
from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_fiscal_kit.channels import Channel, Issued, Requirements
from ledger_kernel.errors import InvalidValueError
from ledger_platform.runtime.facade import facade
lazy from ledger_country_do.channels.ecf_v1.application.issuing import Issuing
lazy from ledger_country_do.channels.ecf_v1.application.requirements import Requiring
lazy from ledger_fiscal_kit.channels import (
    ChannelDef,
    EnrollmentState,
    Objection,
    Representation,
    ResumeReason,
    StatusUpdate,
)
lazy from ledger_fiscal_kit.drafts import DocumentRef, FiscalDraft
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_platform.runtime.calls import SystemCall

_ENVIRONMENTS: Final = {environment.value: environment for environment in Environment}


class ChannelNotReadyError(EcfError):
    """A call the channel does not answer yet (see the module): a defect of the caller."""

    code: ClassVar[str] = "do_ecf_not_ready"

    def __init__(self) -> None:
        super().__init__("do-ecf-v1 does not answer this yet")


@final
class DoEcfChannel(Channel):
    """The channel fiscal routes to (see the module)."""

    __slots__ = ("_definition", "_issuing", "_requiring")

    def __init__(self, definition: ChannelDef, requiring: Requiring, issuing: Issuing) -> None:
        self._definition = definition
        self._requiring = requiring
        self._issuing = issuing

    def _environment(self, call: SystemCall, draft: FiscalDraft) -> Environment:
        """``draft``'s environment, once ``draft`` is ``call``'s tenant's and the environment of its mode."""
        if call.scope != draft.scope:
            raise InvalidValueError(field="call.scope", requirement="the draft's company in the draft's mode")
        if not self._definition.allows(draft.environment, draft.mode):  # the entry's are ``Environment``'s
            raise InvalidValueError(
                field="draft.environment", requirement="one of the channel's, of its mode"
            )
        return _ENVIRONMENTS[draft.environment]

    @property
    @override
    def definition(self) -> ChannelDef:
        return self._definition

    @facade
    @override
    async def requirements(self, call: SystemCall, state: EnrollmentState) -> Requirements:
        """What the enrollment still requires, in the order ``Requirement`` lists them.

        ``SignerUnavailableError`` when the signer cannot be reached (fiscal keeps what it had).
        """
        environment = _ENVIRONMENTS.get(state.environment)
        if environment is None:
            raise InvalidValueError(field="enrollment_state.environment", requirement="one of the channel's")
        who = SigningAs(
            organization=state.organization, company=state.company, mode=state.mode, reason=call.reason
        )
        due = await self._requiring.due(who, environment)
        return Requirements(currently_due=tuple(requirement.value for requirement in due))

    @facade
    @override
    async def check(self, call: SystemCall, draft: FiscalDraft) -> Sequence[Objection]:
        """``draft``'s objections and whether a number could be given now: reads only (``issuing``)."""
        return await self._issuing.check(draft, self._environment(call, draft))

    @facade
    @override
    async def issue(self, call: SystemCall, draft: FiscalDraft, *, sign_within: timedelta | None) -> Issued:
        """``draft``'s document, numbered once and signed within ``sign_within`` if given (``issuing``).

        ``DraftObjectedError`` for a draft ``check`` objects to, ``NoNumberAvailableError`` when no number can
        be given (nothing is kept either way).
        """
        environment = self._environment(call, draft)
        who = SigningAs(
            organization=draft.organization, company=draft.company, mode=draft.mode, reason=call.reason
        )
        issued = await self._issuing.issue(draft, environment, who, sign_within=sign_within)
        return Issued(status=update_of(issued.document), signed=issued.signed)

    @facade
    @override
    async def status(self, call: SystemCall, document: DocumentRef) -> StatusUpdate | None:
        raise ChannelNotReadyError

    @facade
    @override
    async def resume(self, call: SystemCall, company: CompanyId, reason: ResumeReason) -> None:
        raise ChannelNotReadyError

    @facade
    @override
    async def representation(self, call: SystemCall, document: DocumentRef) -> Representation:
        raise ChannelNotReadyError
