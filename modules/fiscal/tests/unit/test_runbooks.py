"""A runbook for every alert of fiscal (spec §11: any dead job, any dead inbox row, each with its runbook).

The platform counts a job that dies by its kind (``ledger_jobs_finished_total{kind, outcome="dead"}``) and an
event a consumer gave up on by its consumer (``ledger_inbox_handled_total{consumer, outcome="dead"}``). Each
job kind and each consumer of fiscal's manifest is named beside that count by a runbook of
``docs/runbooks/fiscal/``.
"""

from pathlib import Path
from typing import Final

from ledger_fiscal._manifest import MANIFEST

RUNBOOKS: Final = Path(__file__).parents[4] / "docs" / "runbooks" / "fiscal"
"""The repository's runbooks of fiscal: beside the code they are about."""


def runbooks() -> str:
    """Every runbook of fiscal's, as one text."""
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(RUNBOOKS.glob("*.md")))


def test_every_job_that_may_die_is_named_beside_the_runner_s_count_of_dead_jobs() -> None:
    kinds = sorted(job.kind for job in MANIFEST.jobs)
    assert kinds
    text = runbooks()
    dead = 'ledger_jobs_finished_total{{kind="{}",outcome="dead"}}'
    assert [kind for kind in kinds if dead.format(kind) not in text] == []


def test_every_consumer_is_named_beside_the_inbox_s_count_of_events_given_up_on() -> None:
    consumers = sorted(consumer.consumer for consumer in MANIFEST.events.consumes)
    assert consumers
    text = runbooks()
    dead = 'ledger_inbox_handled_total{{consumer="{}",outcome="dead"}}'
    assert [consumer for consumer in consumers if dead.format(consumer) not in text] == []
