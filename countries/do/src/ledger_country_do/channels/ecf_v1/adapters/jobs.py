"""The channel's job (§3.6): ``do_ecf.advance``, one per document, keyed by its id.

Enqueued with the document (``issuing``); each run takes the document one step on (``advancing``: signed,
submitted once, polled, decided), at most one call to DGII per run, and answers the platform's outcome:
``RunAgain`` for the next step or the same one later, ``Park`` with what wakes it (``certificate``,
``outcome_unknown``, ``answer_mismatch``, ``blocked``, ``range_missing``: ``Channel.resume``), ``Done`` once
DGII decided. The job runs for the company in the mode it was enqueued for (its scope), its signatures named
``do_ecf.advance`` in the signer's audit.
"""

from typing import Final, assert_type
from uuid import UUID

from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
from ledger_country_do.channels.ecf_v1.application.steps import Again, Finished, Wait
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
from ledger_platform.jobs.outcomes import Done, JobSpec, Park, RunAgain
lazy from ledger_country_do.channels.ecf_v1.application.advancing import Advancing
lazy from ledger_platform.jobs.outcomes import Job, JobOutcome

ADVANCE: Final = "do_ecf.advance"
"""The job kind (the manifest's ``[[channels.jobs]]``)."""


def advance_job(advancing: Advancing) -> JobSpec:
    """``do_ecf.advance`` as the app runs it, over ``advancing`` (see the module)."""

    async def advance(job: Job) -> JobOutcome:
        scope = job.scope
        if not isinstance(scope, Tenant) or scope.mode is None:
            raise InvalidValueError(field=f"{ADVANCE}.scope", requirement="a company in a mode")
        who = SigningAs(
            organization=scope.organization, company=scope.company, mode=scope.mode, reason=ADVANCE
        )
        step = await advancing.advance(who, UUID(job.key))
        match step:
            case Wait(reason=reason):
                return Park(reason.value)
            case Again(at=at):
                return RunAgain(at)
            case _:
                assert_type(step, Finished)
                return Done()

    return JobSpec(kind=ADVANCE, handler=advance)
