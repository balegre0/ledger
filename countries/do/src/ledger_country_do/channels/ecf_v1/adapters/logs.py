"""Where the channel says what it left to its job: its logger (the platform's, which redacts).

A request that numbered a document and could not sign it within its budget says why
(``do_ecf.signing_deferred``, the reason a code, never a document or a tax id): a signer often slower than
the budget shows before clients notice e-CF printed without their security code. A document a request or
the job could not sign for good (the signer's refusal, the channel's own checks of what came back) and a job
naming no document are errors (``do_ecf.signing_failed``): a person looks at them.
"""

from typing import final, override

from ledger_country_do.channels.ecf_v1.application.issuing import SigningReport
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
