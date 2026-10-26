"""Which of a company's enrollments a document goes to (§7.6, Decision 5).

``routes``: every active enrollment of the document's company and mode whose channel is installed and covers
the document's kind and direction (normally one: invoice → channel documents is 1:N keyed by channel).
``pending``: those not yet active (Decision 5: a pending enrollment objects, so a point of sale that believes
it is enrolled is never silently left without its fiscal number). A disabled enrollment, one of another
tenant, or one whose channel is no longer installed routes nothing.

Not in the domain, as the plan placed it: the document is invoicing's and the channels fiscal-kit's, which
fiscal's domain (the kernel only) may not import.
"""

lazy from collections.abc import Sequence

from ledger_fiscal.domain.enrollments import EnrollmentStatus
from ledger_platform.runtime.contexts import ChannelUnknownError
lazy from ledger_fiscal.domain.enrollments import Enrollment
lazy from ledger_fiscal_kit.channels import Channel, ChannelRegistry
lazy from ledger_invoicing.api import InvoiceInfo


def installed(channels: ChannelRegistry, key: str) -> Channel | None:
    """The channel of ``key``, or ``None`` when this installation has none."""
    try:
        return channels.get(key)
    except ChannelUnknownError:
        return None


def _covering(
    document: InvoiceInfo,
    enrollments: Sequence[Enrollment],
    channels: ChannelRegistry,
    status: EnrollmentStatus,
) -> tuple[Enrollment, ...]:
    found: list[Enrollment] = []
    for enrollment in enrollments:
        if enrollment.status is not status or enrollment.tenant != document.tenant:
            continue
        channel = installed(channels, enrollment.channel)
        if channel is not None and channel.definition.covers(document.kind, document.direction):
            found.append(enrollment)
    return tuple(found)


def routes(
    document: InvoiceInfo, enrollments: Sequence[Enrollment], channels: ChannelRegistry
) -> tuple[Enrollment, ...]:
    """The active enrollments ``document`` goes to (see the module), in ``enrollments``' order."""
    return _covering(document, enrollments, channels, EnrollmentStatus.ACTIVE)


def pending(
    document: InvoiceInfo, enrollments: Sequence[Enrollment], channels: ChannelRegistry
) -> tuple[Enrollment, ...]:
    """The enrollments that would take ``document`` once active (Decision 5), in ``enrollments``' order."""
    return _covering(document, enrollments, channels, EnrollmentStatus.PENDING)
