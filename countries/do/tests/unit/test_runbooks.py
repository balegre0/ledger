"""A runbook for every alert of do-ecf-v1 (spec §11): each event a person acts on, each job that may die.

The channel tells operations through its logger, and the platform's job runner counts a job that dies by its
kind (``ledger_jobs_finished_total{kind, outcome="dead"}``). Every warning or error event any source file of
the DO pack logs is listed in the alerts table (``## The alerts``) of a runbook of ``docs/runbooks/do/``, and
every job kind of the channel's manifest entry is named there beside the dead-job count; every runbook the
pack's sources point to exists.
"""

import ast
import re
from pathlib import Path
from typing import Final

from ledger_country_do._manifest import MANIFEST
from ledger_country_do.channels.ecf_v1.domain.channel import KEY

ROOT: Final = Path(__file__).parents[4]
"""The repository: the runbooks live beside the code they are about."""
RUNBOOKS: Final = ROOT / "docs" / "runbooks" / "do"
SOURCES: Final = ROOT / "countries" / "do" / "src"
LEVELS: Final = frozenset({"debug", "info", "warning", "error", "exception", "critical"})
ALERTING: Final = frozenset({"warning", "error", "exception", "critical"})
LOGGERS: Final = frozenset({"logger", "_logger", "log", "_log"})
DEAD: Final = 'ledger_jobs_finished_total{kind="'
LINKED: Final = re.compile(r"docs/runbooks/[a-z0-9/-]+\.md")
ALERTS: Final = re.compile(r"^## The alerts\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)


def _logged(call: ast.Call) -> tuple[str, str] | None:
    """The event and level of ``call``, when it is a logger's method called with an event (a string) first."""
    match call:
        case ast.Call(
            func=ast.Attribute(attr=level, value=ast.Attribute(attr=name) | ast.Name(id=name)),
            args=[ast.Constant(value=str() as event), *_],
        ) if level in LEVELS and name in LOGGERS:
            return event, level
        case _:
            return None


def events_in(source: str) -> dict[str, str]:
    """Each event ``source`` logs, and its level."""
    calls = (node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call))
    return dict(found for call in calls if (found := _logged(call)) is not None)


def events() -> dict[str, str]:
    """Each event any source file of the DO pack logs, and its level."""
    found: dict[str, str] = {}
    for path in sorted(SOURCES.rglob("*.py")):
        found |= events_in(path.read_text(encoding="utf-8"))
    return found


def alerts_tables() -> dict[str, str]:
    """Each DO runbook's alerts table (its ``## The alerts`` section), by the runbook's file name."""
    tables: dict[str, str] = {}
    for path in sorted(RUNBOOKS.glob("*.md")):
        found = ALERTS.search(path.read_text(encoding="utf-8"))
        if found is not None:
            tables[path.name] = "\n".join(line for line in found[1].splitlines() if line.startswith("|"))
    return tables


def test_a_logger_call_is_told_from_any_other_call() -> None:
    source = """
telemetry.logger.error("do_ecf.a", reason=reason)
_logger.warning("do_ecf.b")
self._telemetry.logger.info("do_ecf.c")
report.error("not_logged")
logger.error(message)
"""
    assert events_in(source) == {"do_ecf.a": "error", "do_ecf.b": "warning", "do_ecf.c": "info"}


def test_the_pack_logs_what_its_runbooks_were_written_for() -> None:
    """A new event is a new alert: it gets a runbook, and this list grows with it."""
    assert events() == {
        "do_ecf.signing_deferred": "warning",
        "do_ecf.signing_failed": "error",
        "do_ecf.submission_unknown": "warning",
        "do_ecf.waiting_for_a_person": "error",
        "do_ecf.delivery_needs_authentication": "error",
        "do_ecf.delivery_failed": "error",
        "do_ecf.approval_needs_authentication": "error",
        "do_ecf.approval_unsent": "error",
        "do_ecf.reception": "info",
    }


def test_every_event_a_person_acts_on_is_listed_in_a_runbook_s_alerts_table() -> None:
    tables = alerts_tables()
    alerting = sorted(event for event, level in events().items() if level in ALERTING)
    assert [event for event in alerting if not any(f"`{event}`" in table for table in tables.values())] == []


def test_every_job_that_may_die_is_listed_beside_the_runner_s_count_of_dead_jobs() -> None:
    (entry,) = (channel for channel in MANIFEST.channels if channel.key == KEY)
    kinds = sorted(job.kind for job in entry.jobs)
    assert kinds
    texts = [path.read_text(encoding="utf-8") for path in RUNBOOKS.glob("*.md")]
    assert [kind for kind in kinds if not any(f'{DEAD}{kind}"' in text for text in texts)] == []


def test_every_runbook_the_pack_points_to_exists() -> None:
    named = {
        found.group(0)
        for path in SOURCES.rglob("*.py")
        for found in LINKED.finditer(path.read_text(encoding="utf-8"))
    }
    assert named
    assert sorted(link for link in named if not (ROOT / link).is_file()) == []
