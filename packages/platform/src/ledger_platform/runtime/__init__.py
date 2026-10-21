"""Running an app: assembly of modules and channels, serving, calls, facades, directories, supervision."""

from ledger_platform.config.manifest import AppRole
from ledger_platform.config.settings import Environment, Settings
from ledger_platform.observability.telemetry import Telemetry
from ledger_platform.runtime.app import App
from ledger_platform.runtime.calls import Call, SystemCall
from ledger_platform.runtime.contexts import (
    ChannelContext,
    ChannelPack,
    ChannelParts,
    Module,
    ModuleContext,
    ModuleParts,
)
from ledger_platform.runtime.facade import UnitOpenError, facade
from ledger_platform.runtime.keys import Key
from ledger_platform.runtime.serving import SERVER_LOGGERS, serve, serving_logs
from ledger_platform.runtime.supervision import Supervisor

__all__ = [
    "SERVER_LOGGERS",
    "App",
    "AppRole",
    "Call",
    "ChannelContext",
    "ChannelPack",
    "ChannelParts",
    "Environment",
    "Key",
    "Module",
    "ModuleContext",
    "ModuleParts",
    "Settings",
    "Supervisor",
    "SystemCall",
    "Telemetry",
    "UnitOpenError",
    "facade",
    "serve",
    "serving_logs",
]
