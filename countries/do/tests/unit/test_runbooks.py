"""A runbook for every alert of do-ecf-v1 (spec §11): each event a person acts on, each job that may die.

The channel tells operations through its logger (``adapters.logs``) and the platform's job runner counts a job
that dies by its kind (``ledger_jobs_finished_total{kind, outcome="dead"}``). Every warning or error event the
channel logs is named by a runbook of ``docs/runbooks/do/``, and so is every job kind of its manifest entry
beside the dead-job count; every runbook the channel's sources point to exists.
"""

import ast
import inspect
import re
from pathlib import Path
from typing import Final

from ledger_country_do._manifest import MANIFEST
from ledger_country_do.channels.ecf_v1.adapters import logs
from ledger_country_do.channels.ecf_v1.domain.channel import KEY

ROOT: Final = Path(__file__).parents[4]
"""The repository: the runbooks live beside the code they are about."""
RUNBOOKS: Final = ROOT / "docs" / "runbooks" / "do"
SOURCES: Final = ROOT / "countries" / "do" / "src"
LEVELS: Final = frozenset({"debug", "info", "warning", "error", "exception", "critical"})
ALERTING: Final = frozenset({"warning", "error", "exception", "critical"})
DEAD: Final = 'ledger_jobs_finished_total{kind="'
LINKED: Final = re.compile(r"docs/runbooks/[a-z0-9/-]+\.md")


def events() -> dict[str, str]:
    """Each event the channel's logger is called with, and its level."""
    found: dict[str, str] = {}
    for node in ast.walk(ast.parse(inspect.getsource(logs))):
        match node:
            case ast.Call(func=ast.Attribute(attr=level), args=[ast.Constant(value=str() as event), *_]) if (
                level in LEVELS
            ):
                found[event] = level
            case _:
                pass
    return found


def runbooks() -> dict[str, str]:
    """Each runbook of the DO pack, by its file name, and its text."""
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(RUNBOOKS.glob("*.md"))}


def test_the_channel_logs_what_its_runbooks_were_written_for() -> None:
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


def test_every_event_a_person_acts_on_is_named_by_a_runbook() -> None:
    texts = runbooks()
    alerting = sorted(event for event, level in events().items() if level in ALERTING)
    assert [event for event in alerting if not any(f"`{event}`" in text for text in texts.values())] == []


def test_every_job_that_may_die_is_named_by_a_runbook_beside_the_runner_s_count_of_dead_jobs() -> None:
    (entry,) = (channel for channel in MANIFEST.channels if channel.key == KEY)
    kinds = sorted(job.kind for job in entry.jobs)
    assert kinds
    texts = runbooks()
    assert [kind for kind in kinds if not any(f'{DEAD}{kind}"' in text for text in texts.values())] == []


def test_every_runbook_the_channel_points_to_exists() -> None:
    named = {
        found.group(0)
        for path in SOURCES.rglob("*.py")
        for found in LINKED.finditer(path.read_text(encoding="utf-8"))
    }
    assert named
    assert sorted(link for link in named if not (ROOT / link).is_file()) == []
