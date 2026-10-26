"""do-ecf-v1 assembled (§3.10): its ports read, its settings checked, its clients opened in the worker only.

Assembly builds and reads, and opens nothing: the worker's DGII and buyer clients and the receiver's trust are
resources the app opens at start, the trusted roots read then. A refusal names the setting and what it must
be, never its value.
"""

from http import HTTPStatus
from typing import Final, Protocol, final, override
lazy from collections.abc import Mapping
lazy from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from ledger_country_do.channels.ecf_v1.adapters.buyers.client import BuyerClient
from ledger_country_do.channels.ecf_v1.adapters.buyers.delivery import BuyerDeliveries
from ledger_country_do.channels.ecf_v1.adapters.buyers.emitters import EmitterApprovals
from ledger_country_do.channels.ecf_v1.adapters.channel import DoEcfChannel
from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import HostRefusedError
from ledger_country_do.channels.ecf_v1.adapters.inbound import Inbound, InboundClosedError
from ledger_country_do.channels.ecf_v1.adapters.outbound import (
    Outbound,
    OutboundClosedError,
    TrustedRootsUnreadableError,
    unopened_buyers,
    unopened_emitters,
)
from ledger_country_do.channels.ecf_v1.adapters.routes.deliveries import PREFIX as DELIVERIES
from ledger_country_do.channels.ecf_v1.adapters.routes.options import PATH as OPTIONS
from ledger_country_do.channels.ecf_v1.adapters.routes.ranges import PREFIX
from ledger_country_do.channels.ecf_v1.adapters.routes.received import PREFIX as RECEIVED_DOCUMENTS
from ledger_country_do.channels.ecf_v1.application.advancing import SettleTooShortError
from ledger_country_do.channels.ecf_v1.assembly import SettingRefusedError, assemble
from ledger_country_do.channels.ecf_v1.domain.fields import CHANNEL_FIELDS
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
from ledger_fiscal_kit.channels import Channel
from ledger_fiscal_kit.ports import (
    ARCHIVE_PORT,
    COMPANY_DIRECTORY,
    SIGNER_PORT,
    CompanyDirectory,
    ReceivingCompany,
)
from ledger_kernel.access import CompanyNotFoundError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.regimes import Direction, DocumentKind, Locale
from ledger_kernel.tenancy import Mode
from ledger_platform.http.problems import install_problem_handlers
from ledger_platform.runtime.facade import facade
from ledger_platform.runtime.keys import BindingMissingError
from ledger_testing.pki import pem
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_kernel.tax_ids import TaxId
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.config.settings import Environment
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_platform.runtime.contexts import ChannelContext
lazy from ledger_testing.pki import Authority

FINGERPRINT: Final = "ab" * 32
RECEIVED: Final = DoEcfSettings(receiver_public_url="https://receiver.ledger.test")


class Contexts(Protocol):
    """The conftest's ``contexts``: a channel context as the app makes it."""

    def __call__(
        self,
        *,
        settings: DoEcfSettings,
        bound: Mapping[str, object] | None = None,
        role: AppRole = "api",
        environment: Environment = "test",
    ) -> ChannelContext: ...


def test_the_channel_assembles_with_its_definition_routes_and_migrations(contexts: Contexts) -> None:
    parts = assemble(contexts(settings=RECEIVED))
    channel = parts.channel
    assert isinstance(channel, DoEcfChannel)
    assert isinstance(channel, Channel)
    definition = channel.definition
    assert (definition.key, definition.regime, definition.environments, definition.live_environments) == (
        "do-ecf-v1",
        "do",
        ("testecf", "certecf", "ecf"),
        ("ecf",),
    )
    assert (definition.mode_of("ecf"), definition.mode_of("testecf")) == (Mode.LIVE, Mode.TEST)
    assert definition.covers(DocumentKind.CREDIT_NOTE, Direction.PURCHASE)
    assert definition.fields == CHANNEL_FIELDS
    assert set(definition.labels) == {Locale("es"), Locale("en")}
    assert definition.sources
    assert [route for router in parts.routers for route in router.routes]  # its ranges, its deliveries...
    assert [router.prefix for router in parts.routers] == [PREFIX, DELIVERIES, OPTIONS, RECEIVED_DOCUMENTS]
    assert (parts.resources, parts.consumers, parts.receiver_routers) == ((), (), ())
    assert [spec.kind for spec in parts.jobs] == [
        "do_ecf.advance",
        "do_ecf.deliver",
        "do_ecf.approve",
        "do_ecf.purge_receiver_credentials",
    ]
    assert parts.migrations is not None
    assert parts.migrations.schema == "do_ecf"


@pytest.mark.parametrize("missing", [SIGNER_PORT.name, ARCHIVE_PORT.name])
def test_the_channel_does_not_start_without_its_signer_or_its_archive(
    contexts: Contexts, ports: dict[str, object], missing: str
) -> None:
    bound = {name: value for name, value in ports.items() if name != missing}
    with pytest.raises(BindingMissingError, match=f"'{missing}'"):
        assemble(contexts(settings=RECEIVED, bound=bound))


def test_migrate_reads_no_archive_and_gets_the_channel_s_migrations_alone(
    contexts: Contexts, ports: dict[str, object]
) -> None:
    """``ledger-migrate`` archives nothing: it needs no archive configured, and mounts or opens nothing."""
    bound = {name: value for name, value in ports.items() if name != ARCHIVE_PORT.name}
    parts = assemble(contexts(settings=RECEIVED, bound=bound, role="migrate"))
    assert (parts.routers, parts.resources) == ((), ())
    # declared: every app hands its handler
    assert [spec.kind for spec in parts.jobs] == [
        "do_ecf.advance",
        "do_ecf.deliver",
        "do_ecf.approve",
        "do_ecf.purge_receiver_credentials",
    ]
    assert parts.migrations is not None
    assert parts.migrations.schema == "do_ecf"
    with pytest.raises(BindingMissingError, match="'ArchivePort'"):
        assemble(contexts(settings=RECEIVED, bound=bound, role="api"))


def test_an_attempt_longer_than_the_settle_delay_is_refused(contexts: Contexts) -> None:
    """A request still in flight when track ids are asked for could land after the answer, and be missed."""
    slow = RECEIVED.model_copy(update={"dgii_attempt_seconds": 31.0})
    with pytest.raises(SettleTooShortError):
        assemble(contexts(settings=slow))
    assemble(contexts(settings=RECEIVED.model_copy(update={"dgii_attempt_seconds": 30.0})))


def test_another_dgii_host_must_be_an_origin(contexts: Contexts) -> None:
    with pytest.raises(HostRefusedError):
        assemble(contexts(settings=RECEIVED.model_copy(update={"ecf_host": "https://dgii.invalid/path"})))
    fake = RECEIVED.model_copy(
        update={"ecf_host": "http://127.0.0.1:8471", "fc_host": "http://127.0.0.1:8471"}
    )
    assert isinstance(assemble(contexts(settings=fake)).channel, DoEcfChannel)


def test_test_sender_stand_ins_are_refused_in_production_without_naming_them(contexts: Contexts) -> None:
    standing_in = RECEIVED.model_copy(update={"test_sender_standins": frozenset({FINGERPRINT})})
    with pytest.raises(SettingRefusedError, match="LEDGER_DO_ECF_TEST_SENDER_STANDINS") as caught:
        assemble(contexts(settings=standing_in, environment="production"))
    assert FINGERPRINT not in str(caught.value)
    assert caught.value.code == "do_ecf_setting_refused"
    assemble(contexts(settings=standing_in, environment="staging"))  # certification runs there


@pytest.mark.parametrize(
    ("environment", "allowed"), [("local", True), ("test", True), ("staging", False), ("production", False)]
)
def test_plain_http_beyond_loopback_is_for_the_local_stack_and_tests_alone(
    contexts: Contexts, environment: Environment, *, allowed: bool
) -> None:
    """``http://fake-dgii:8471`` (compose's fake DGII) assembles in ``local`` and ``test`` only; loopback's
    fake and https hosts anywhere a fake may stand in."""
    on_a_service = RECEIVED.model_copy(
        update={"ecf_host": "http://fake-dgii:8471", "fc_host": "https://fc.fake.test"}
    )
    on_loopback = RECEIVED.model_copy(
        update={"ecf_host": "http://127.0.0.1:8471", "fc_host": "http://localhost:8471"}
    )
    if allowed:
        assert isinstance(
            assemble(contexts(settings=on_a_service, environment=environment)).channel, DoEcfChannel
        )
    else:
        with pytest.raises(SettingRefusedError, match="LEDGER_DO_ECF_ECF_HOST") as caught:
            assemble(contexts(settings=on_a_service, environment=environment))
        assert "fake-dgii" not in str(caught.value)
    assert isinstance(assemble(contexts(settings=on_loopback, environment=environment)).channel, DoEcfChannel)


def test_the_worker_needs_trusted_roots(contexts: Contexts) -> None:
    with pytest.raises(SettingRefusedError, match=r"LEDGER_DO_ECF_TRUSTED_ROOTS .* the worker"):
        assemble(contexts(settings=RECEIVED, role="worker"))


async def test_the_receiver_reads_its_trusted_roots_at_start_and_opens_no_client(
    contexts: Contexts, tmp_path: Path, authority: Authority
) -> None:
    """The documents emitters send the receiver chain to the roots, read when it starts as the worker reads
    them; DGII's and the buyers' clients are the worker's alone (the receiver mounts the channel's receiver
    routes, under ``/do-ecf-v1/``, and the api app does not)."""
    with pytest.raises(SettingRefusedError, match=r"LEDGER_DO_ECF_TRUSTED_ROOTS .* the receiver"):
        assemble(contexts(settings=RECEIVED, role="receiver"))
    roots = tmp_path / "roots.pem"
    roots.write_bytes(pem(authority.certificate))
    parts = assemble(contexts(settings=RECEIVED.model_copy(update={"trusted_roots": roots}), role="receiver"))
    (mounted,) = parts.receiver_routers
    assert {route.path for route in mounted.routes if isinstance(route, APIRoute)} == {
        f"/do-ecf-v1/{{environment}}/{{rnc}}/fe/{path}"
        for path in (
            "autenticacion/api/semilla",
            "autenticacion/api/validacioncertificado",
            "recepcion/api/ecf",
            "aprobacioncomercial/api/ecf",
        )
    }
    assert [spec.kind for spec in parts.jobs] == [
        "do_ecf.advance",
        "do_ecf.deliver",
        "do_ecf.approve",
        "do_ecf.purge_receiver_credentials",
    ]
    (opening,) = parts.resources
    async with opening as opened:
        assert isinstance(opened, Inbound)
        assert list(opened.trust.roots) == [authority.certificate]
    with pytest.raises(InboundClosedError):
        _ = opened.trust


async def test_the_worker_opens_its_dgii_and_buyer_clients_at_start_and_closes_them_at_stop(
    contexts: Contexts, tmp_path: Path, authority: Authority
) -> None:
    roots = tmp_path / "roots.pem"
    roots.write_bytes(pem(authority.certificate))
    rooted = RECEIVED.model_copy(update={"trusted_roots": roots})
    (opening,) = assemble(contexts(settings=rooted, role="worker")).resources
    async with opening as opened:
        assert isinstance(opened, Outbound)
        assert isinstance(opened.dgii, DgiiClient)
        assert isinstance(opened.buyers, BuyerClient)
        assert isinstance(opened.deliveries(), BuyerDeliveries)
        assert isinstance(opened.approvals(), EmitterApprovals)
    with pytest.raises(OutboundClosedError):
        _ = opened.dgii
    with pytest.raises(OutboundClosedError):
        _ = opened.buyers
    with pytest.raises(OutboundClosedError):
        opened.deliveries()
    with pytest.raises(OutboundClosedError):
        opened.approvals()
    with pytest.raises(OutboundClosedError):  # every other role's: the delivery job is the worker's
        unopened_buyers()
    with pytest.raises(OutboundClosedError):  # the approval job's likewise
        unopened_emitters()


@pytest.mark.parametrize("content", [None, b"", b"-----BEGIN CERTIFICATE-----\nnot base64\n"])
@pytest.mark.parametrize("role", ["worker", "receiver"])
async def test_roots_that_cannot_be_read_stop_the_worker_and_the_receiver_without_naming_their_file(
    contexts: Contexts, tmp_path: Path, content: bytes | None, role: AppRole
) -> None:
    roots = tmp_path / "secret-roots.pem"
    if content is not None:
        roots.write_bytes(content)
    rooted = RECEIVED.model_copy(update={"trusted_roots": roots})
    (opening,) = assemble(contexts(settings=rooted, role=role)).resources
    with pytest.raises(TrustedRootsUnreadableError) as caught:
        async with opening:
            pytest.fail("an app that cannot verify a counterparty never runs")
    assert "secret-roots" not in str(caught.value)
    assert caught.value.code == "do_ecf_trusted_roots_unreadable"


@final
class WrongMode(CompanyDirectory):
    """A directory that names any tax id's company, in the live mode whatever the environment: a defect."""

    @facade
    @override
    async def receiving_company(
        self, call: SystemCall, *, tax_id: TaxId, channel: str, environment: str
    ) -> ReceivingCompany | None:
        del call, channel, environment
        return ReceivingCompany(
            organization=OrganizationId(new_id()),
            company=CompanyId(new_id()),
            mode=Mode.LIVE,
            legal_name="RECEPTORA SRL",
            tax_id=tax_id,
        )

    @facade
    @override
    async def company(self, call: SystemCall, company: CompanyId) -> ReceivingCompany:
        del call
        raise CompanyNotFoundError(company)

    @facade
    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        del principal, company
        return frozenset()


async def test_a_company_the_directory_answers_in_another_mode_than_the_environment_s_is_not_found(
    contexts: Contexts, ports: dict[str, object], tmp_path: Path, authority: Authority
) -> None:
    """``testecf`` serves the test mode (``ChannelDef.mode_of``): a live company there is no receiver's."""
    roots = tmp_path / "roots.pem"
    roots.write_bytes(pem(authority.certificate))
    rooted = RECEIVED.model_copy(update={"trusted_roots": roots})
    bound = ports | {COMPANY_DIRECTORY.name: WrongMode()}
    (mounted,) = assemble(contexts(settings=rooted, bound=bound, role="receiver")).receiver_routers
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(mounted)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://r.test") as client:
        response = await client.get("/do-ecf-v1/testecf/131098193/fe/autenticacion/api/semilla")
    assert response.status_code == HTTPStatus.NOT_FOUND, response.text
