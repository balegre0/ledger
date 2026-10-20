import asyncio
import base64
import gzip
import json
import math
from http import HTTPStatus
from typing import Final
lazy from collections.abc import MutableMapping

import pytest
from prometheus_client import CollectorRegistry
from pydantic import TypeAdapter
from structlog.testing import capture_logs

from ledger_kernel.errors import ErrorKind
from ledger_platform.auth.keys import (
    MAX_KEYS_SIZE,
    HttpKeys,
    KeysClientClosedError,
    PublishedKeys,
    TokenKeysUnavailableError,
)
from ledger_platform.auth.tokens import InvalidTokenError, TokenPolicy, TokenProblem, TokenVerifier
from ledger_testing.clock import Moment
from ledger_testing.issuer import AUDIENCE, ISSUER, SUBJECT, LocalIssuer
from ledger_testing.waiting import eventually

OFFLINE: Final = LocalIssuer()
"""An issuer whose keys a fake fetch hands over."""
OFFLINE_POLICY: Final = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url="http://keys.invalid/keys")
KEY_SET: Final = TypeAdapter(dict[str, list[dict[str, object]]])
UNREACHABLE_PROXY: Final = "http://127.0.0.1:1"
"""A proxy that refuses every connection: a fetch that went through it would fail."""


@pytest.fixture
def policy(local_issuer: LocalIssuer) -> TokenPolicy:
    # generous: a success path never races a fetch's time limit, and a stalled provider's twenty requests are
    # held to half of it, however slow a loaded machine is to answer them
    return TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=local_issuer.keys_url, fetch_timeout=4.0)


@pytest.fixture
def moment() -> Moment:
    """A clock the test moves by hand, at 1000 seconds."""
    return Moment(1_000.0)


@pytest.fixture
def verifier(policy: TokenPolicy, moment: Moment) -> TokenVerifier:
    """A verifier whose keys age on ``moment``."""
    return TokenVerifier(policy, keys=PublishedKeys(policy, clock=moment))


async def offline_keys(url: str) -> bytes:
    del url
    return OFFLINE.published()


def unsigned(kid: str) -> str:
    """A token naming the key ``kid``, with an empty payload and a signature of garbage."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "kid": kid}).encode()).rstrip(b"=").decode()
    return f"{header}.e30.c2lnbmF0dXJl"


async def accepted(verifier: TokenVerifier, token: str) -> bool:
    """Whether ``verifier`` accepts ``token`` (it raises otherwise) as proof of the issuer's usual subject."""
    return (await verifier.verify(token)).subject == SUBJECT


async def logged(logs: list[MutableMapping[str, object]], count: int = 1) -> bool:
    return len(logs) >= count


async def unknown(verifier: TokenVerifier, token: str) -> bool:
    """Whether ``verifier`` refuses ``token`` for its key id, which the keys it holds lack."""
    try:
        await verifier.verify(token)
    except InvalidTokenError as error:
        return error.problem is TokenProblem.UNKNOWN_KEY
    return False


async def refused(verifier: TokenVerifier, token: str) -> TokenProblem:
    with pytest.raises(InvalidTokenError) as caught:
        await verifier.verify(token)
    return caught.value.problem


async def test_the_keys_are_fetched_once_and_kept(local_issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    for _ in range(5):
        await verifier.verify(local_issuer.token())
    assert local_issuer.fetches == 1


async def test_the_keys_are_fetched_again_once_they_are_old(
    local_issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    old = local_issuer.token()
    await verifier.verify(old)
    moment.now += policy.keys_ttl
    local_issuer.rotate()
    local_issuer.retire("key-1")
    assert await accepted(verifier, old)  # held, and only past its time to live: answered at once
    await eventually(lambda: unknown(verifier, old))
    assert local_issuer.fetches == 2


async def test_a_key_the_copy_lacks_refreshes_it_but_no_more_than_once_per_floor(
    local_issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(local_issuer.token())
    moment.now += policy.refresh_floor
    local_issuer.rotate()
    assert await accepted(verifier, local_issuer.token())  # signed with the key just published
    assert local_issuer.fetches == 2
    for number in range(20):
        assert await refused(verifier, unsigned(f"invented-{number}")) is (TokenProblem.UNKNOWN_KEY)
    assert local_issuer.fetches == 2  # within the floor: invented ids cost nothing
    moment.now += policy.refresh_floor
    assert await refused(verifier, unsigned("invented-again")) is TokenProblem.UNKNOWN_KEY
    assert local_issuer.fetches == 3


async def test_a_retired_key_stops_working_once_the_refresh_it_set_off_completes(
    local_issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    old = local_issuer.token()
    assert await accepted(verifier, old)
    local_issuer.rotate()
    local_issuer.retire("key-1")
    moment.now += policy.keys_ttl
    assert await accepted(verifier, old)
    await eventually(lambda: unknown(verifier, old))


async def test_keys_past_their_ceiling_are_refreshed_before_the_request_is_answered(
    local_issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    old = local_issuer.token()
    assert await accepted(verifier, old)
    local_issuer.rotate()
    local_issuer.retire("key-1")
    moment.now += policy.keys_max_age
    assert await refused(verifier, old) is TokenProblem.UNKNOWN_KEY


async def test_keys_past_their_ceiling_still_work_when_the_refresh_fails(
    local_issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(local_issuer.token())
    local_issuer.failure = (HTTPStatus.SERVICE_UNAVAILABLE, b"")
    moment.now += policy.keys_max_age
    with capture_logs() as logs:
        assert await accepted(verifier, local_issuer.token())
    assert [log["event"] for log in logs] == ["tokens.keys_past_ceiling", "tokens.keys_unavailable"]


async def test_past_the_ceiling_a_hung_provider_stalls_one_request_and_no_more(
    local_issuer: LocalIssuer, moment: Moment
) -> None:
    # generous: the twenty requests below are held to half of it, a machine under load being slow to answer
    patient = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=local_issuer.keys_url, fetch_timeout=4.0)
    verifier = TokenVerifier(patient, keys=PublishedKeys(patient, clock=moment))
    old = local_issuer.token()
    await verifier.verify(old)
    local_issuer.delay = 30.0
    moment.now += patient.keys_max_age
    with capture_logs() as logs:
        assert await accepted(verifier, old)  # waited once, for a refresh that failed
        assert [log["event"] for log in logs] == ["tokens.keys_past_ceiling", "tokens.keys_unavailable"]
        moment.now += patient.refresh_floor
        async with asyncio.timeout(patient.fetch_timeout / 2), asyncio.TaskGroup() as group:
            answers = [group.create_task(accepted(verifier, old)) for _ in range(20)]
        assert all(answer.result() for answer in answers)
        assert not await logged(logs, 3)  # answered while their refresh still waits
        await eventually(lambda: logged(logs, 3), within=3 * patient.fetch_timeout)  # its refresh failed too
        local_issuer.delay = 0.0
        local_issuer.rotate()
        local_issuer.retire("key-1")
        moment.now += patient.refresh_floor
        assert await accepted(verifier, old)  # still not waiting: the refresh runs behind
        await eventually(lambda: unknown(verifier, old))  # the provider is back: the copy is fresh again
    assert [log["event"] for log in logs][3:] == ["tokens.keys_refreshed"]
    assert logs[3]["log_level"] == "info"


async def test_once_refreshed_past_the_ceiling_the_next_crossing_waits_again(
    local_issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(local_issuer.token())
    local_issuer.failure = (HTTPStatus.SERVICE_UNAVAILABLE, b"")
    moment.now += policy.keys_max_age
    with capture_logs():
        assert await accepted(verifier, local_issuer.token())  # waited, failed: held keys served from now on
        local_issuer.failure = None
        moment.now += policy.refresh_floor
        old = local_issuer.token()
        local_issuer.rotate()
        local_issuer.retire("key-1")
        assert await accepted(verifier, old)
        await eventually(lambda: unknown(verifier, old))
    newest = local_issuer.token()
    local_issuer.rotate()
    local_issuer.retire("key-2")
    moment.now += policy.keys_max_age
    with capture_logs():
        assert await refused(verifier, newest) is TokenProblem.UNKNOWN_KEY  # waited: no failure since


async def test_the_age_of_the_keys_held_is_exported(
    local_issuer: LocalIssuer, policy: TokenPolicy, moment: Moment
) -> None:
    registry = CollectorRegistry()
    verifier = TokenVerifier(policy, keys=PublishedKeys(policy, clock=moment, registry=registry))
    assert registry.get_sample_value("ledger_auth_keys_age_seconds") == math.inf  # none held yet
    await verifier.verify(local_issuer.token())
    assert registry.get_sample_value("ledger_auth_keys_age_seconds") == 0.0
    moment.now += 42.5
    assert registry.get_sample_value("ledger_auth_keys_age_seconds") == 42.5


async def test_a_keys_document_without_a_key_with_an_id_keeps_the_keys_held(
    local_issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(local_issuer.token())
    keys = KEY_SET.validate_json(local_issuer.published())["keys"]
    nameless = [{name: value for name, value in key.items() if name != "kid"} for key in keys]
    local_issuer.failure = (HTTPStatus.OK, json.dumps({"keys": nameless}).encode())
    moment.now += policy.keys_max_age
    with capture_logs() as logs:
        assert await accepted(verifier, local_issuer.token())
    assert [log["event"] for log in logs] == ["tokens.keys_past_ceiling", "tokens.keys_unavailable"]


async def test_without_keys_no_token_can_be_checked_and_the_provider_is_not_hammered(
    local_issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    local_issuer.failure = (HTTPStatus.INTERNAL_SERVER_ERROR, b"oops")
    with capture_logs() as logs, pytest.raises(TokenKeysUnavailableError):
        await verifier.verify(local_issuer.token())
    with pytest.raises(TokenKeysUnavailableError) as caught:
        await verifier.verify(local_issuer.token())
    assert local_issuer.fetches == 1
    assert (caught.value.kind, caught.value.code) == (ErrorKind.UNAVAILABLE, "token_keys_unavailable")
    assert [log["event"] for log in logs] == ["tokens.keys_unavailable"]
    local_issuer.failure = None
    moment.now += policy.refresh_floor
    assert await accepted(verifier, local_issuer.token())


async def test_the_keys_held_keep_working_while_the_provider_is_down(
    local_issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(local_issuer.token())
    local_issuer.failure = (HTTPStatus.SERVICE_UNAVAILABLE, b"")
    moment.now += policy.keys_ttl
    with capture_logs() as logs:
        assert await accepted(verifier, local_issuer.token())
        await eventually(lambda: logged(logs))
        assert await accepted(verifier, local_issuer.token())
    assert local_issuer.fetches == 2


@pytest.mark.parametrize(
    "answer",
    [
        (HTTPStatus.OK, b"not json"),
        (HTTPStatus.OK, b'{"keys": []}'),
        (HTTPStatus.OK, b'{"keys": [{"kty": "RSA", "n": "AQAB"}]}'),
        (HTTPStatus.OK, b'{"keys": [' + b" " * MAX_KEYS_SIZE + b"]}"),
        (HTTPStatus.FOUND, b""),
    ],
    ids=["garbage", "empty", "broken-key", "too-large", "redirect"],
)
async def test_a_keys_document_that_cannot_be_used_is_no_keys(
    local_issuer: LocalIssuer, verifier: TokenVerifier, answer: tuple[HTTPStatus, bytes]
) -> None:
    local_issuer.failure = answer
    with capture_logs(), pytest.raises(TokenKeysUnavailableError):
        await verifier.verify(local_issuer.token())


async def test_keys_are_asked_for_uncompressed_and_a_compressed_answer_is_no_keys(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    local_issuer.encoding = "gzip"
    local_issuer.failure = (HTTPStatus.OK, gzip.compress(local_issuer.published()))
    with capture_logs(), pytest.raises(TokenKeysUnavailableError):
        await verifier.verify(local_issuer.token())
    assert b"\r\naccept-encoding: identity\r\n" in local_issuer.requests[0].lower()


async def test_a_provider_that_does_not_answer_in_time_is_no_keys(
    local_issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    local_issuer.delay = 30.0
    impatient = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=policy.keys_url, fetch_timeout=0.1)
    with capture_logs(), pytest.raises(TokenKeysUnavailableError):
        await TokenVerifier(impatient).verify(local_issuer.token())


async def test_requests_that_find_the_keys_stale_together_share_one_fetch(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    async with asyncio.TaskGroup() as group:
        for _ in range(10):
            group.create_task(verifier.verify(local_issuer.token()))
    assert local_issuer.fetches == 1


async def test_a_service_fetches_with_one_client_held_for_its_run(
    local_issuer: LocalIssuer, policy: TokenPolicy, moment: Moment
) -> None:
    keys = HttpKeys()
    async with keys.opened() as fetch:
        verifier = TokenVerifier(policy, keys=PublishedKeys(policy, fetch=fetch, clock=moment))
        old = local_issuer.token()
        assert await accepted(verifier, old)
        local_issuer.rotate()
        local_issuer.retire("key-1")
        moment.now += policy.keys_ttl
        assert await accepted(verifier, old)
        await eventually(lambda: unknown(verifier, old))
    assert local_issuer.fetches == 2
    with pytest.raises(KeysClientClosedError) as caught:
        await keys(local_issuer.keys_url)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "keys_client_closed")


async def test_the_environment_s_proxies_do_not_reach_the_key_fetch(
    local_issuer: LocalIssuer, policy: TokenPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, UNREACHABLE_PROXY)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert await accepted(TokenVerifier(policy), local_issuer.token())
    async with HttpKeys().opened() as fetch:
        assert await fetch(local_issuer.keys_url) == local_issuer.published()


async def test_a_flood_of_invented_key_ids_at_once_costs_one_fetch(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    await verifier.verify(local_issuer.token())
    async with asyncio.TaskGroup() as group:
        floods = [
            group.create_task(refused(verifier, unsigned(f"invented-{number}"))) for number in range(50)
        ]
    assert {flood.result() for flood in floods} == {TokenProblem.UNKNOWN_KEY}
    assert local_issuer.fetches == 1


async def test_requests_with_a_key_held_never_wait_for_a_provider_that_stalls(
    local_issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(local_issuer.token())
    local_issuer.delay = 30.0
    moment.now += policy.keys_ttl
    with capture_logs() as logs:
        async with asyncio.timeout(policy.fetch_timeout / 2), asyncio.TaskGroup() as group:
            answers = [group.create_task(accepted(verifier, local_issuer.token())) for _ in range(20)]
        assert all(answer.result() for answer in answers)
        assert not await logged(logs)  # answered while their refresh still waits
        # the one refresh behind them gave up on its own
        await eventually(lambda: logged(logs), within=3 * policy.fetch_timeout)
    assert local_issuer.fetches == 2


async def test_a_request_that_goes_away_does_not_cancel_the_fetch_others_wait_for() -> None:
    asked, answer = asyncio.Event(), asyncio.Event()

    async def held(url: str) -> bytes:
        asked.set()
        await answer.wait()
        return await offline_keys(url)

    verifier = TokenVerifier(OFFLINE_POLICY, keys=PublishedKeys(OFFLINE_POLICY, fetch=held))
    leaving = asyncio.create_task(verifier.verify(OFFLINE.token()))
    await asked.wait()
    staying = asyncio.create_task(verifier.verify(OFFLINE.token()))
    leaving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaving
    answer.set()
    assert (await staying).subject == SUBJECT
