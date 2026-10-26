"""The receiver's limits (spec §10; Decision 40): sizes before parsing, a rate per RNC, credentials purged.

A reception's body is refused over 2 MiB (``MAX_INBOUND``, the reader's own limit) before anything reads it;
a signed seed over 64 KiB is refused unread. Each company's routes in an environment answer at most 10
requests a second, 50 at once (``(environment, rnc)``: the app's per-address limit is another, Task 13's).
Seeds and tokens past their expiry are deleted every hour (``do_ecf.purge_receiver_credentials``).
"""

from datetime import timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Final, Protocol
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.receiver.routes import (
    MAX_SEED_BYTES,
    MAX_SMALL_BODY,
    RATE_BURST,
    RATE_PER_SECOND,
)
from ledger_country_do.channels.ecf_v1.adapters.receiver.seeds import SignedSeed
from ledger_country_do.channels.ecf_v1.adapters.receiver.tokens import ReceiverCredentials
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import MAX_INBOUND
from ledger_country_do.channels.ecf_v1.application.receiving import Sender
from ledger_country_do.channels.ecf_v1.assembly import assemble
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
from ledger_kernel.tenancy import Mode
from ledger_platform.jobs.outcomes import Done, Job
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.runtime.contexts import ChannelContext
lazy from ledger_testing.clock import ManualClock

RECEIVING_RNC: Final = "131098193"
BASE: Final = f"/do-ecf-v1/testecf/{RECEIVING_RNC}/fe"
SEED: Final = f"{BASE}/autenticacion/api/semilla"
VALIDATION: Final = f"{BASE}/autenticacion/api/validacioncertificado"
RECEPTION: Final = f"{BASE}/recepcion/api/ecf"
APPROVAL: Final = f"{BASE}/aprobacioncomercial/api/ecf"
GOLDEN: Final = Path(__file__).parents[1] / "contract" / "golden" / "ecf-31.xml"
PURGE: Final = "do_ecf.purge_receiver_credentials"
COUNTS: Final = TypeAdapter(list[tuple[int, int]])


class Contexts(Protocol):
    def __call__(self, *, settings: DoEcfSettings, role: AppRole = "api") -> ChannelContext: ...


async def test_a_reception_over_2_mib_is_refused_before_it_is_read(
    receiver_client: httpx.AsyncClient, token: str, tmp_path: Path
) -> None:
    assert MAX_INBOUND == 2 * 1024 * 1024
    large = b"<ECF>" + b" " * (3 * 1024 * 1024) + b"</ECF>"
    response = await receiver_client.post(
        RECEPTION, files={"xml": ("e.xml", large, "text/xml")}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == HTTPStatus.CONTENT_TOO_LARGE, response.text
    assert list((tmp_path / "archive").rglob("*.xml")) == []


@pytest.mark.usefixtures("receiving")
async def test_a_signed_seed_over_64_kib_is_refused_before_it_is_read(
    receiver_client: httpx.AsyncClient, sender: KeyPairSigner
) -> None:
    assert MAX_SEED_BYTES == 64 * 1024
    seed = (await receiver_client.get(SEED)).content
    padded = sender.sign(seed).replace(b"</SemillaModel>", b" " * MAX_SEED_BYTES + b"</SemillaModel>")
    response = await receiver_client.post(VALIDATION, files={"xml": ("s.xml", padded, "text/xml")})
    assert response.status_code == HTTPStatus.CONTENT_TOO_LARGE, response.text


@pytest.mark.usefixtures("receiving")
async def test_a_company_s_routes_are_held_to_its_own_rate(
    receiver_client: httpx.AsyncClient, clock: ManualClock
) -> None:
    """The receiver's clock stands still: the burst is all there is until it moves on."""
    assert (RATE_PER_SECOND, RATE_BURST) == (10, 50)
    for _ in range(RATE_BURST):
        assert (await receiver_client.get(SEED)).status_code == HTTPStatus.OK
    limited = await receiver_client.get(SEED)
    assert limited.status_code == HTTPStatus.TOO_MANY_REQUESTS, limited.text
    assert limited.headers["retry-after"] == "1"
    elsewhere = await receiver_client.get(SEED.replace("/testecf/", "/certecf/"))
    assert elsewhere.status_code == HTTPStatus.NOT_FOUND  # another (environment, rnc): its own allowance
    clock.advance(timedelta(seconds=1))
    assert (await receiver_client.get(SEED)).status_code == HTTPStatus.OK


async def counted(admin: psycopg.AsyncConnection) -> tuple[int, int]:
    found = await admin.execute(
        "SELECT (SELECT count(*) FROM do_ecf.receiver_seeds), (SELECT count(*) FROM do_ecf.receiver_tokens)"
    )
    ((seeds, tokens),) = COUNTS.validate_python(await found.fetchall())
    return seeds, tokens


def purge_job() -> Job:
    return Job(
        kind=PURGE,
        key=PURGE,
        scope=None,
        payload={},
        attempts=1,
        max_attempts=1,
        deadline_at=None,
        lease_token=uuid4(),
        traceparent=None,
    )


async def test_expired_seeds_and_tokens_are_purged_a_batch_at_a_time(
    module_database: BoundDatabase, contexts: Contexts, admin: psycopg.AsyncConnection, clock: ManualClock
) -> None:
    """Every hour, by the channel's job; each batch in a unit of its own, until one comes back short."""
    credentials = ReceiverCredentials(module_database, clock=clock)
    company = uuid4()
    first, _ = await credentials.seed(company, Environment.TESTECF)
    sender = Sender(identities=frozenset({"101850043"}), stand_in=False)
    for value in (first, *[(await credentials.seed(company, Environment.TESTECF))[0] for _ in range(2)]):
        signed = SignedSeed(value=value, sender=sender)
        token = await credentials.exchange(
            signed, company=company, mode=Mode.TEST, environment=Environment.TESTECF
        )
        assert token is not None
    clock.advance(timedelta(minutes=6))
    await credentials.seed(company, Environment.TESTECF)
    assert await counted(admin) == (4, 3)
    await credentials.purge(batch=1)
    assert await counted(admin) == (1, 3)
    clock.advance(timedelta(hours=1))
    settings = DoEcfSettings(receiver_public_url="https://receiver.ledger.test")
    (purge,) = [spec for spec in assemble(contexts(settings=settings)).jobs if spec.kind == PURGE]
    assert isinstance(await purge.handler(purge_job()), Done)
    assert await counted(admin) == (0, 0)


async def test_a_reception_of_one_and_a_half_mib_is_read(
    receiver_client: httpx.AsyncClient, token: str, sender: KeyPairSigner
) -> None:
    """Over the app's 1 MiB, under the route's 2 MiB: read (the padding breaks its signature: reason 2)."""
    signed = sender.sign(GOLDEN.read_bytes())
    large = signed.replace(b"</ECF>", b" " * (3 * 512 * 1024) + b"</ECF>")
    response = await receiver_client.post(
        RECEPTION, files={"xml": ("e.xml", large, "text/xml")}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == HTTPStatus.OK, response.text


@pytest.mark.usefixtures("receiving")
async def test_validation_and_approval_bodies_over_64_kib_are_refused_before_they_are_parsed(
    receiver_client: httpx.AsyncClient, token: str
) -> None:
    """A body that is no form at all would be a 400 once parsed: refused for its size first, it is a 413."""
    assert MAX_SMALL_BODY == 64 * 1024
    body = b"<a/>" + b" " * (512 * 1024)
    headers = {"Content-Type": "text/xml", "Authorization": f"Bearer {token}"}
    for route in (VALIDATION, APPROVAL):
        response = await receiver_client.post(route, content=body, headers=headers)
        assert response.status_code == HTTPStatus.CONTENT_TOO_LARGE, response.text


@pytest.mark.usefixtures("receiving")
@pytest.mark.parametrize("where", [SEED.replace("/testecf/", "/prod/"), SEED.replace("131098193", "abc")])
async def test_a_path_that_names_no_environment_or_tax_id_is_held_to_no_company_s_rate(
    receiver_client: httpx.AsyncClient, where: str
) -> None:
    """Only a known environment and an RNC or cédula are counted: arbitrary paths fill no bucket, so they can
    never push a company's out (the app's per-address limit still counts them)."""
    for _ in range(RATE_BURST + 1):
        assert (await receiver_client.get(where)).status_code == HTTPStatus.NOT_FOUND
