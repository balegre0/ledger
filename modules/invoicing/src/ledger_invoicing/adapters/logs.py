"""Where invoicing reports a policy's ``after_issue`` that did not finish: its logger and a counter.

Lines go through the platform's logging, whose processors redact what an exception's text and traceback
repeat (``ledger_platform.observability.logs``); the counter is the app's registry's
(``ledger_invoicing_after_issue_outcomes_total{policy, outcome}``), so a slow signer or a failing channel
shows on a dashboard before a client notices documents finished by the recovery path.
"""

import sys
from typing import Final, final, override

from prometheus_client import Counter

from ledger_invoicing.application.ports import AfterIssueReport
lazy from ledger_platform.observability.telemetry import Telemetry

OUTCOMES: Final = "ledger_invoicing_after_issue_outcomes"


@final
class TelemetryLog(AfterIssueReport):
    """The module's logger and a counter in the app's registry (``ModuleContext.telemetry``)."""

    __slots__ = ("_outcomes", "_telemetry")

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry
        self._outcomes = Counter(
            OUTCOMES,
            "After-issue work of an IssuePolicy that did not finish, by policy and outcome.",
            ("policy", "outcome"),
            registry=telemetry.registry,
        )

    @override
    def timed_out(self, policy: str) -> None:
        self._outcomes.labels(policy=policy, outcome="timed_out").inc()
        self._telemetry.logger.warning("invoicing.after_issue_timed_out", policy=policy)

    @override
    def exception(self, policy: str, error: str) -> None:
        self._outcomes.labels(policy=policy, outcome="failed").inc()
        handled = (
            sys.exception()
        )  # the one being handled: its traceback, redacted by the platform's processors
        self._telemetry.logger.error(
            "invoicing.after_issue_failed", policy=policy, error=error, exc_info=handled
        )
