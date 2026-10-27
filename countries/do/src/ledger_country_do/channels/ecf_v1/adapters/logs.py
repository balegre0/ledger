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

A buyer that requires authentication while the company keeps it switched off is an error at every attempt
(``do_ecf.delivery_needs_authentication``, Decision 19: the client may switch it on and retry), and so is a
delivery failed for good (``do_ecf.delivery_failed``, its reason a code: Decision 21's alert).

A commercial approval the company sent that will not reach a party (``do_ecf.approval_unsent``, the party,
``emitter`` or ``dgii``, and the outcome: ``refused``, ``failed`` or ``outcome_unknown``) is an error, and so
is an emitter that requires authentication while it is switched off
(``do_ecf.approval_needs_authentication``): a person looks.

Each e-CF reception the receiver answers is an info line (``do_ecf.reception``): the receiving company's id,
the environment and the outcome (``ReceptionOutcome``), nothing of the document, its parties or its sender.

The runbooks of ``docs/runbooks/do/`` name each of these events and what a person does about it.
"""

from typing import final, override

from ledger_country_do.channels.ecf_v1.application.advancing import AdvanceReport
from ledger_country_do.channels.ecf_v1.application.approving import ApprovalReport
from ledger_country_do.channels.ecf_v1.application.delivering import DeliveryReport
from ledger_country_do.channels.ecf_v1.application.issuing import SigningReport
from ledger_country_do.channels.ecf_v1.application.receiving import ReceptionReport
lazy from ledger_country_do.channels.ecf_v1.application.approving import Party
lazy from ledger_country_do.channels.ecf_v1.application.receiving import Receiver, ReceptionOutcome
lazy from ledger_country_do.channels.ecf_v1.application.steps import ParkReason
lazy from ledger_country_do.channels.ecf_v1.domain.approval import Sending
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


@final
class DeliveryLog(DeliveryReport):
    """``DeliveryReport`` on the channel's logger: what the alerts read."""

    __slots__ = ("_telemetry",)

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry

    @override
    def needs_authentication(self) -> None:
        self._telemetry.logger.error("do_ecf.delivery_needs_authentication")

    @override
    def failed(self, reason: str) -> None:
        self._telemetry.logger.error("do_ecf.delivery_failed", reason=reason)


@final
class ApprovalLog(ApprovalReport):
    """``ApprovalReport`` on the channel's logger: what the alerts read."""

    __slots__ = ("_telemetry",)

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry

    @override
    def needs_authentication(self) -> None:
        self._telemetry.logger.error("do_ecf.approval_needs_authentication")

    @override
    def unsent(self, party: Party, outcome: Sending) -> None:
        self._telemetry.logger.error("do_ecf.approval_unsent", party=party, outcome=outcome.value)


@final
class ReceptionLog(ReceptionReport):
    """``ReceptionReport`` on the channel's logger: the company, the environment and the outcome alone."""

    __slots__ = ("_telemetry",)

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry

    @override
    def answered(self, receiver: Receiver, outcome: ReceptionOutcome) -> None:
        self._telemetry.logger.info(
            "do_ecf.reception",
            company_id=str(receiver.who.tenant.company),
            environment=receiver.environment.value,
            outcome=outcome.value,
        )
