"""do-ecf-v1 as fiscal routes to it: fiscal-kit's ``Channel`` over the channel's use cases (spec §8.3).

fiscal calls it with a ``SystemCall`` scoped to the tenant the call is about, outside any unit; it opens its
own. ``definition`` is what the channel is (its manifest entry, DGII's sources, the format-level fields it
reads); ``requirements`` what an enrollment still needs (``application.requirements``), in the channel's
words. The rest arrives with the tasks that build it (``check`` and ``issue`` with numbering and signing,
``status``, ``resume``, ``representation``): until then each is ``ChannelNotReadyError``, a defect of a caller
that routes a document to a channel that issues none yet.
"""

from typing import ClassVar, Final, final, override
lazy from collections.abc import Sequence
lazy from datetime import timedelta

from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_fiscal_kit.channels import Channel, Requirements
from ledger_kernel.errors import InvalidValueError
from ledger_platform.runtime.facade import facade
lazy from ledger_country_do.channels.ecf_v1.application.requirements import Requiring
lazy from ledger_fiscal_kit.channels import (
    ChannelDef,
    EnrollmentState,
    Issued,
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
    """A document routed to the channel before it issues any: a defect of the caller (see the module)."""

    code: ClassVar[str] = "do_ecf_not_ready"

    def __init__(self) -> None:
        super().__init__("do-ecf-v1 does not issue documents yet: route none to it")


@final
class DoEcfChannel(Channel):
    """The channel fiscal routes to (see the module)."""

    __slots__ = ("_definition", "_requiring")

    def __init__(self, definition: ChannelDef, requiring: Requiring) -> None:
        self._definition = definition
        self._requiring = requiring

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
        raise ChannelNotReadyError

    @facade
    @override
    async def issue(self, call: SystemCall, draft: FiscalDraft, *, sign_within: timedelta | None) -> Issued:
        raise ChannelNotReadyError

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
