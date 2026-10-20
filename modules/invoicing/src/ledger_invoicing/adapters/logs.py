"""Where invoicing logs (``application.ports.Log``): its own logger, scoped to the module by the app."""

from typing import final, override

from ledger_invoicing.application.ports import Log
lazy from ledger_platform.observability.telemetry import Telemetry


@final
class TelemetryLog(Log):
    """The module's logger (``ModuleContext.telemetry``): structured lines, its package named on each."""

    __slots__ = ("_telemetry",)

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry

    @override
    def warning(self, event: str, /, **fields: str) -> None:
        self._telemetry.logger.warning(event, **fields)

    @override
    def exception(self, event: str, /, **fields: str) -> None:
        self._telemetry.logger.error(event, **fields)  # the error by its type (a field), never its text
