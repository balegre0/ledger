"""The channel's jobs (§3.6): per document, per received document; the receiver's purge.

``do_ecf.advance`` and ``do_ecf.deliver`` run per document, ``do_ecf.approve`` per received document.

Enqueued with the document (``issuing``); each run takes the document one step on (``advancing``: signed,
submitted once, polled, decided), at most one call to DGII per run, and answers the platform's outcome:
``RunAgain`` for the next step or the same one later, ``Park`` with what wakes it (``certificate``,
``outcome_unknown``, ``answer_mismatch``, ``blocked``, ``range_missing``: ``Channel.resume``), ``Done`` once
DGII decided. The job runs for the company in the mode it was enqueued for (its scope), its signatures named
``do_ecf.advance`` in the signer's audit.

``do_ecf.deliver`` is enqueued once DGII accepted the document (``delivering``): each run is one exchange
with the buyer, ``RunAgain`` on the delivery's schedule while the buyer is out of reach, ``Done`` once
delivered, failed or not applicable. Its buyers' seeds are signed as ``do_ecf.deliver``.

``do_ecf.approve`` is enqueued once the company decided of a document it received: each run signs its ACECF
once and sends it to DGII and to the emitter, ``RunAgain`` on the delivery's schedule while one is out of
reach, ``Done`` once neither is left. Its signatures are named ``do_ecf.approve``.

``do_ecf.purge_receiver_credentials`` (every hour, its schedule's) deletes the receiver's seeds and tokens
past their expiry (``ReceiverCredentials.purge``): they are kept outside any tenant, so it runs without one.
"""

from typing import Final, assert_type
from uuid import UUID

from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
from ledger_country_do.channels.ecf_v1.application.steps import Again, Finished, Wait
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
from ledger_platform.jobs.outcomes import Done, JobSpec, Park, RunAgain
lazy from ledger_country_do.channels.ecf_v1.adapters.receiver.tokens import ReceiverCredentials
lazy from ledger_country_do.channels.ecf_v1.application.advancing import Advancing
lazy from ledger_country_do.channels.ecf_v1.application.approving import CommercialApprovals
lazy from ledger_country_do.channels.ecf_v1.application.delivering import Delivering
lazy from ledger_country_do.channels.ecf_v1.application.steps import Next
lazy from ledger_platform.jobs.outcomes import Job, JobOutcome

ADVANCE: Final = "do_ecf.advance"
"""The job kind (the manifest's ``[[channels.jobs]]``)."""
PURGE: Final = "do_ecf.purge_receiver_credentials"
"""The receiver's purge (the manifest's job and its schedule)."""


DELIVER: Final = "do_ecf.deliver"
"""The delivery's job kind (the manifest's ``[[channels.jobs]]``)."""
APPROVE: Final = "do_ecf.approve"
"""The commercial approval's job kind (the manifest's ``[[channels.jobs]]``)."""


def _who(job: Job) -> SigningAs:
    """Whom ``job`` runs for: the company in the mode it was enqueued for, its signatures named its kind."""
    scope = job.scope
    if not isinstance(scope, Tenant) or scope.mode is None:
        raise InvalidValueError(field=f"{job.kind}.scope", requirement="a company in a mode")
    return SigningAs(organization=scope.organization, company=scope.company, mode=scope.mode, reason=job.kind)


def _outcome(step: Next) -> JobOutcome:
    """The platform's outcome of what a run answered."""
    match step:
        case Wait(reason=reason):
            return Park(reason.value)
        case Again(at=at):
            return RunAgain(at)
        case _:
            assert_type(step, Finished)
            return Done()


def advance_job(advancing: Advancing) -> JobSpec:
    """``do_ecf.advance`` as the app runs it, over ``advancing`` (see the module)."""

    async def advance(job: Job) -> JobOutcome:
        return _outcome(await advancing.advance(_who(job), UUID(job.key)))

    return JobSpec(kind=ADVANCE, handler=advance)


def deliver_job(delivering: Delivering) -> JobSpec:
    """``do_ecf.deliver`` as the app runs it, over ``delivering`` (see the module)."""

    async def deliver(job: Job) -> JobOutcome:
        return _outcome(await delivering.deliver(_who(job), UUID(job.key)))

    return JobSpec(kind=DELIVER, handler=deliver)


def approve_job(approvals: CommercialApprovals) -> JobSpec:
    """``do_ecf.approve`` as the app runs it, over ``approvals`` (see the module)."""

    async def approve(job: Job) -> JobOutcome:
        return _outcome(await approvals.approve(_who(job), UUID(job.key)))

    return JobSpec(kind=APPROVE, handler=approve)


def purge_job(credentials: ReceiverCredentials) -> JobSpec:
    """``do_ecf.purge_receiver_credentials`` over ``credentials`` (see the module)."""

    async def purge(job: Job) -> JobOutcome:
        del job
        await credentials.purge()
        return Done()

    return JobSpec(kind=PURGE, handler=purge)
