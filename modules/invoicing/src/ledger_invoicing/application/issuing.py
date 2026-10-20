"""invoicing's extension points as it runs them (ADR 0011, §8.5): the ``IssuePolicy`` and ``Renderer`` chains.

Each chain is read when it is run, never when invoicing assembles: the modules that implement a point
assemble after invoicing, which they depend on. A chain is the implementations in ``(order, module)`` order,
empty when none is installed.

- ``IssuePolicies.check`` asks every policy, in order, with no unit open, and refuses the document with the
  objections of all of them (``DocumentInvalidError``); an empty chain refuses nothing.
- ``IssuePolicies.after_issue`` runs each policy's ``after_issue`` in order, once the document is committed,
  with what is left of the budget (never less than nothing), each cancelled when that runs out
  (``asyncio.timeout``). Running out of time is logged as a warning, anything else it raises as an error,
  both by the policy's (and the error's) type name, and passed over: the document is issued, and fiscal's
  recovery consumer finishes what the policy did not (§8.5). A cancellation of the request is not caught.
- ``Renderers.render`` answers the first representation a renderer answers, asking them in order.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, final
lazy from collections.abc import Callable, Sequence

from ledger_invoicing.api import ISSUE_BUDGET
from ledger_invoicing.domain.errors import DocumentInvalidError, RepresentationUnavailableError
lazy from ledger_invoicing.api import InvoiceInfo, IssuePolicy, RenderedDocument, Renderer
lazy from ledger_invoicing.application.ports import Log
lazy from ledger_platform.runtime.calls import Call

_NOTHING_LEFT: Final = timedelta(0)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class IssuePolicies:
    """The ``IssuePolicy`` chain, run as the module says."""

    chain: Callable[[], Sequence[IssuePolicy]]
    """The implementations, in chain order, read at each run."""
    log: Log
    budget: timedelta = ISSUE_BUDGET
    """What the whole chain's ``after_issue`` may take."""
    monotonic: Callable[[], float] = time.monotonic
    """What the budget left is measured with."""

    def judges_fields(self) -> bool:
        """Whether a chain judges the regime's undeclared country fields (Decision 24): there is one."""
        return bool(self.chain())

    async def check(self, call: Call, document: InvoiceInfo) -> None:
        """Refuse ``document`` (``DocumentInvalidError``) with every objection of the chain, if any."""
        objections = [
            objection for policy in self.chain() for objection in await policy.check(call, document)
        ]
        if objections:
            raise DocumentInvalidError(objections)

    async def after_issue(self, call: Call, document: InvoiceInfo) -> bool:
        """Run each policy's ``after_issue`` on the issued ``document``; whether any ran."""
        policies = tuple(self.chain())
        started = self.monotonic()
        for policy in policies:
            spent = timedelta(seconds=self.monotonic() - started)
            left = max(self.budget - spent, _NOTHING_LEFT)
            name = type(policy).__name__
            try:
                async with asyncio.timeout(left.total_seconds()):
                    await policy.after_issue(call, document, budget=left)
            except TimeoutError:
                self.log.warning("invoicing.after_issue_timed_out", policy=name)
            except Exception as error:  # a policy may fail with anything; a cancellation is no Exception
                self.log.exception("invoicing.after_issue_failed", policy=name, error=type(error).__name__)
        return bool(policies)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Renderers:
    """The ``Renderer`` chain, run as the module says."""

    chain: Callable[[], Sequence[Renderer]]

    async def render(self, call: Call, document: InvoiceInfo, channel: str) -> RenderedDocument:
        """The first representation of ``document`` for ``channel`` a renderer answers.

        ``RepresentationUnavailableError`` when none answers one (or none is installed).
        """
        for renderer in self.chain():
            rendered = await renderer.render(call, document, channel)
            if rendered is not None:
                return rendered
        raise RepresentationUnavailableError
