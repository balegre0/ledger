"""The channel's job (§3.6): ``do_ecf.advance``, one per document, keyed by its id.

Enqueued with the document (``issuing``); each run signs a ``numbered`` document (Task 18's step, the one
``Channel.issue`` takes within a request's budget) and parks it for what it waits for next: submission
(``submission``, Task 19's step), a certificate (``certificate``, woken by ``Channel.resume``), or a person
(``blocked``). The job runs for the company in the mode it was enqueued for (its scope), its signatures
named ``do_ecf.advance`` in the signer's audit.
"""

from typing import Final
from uuid import UUID

from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
from ledger_platform.jobs.outcomes import JobSpec, Park
lazy from ledger_country_do.channels.ecf_v1.application.issuing import Issuing
lazy from ledger_platform.jobs.outcomes import Job, JobOutcome

ADVANCE: Final = "do_ecf.advance"
"""The job kind (the manifest's ``[[channels.jobs]]``)."""


def advance_job(issuing: Issuing) -> JobSpec:
    """``do_ecf.advance`` as the app runs it, over ``issuing`` (see the module)."""

    async def advance(job: Job) -> JobOutcome:
        scope = job.scope
        if not isinstance(scope, Tenant) or scope.mode is None:
            raise InvalidValueError(field=f"{ADVANCE}.scope", requirement="a company in a mode")
        who = SigningAs(
            organization=scope.organization, company=scope.company, mode=scope.mode, reason=ADVANCE
        )
        reason = await issuing.advance(who, UUID(job.key))
        return Park(reason.value)

    return JobSpec(kind=ADVANCE, handler=advance)
