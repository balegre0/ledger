"""What do-ecf-v1 requires of an enrollment (Decision 3): asked by fiscal, answered by the signer and ranges.

A company needs a certificate that covers now (the signer's answer), a range of 31 or 32 in the enrollment's
environment that can number a document today, and the receiver's public URL configured. Each is read when
asked: nothing is stored here.
"""

from datetime import date
from typing import Final, Protocol
lazy from collections.abc import Mapping

import pytest

from ledger_country_do.channels.ecf_v1.adapters.channel import DoEcfChannel
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.application.ports import SignerUnavailableError
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange
from ledger_country_do.channels.ecf_v1.assembly import assemble
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import RangeState, RangeStatus
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
from ledger_fiscal_kit.channels import EnrollmentId, EnrollmentState, Requirements
from ledger_fiscal_kit.ports import SIGNER_PORT
from ledger_fiscal_kit.ports import SignerUnavailableError as PortUnavailableError
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.runtime.calls import SystemCall
from ledger_testing.signer import TestSigner
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.config.settings import Environment as AppEnvironment
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.runtime.contexts import ChannelContext
lazy from ledger_testing.clock import ManualClock

RECEIVED: Final = DoEcfSettings(receiver_public_url="https://receiver.ledger.test")
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)
CALL: Final = SystemCall(reason="fiscal:requirements", scope=TENANT)
NOTHING: Final = Requirements(currently_due=())


class Contexts(Protocol):
    """The conftest's ``contexts``: a channel context as the app makes it."""

    def __call__(
        self,
        *,
        settings: DoEcfSettings,
        bound: Mapping[str, object] | None = None,
        role: AppRole = "api",
        environment: AppEnvironment = "test",
    ) -> ChannelContext: ...


def state(environment: str = "testecf", tenant: Tenant = TENANT) -> EnrollmentState:
    mode = tenant.mode
    assert mode is not None
    return EnrollmentState(
        enrollment=EnrollmentId(new_id()),
        organization=tenant.organization,
        company=tenant.company,
        mode=mode,
        environment=environment,
        active=False,
    )


def channel_of(contexts: Contexts, ports: dict[str, object], **changes: object) -> DoEcfChannel:
    settings = RECEIVED.model_copy(update=changes)
    channel = assemble(contexts(settings=settings, bound=ports)).channel
    assert isinstance(channel, DoEcfChannel)
    return channel


async def registered(
    module_database: BoundDatabase,
    clock: ManualClock,
    *,
    environment: Environment = Environment.TESTECF,
    ecf_type: EcfType = EcfType.CREDIT_INVOICE,
) -> None:
    """A range of ``ecf_type`` in ``environment`` for ``TENANT``, kept as registering one keeps it."""
    async with module_database.unit(TENANT) as unit:
        ranges = bind(unit).ranges
        series = await ranges.series(environment, ecf_type)
        assert series == ()
        authorized = RangeState(
            id=new_id(),
            ecf_type=ecf_type,
            first=1,
            last=100,
            next=1,
            expires_on=date(2028, 12, 31),
            status=RangeStatus.ACTIVE,
        )
        await ranges.add(
            NumberingRange(environment=environment, range=authorized, created_at=clock(), version=1)
        )


async def test_a_company_without_a_certificate_or_a_range_needs_both(
    contexts: Contexts, ports: dict[str, object]
) -> None:
    channel = channel_of(contexts, ports | {SIGNER_PORT.name: TestSigner(validity=None)})
    assert await channel.requirements(CALL, state()) == Requirements(
        currently_due=("certificate", "numbering_range")
    )


async def test_a_certificate_and_a_range_of_the_enrollment_s_environment_leave_nothing_due(
    contexts: Contexts, ports: dict[str, object], module_database: BoundDatabase, clock: ManualClock
) -> None:
    channel = channel_of(contexts, ports)
    assert await channel.requirements(CALL, state()) == Requirements(currently_due=("numbering_range",))
    await registered(module_database, clock, environment=Environment.CERTECF)
    assert await channel.requirements(CALL, state()) == Requirements(currently_due=("numbering_range",))
    await registered(module_database, clock)
    assert await channel.requirements(CALL, state()) == NOTHING
    assert await channel.requirements(CALL, state("certecf")) == NOTHING
    other = Tenant(TENANT.organization, CompanyId(new_id()), Mode.TEST)
    assert await channel.requirements(CALL, state(tenant=other)) == Requirements(
        currently_due=("numbering_range",)
    )


async def test_a_range_of_notes_numbers_no_invoice(
    contexts: Contexts, ports: dict[str, object], module_database: BoundDatabase, clock: ManualClock
) -> None:
    await registered(module_database, clock, ecf_type=EcfType.CREDIT_NOTE)
    assert await channel_of(contexts, ports).requirements(CALL, state()) == Requirements(
        currently_due=("numbering_range",)
    )


async def test_without_the_receiver_s_url_every_enrollment_needs_it(
    contexts: Contexts, ports: dict[str, object], module_database: BoundDatabase, clock: ManualClock
) -> None:
    await registered(module_database, clock)
    channel = channel_of(contexts, ports, receiver_public_url=None)
    assert await channel.requirements(CALL, state()) == Requirements(currently_due=("receiver_url",))


async def test_an_unreachable_signer_is_unavailable_and_nothing_is_answered(
    contexts: Contexts, ports: dict[str, object]
) -> None:
    """fiscal keeps the enrollment as it was when its channel cannot answer (an ``UnavailableError``)."""
    channel = channel_of(contexts, ports | {SIGNER_PORT.name: TestSigner(failures=(PortUnavailableError(),))})
    with pytest.raises(SignerUnavailableError):
        await channel.requirements(CALL, state())


async def test_an_environment_the_channel_does_not_have_is_a_defect_of_the_caller(
    contexts: Contexts, ports: dict[str, object]
) -> None:
    with pytest.raises(InvalidValueError, match=r"enrollment_state\.environment"):
        await channel_of(contexts, ports).requirements(CALL, state("production"))
