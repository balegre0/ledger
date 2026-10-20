"""What invoicing reports of a policy's ``after_issue`` that ran out of time or failed: a line and a count."""

from typing import Final

from structlog.testing import capture_logs

from ledger_invoicing.adapters.logs import TelemetryLog
from ledger_platform.observability.telemetry import Telemetry

OUTCOMES: Final = "ledger_invoicing_after_issue_outcomes_total"


def count(telemetry: Telemetry, policy: str, outcome: str) -> float | None:
    return telemetry.registry.get_sample_value(OUTCOMES, {"policy": policy, "outcome": outcome})


def fail() -> None:
    raise LookupError


def test_a_policy_that_ran_out_of_time_or_failed_is_logged_and_counted() -> None:
    telemetry = Telemetry.unexported("invoicing")
    report = TelemetryLog(telemetry)
    with capture_logs() as logged:
        report.timed_out("FiscalPolicy")
        try:
            fail()
        except LookupError:
            report.exception("FiscalPolicy", "LookupError")
        report.timed_out("FiscalPolicy")
    assert [(line["event"], line["log_level"], line["policy"]) for line in logged] == [
        ("invoicing.after_issue_timed_out", "warning", "FiscalPolicy"),
        ("invoicing.after_issue_failed", "error", "FiscalPolicy"),
        ("invoicing.after_issue_timed_out", "warning", "FiscalPolicy"),
    ]
    assert logged[1]["error"] == "LookupError"
    assert isinstance(logged[1]["exc_info"], LookupError)  # rendered by the platform's redacting processors
    assert count(telemetry, "FiscalPolicy", "timed_out") == 2
    assert count(telemetry, "FiscalPolicy", "failed") == 1
