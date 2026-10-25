"""Where the channel says what it left to its job: its logger (the platform's, which redacts).

A request that numbered a document and could not sign it within its budget says why
(``do_ecf.signing_deferred``, the reason a code, never a document or a tax id): a signer often slower than
the budget shows before clients notice e-CF printed without their security code. A document a request or
the job could not sign for good (the signer's refusal, the channel's own checks of what came back) and a job
naming no document are errors (``do_ecf.signing_failed``): a person looks at them.

A submission whose outcome became unknown is a warning (``do_ecf.submission_unknown``, its reason: the
failure, ``marked`` or ``undocumented``); its track ids settle it, usually. A document left waiting for a
person (an outcome unknown for a day, or in CerteCF; an answer about another e-NCF) is an error
(``do_ecf.waiting_for_a_person``, its reason; runbook ``docs/runbooks/do/outcome-unknown.md``, Task 27).
"""

from typing import final, override

from ledger_country_do.channels.ecf_v1.application.advancing import AdvanceReport
from ledger_country_do.channels.ecf_v1.application.issuing import SigningReport
lazy from ledger_country_do.channels.ecf_v1.application.steps import ParkReason
lazy from ledger_platform.observability.telemetry import Telemetry


@final
class SigningLog(SigningReport):
    """``SigningReport`` on the channel's logger (``ChannelContext.telemetry``)."""

    __slots__ = ("_telemetry",)

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry

    @override
    def deferred(self, reason: str) -> None:
        self._telemetry.logger.warning("do_ecf.signing_deferred", reason=reason)

    @override
    def failed(self, reason: str) -> None:
        self._telemetry.logger.error("do_ecf.signing_failed", reason=reason)


@final
class AdvanceLog(AdvanceReport):
    """``AdvanceReport`` on the channel's logger: what the alerts and the runbooks read."""

    __slots__ = ("_telemetry",)

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry

    @override
    def unknown(self, reason: str) -> None:
        self._telemetry.logger.warning("do_ecf.submission_unknown", reason=reason)

    @override
    def waiting(self, reason: ParkReason) -> None:
        self._telemetry.logger.error("do_ecf.waiting_for_a_person", reason=reason.value)
