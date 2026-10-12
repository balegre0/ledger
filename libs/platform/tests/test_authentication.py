import asyncio
import base64
import json
from http import HTTPStatus
from time import time
from typing import Final
from uuid import NAMESPACE_URL, uuid5
lazy from collections.abc import AsyncGenerator, Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st
from joserfc import jws, jwt
from joserfc.jwk import ECKey, OctKey
from pydantic import SecretStr
from structlog.testing import capture_logs

from ledger_kernel.errors import ErrorKind
from ledger_platform.authentication import (
    MAX_KEYS_SIZE,
    MAX_TOKEN_LENGTH,
    PROJECT_ROLES_CLAIM,
    HttpKeys,
    InvalidTokenError,
    InvalidTokenOptionError,
    KeysClientClosedError,
    MissingTokenError,
    PublishedKeys,
    TokenKeysUnavailableError,
    TokenPolicy,
    TokenProblem,
    TokenVerifier,
    organization_of,
)
from ledger_platform.settings import ApiSettings
from ledger_testing.clock import Moment
from ledger_testing.issuer import (
    AUDIENCE,
    ISSUER,
    ORGANIZATION_CLAIM,
    PROVIDER_ORGANIZATION,
    SUBJECT,
    LocalIssuer,
)

KEYS: Final = "http://keys.invalid/keys"
OFFLINE: Final = LocalIssuer()
"""An issuer whose keys a fake fetch hands over: for property tests, which must not wait on sockets."""
OFFLINE_POLICY: Final = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS)
UNREACHABLE_PROXY: Final = "http://127.0.0.1:1"
"""A proxy that refuses every connection: a fetch that went through it would fail."""


async def offline_keys(url: str) -> bytes:
    del url
    return OFFLINE.published()


def offline_verifier() -> TokenVerifier:
    return TokenVerifier(OFFLINE_POLICY, keys=PublishedKeys(OFFLINE_POLICY, fetch=offline_keys))


def segment(value: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def encoded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unsigned(header: dict[str, str]) -> str:
    """A token with ``header``, an empty payload and a signature of garbage."""
    return f"{segment(header)}.{segment({})}.c2lnbmF0dXJl"


async def accepted(verifier: TokenVerifier, token: str) -> bool:
    """Whether ``verifier`` accepts ``token`` (it raises otherwise) as proof of the issuer's usual subject."""
    return (await verifier.verify(token)).subject == SUBJECT


def rsa_or_ec(policy: TokenPolicy) -> TokenVerifier:
    """A verifier that allows RS256 and ES256, for tokens whose algorithm does not fit the published key."""
    return TokenVerifier(
        TokenPolicy(
            issuer=ISSUER,
            audience=AUDIENCE,
            keys_url=policy.keys_url,
            algorithms=frozenset({"RS256", "ES256"}),
        )
    )


def now() -> int:
    return int(time())


@pytest.fixture
async def issuer() -> AsyncGenerator[LocalIssuer]:
    async with LocalIssuer.started() as started:
        yield started


@pytest.fixture
def policy(issuer: LocalIssuer) -> TokenPolicy:
    return TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=issuer.keys_url, fetch_timeout=1.0)


@pytest.fixture
def moment() -> Moment:
    return Moment(1_000.0)


@pytest.fixture
def verifier(policy: TokenPolicy, moment: Moment) -> TokenVerifier:
    return TokenVerifier(policy, keys=PublishedKeys(policy, clock=moment))


async def refused(verifier: TokenVerifier, token: str) -> TokenProblem:
    with pytest.raises(InvalidTokenError) as caught:
        await verifier.verify(token)
    assert not token or token not in str(caught.value)
    return caught.value.problem


# ── Accepted ────────────────────────────────────────────────────────────────────


async def test_a_token_of_the_provider_proves_who_calls(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    token = issuer.token(subject="394763379928989699", organization="394763327332483075")
    principal = await verifier.verify(token)
    assert principal.subject == "394763379928989699"
    assert principal.provider_organization == "394763327332483075"
    assert principal.organization == organization_of("394763327332483075")
    assert principal.roles == frozenset()


async def test_project_roles_granted_in_the_callers_organization_are_read(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    generic = {"support": {PROVIDER_ORGANIZATION: "ledger.test"}, "auditor": {"9999": "other.test"}}
    token = issuer.token(roles=("operator",), claims={PROJECT_ROLES_CLAIM: generic})
    assert (await verifier.verify(token)).roles == frozenset({"operator", "support"})


async def test_an_audience_among_others_or_alone_is_enough(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    assert await accepted(verifier, issuer.token(claims={"aud": ["zitadel", AUDIENCE]}))
    assert await accepted(verifier, issuer.token(claims={"aud": AUDIENCE}))


def test_an_organization_id_is_the_same_wherever_it_is_derived() -> None:
    namespace = uuid5(NAMESPACE_URL, "urn:ledger:organization")
    assert organization_of("1001") == uuid5(namespace, "1001") == organization_of("1001")
    assert organization_of("1001") != organization_of("1002")
    assert organization_of("1001").version == 5


# ── Time ────────────────────────────────────────────────────────────────────────


async def test_an_expired_token_is_refused_beyond_the_leeway(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    assert await refused(verifier, issuer.token(claims={"exp": now() - 90})) is TokenProblem.EXPIRED
    assert await accepted(verifier, issuer.token(claims={"exp": now() - 30}))


@pytest.mark.parametrize("claim", ["nbf", "iat"])
async def test_a_token_from_the_future_is_refused_beyond_the_leeway(
    issuer: LocalIssuer, verifier: TokenVerifier, claim: str
) -> None:
    assert await refused(verifier, issuer.token(claims={claim: now() + 90})) is TokenProblem.NOT_YET_VALID
    assert await accepted(verifier, issuer.token(claims={claim: now() + 30}))


async def test_the_leeway_holds_to_its_last_second_and_not_one_more(
    issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    moment = now()
    stopped = TokenVerifier(policy, clock=lambda: moment)
    leeway = int(policy.leeway)
    assert await accepted(stopped, issuer.token(claims={"exp": moment - leeway}))
    assert await refused(stopped, issuer.token(claims={"exp": moment - leeway - 1})) is TokenProblem.EXPIRED
    assert await accepted(stopped, issuer.token(claims={"nbf": moment + leeway, "iat": moment + leeway}))
    for claim in ("nbf", "iat"):
        late = issuer.token(claims={claim: moment + leeway + 1})
        assert await refused(stopped, late) is TokenProblem.NOT_YET_VALID


async def test_the_leeway_comes_from_the_policy(issuer: LocalIssuer, policy: TokenPolicy) -> None:
    strict = TokenVerifier(
        TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=policy.keys_url, leeway=0.0)
    )
    assert await refused(strict, issuer.token(claims={"exp": now() - 30})) is TokenProblem.EXPIRED


async def test_the_verifier_reads_the_time_from_its_clock(issuer: LocalIssuer, policy: TokenPolicy) -> None:
    tomorrow = TokenVerifier(policy, clock=lambda: time() + 86_400)
    assert await refused(tomorrow, issuer.token()) is TokenProblem.EXPIRED


# ── Claims ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("claims", "problem"),
    [
        ({"iss": "https://impostor.example"}, TokenProblem.ISSUER),
        ({"aud": ["someone-else"]}, TokenProblem.AUDIENCE),
        ({"exp": None}, TokenProblem.CLAIMS),
        ({"exp": "tomorrow"}, TokenProblem.CLAIMS),
        ({"sub": None}, TokenProblem.CLAIMS),
        ({"sub": 2001}, TokenProblem.CLAIMS),
        ({"sub": "two thousand one"}, TokenProblem.CLAIMS),
        ({ORGANIZATION_CLAIM: None}, TokenProblem.CLAIMS),
        ({ORGANIZATION_CLAIM: "10 01"}, TokenProblem.CLAIMS),
        ({ORGANIZATION_CLAIM: 1001}, TokenProblem.CLAIMS),
        ({PROJECT_ROLES_CLAIM: ["operator"]}, TokenProblem.CLAIMS),
    ],
    ids=[
        "issuer",
        "audience",
        "no-exp",
        "text-exp",
        "no-sub",
        "number-sub",
        "spaced-sub",
        "no-organization",
        "spaced-organization",
        "number-organization",
        "roles-as-a-list",
    ],
)
async def test_a_token_with_wrong_or_missing_claims_is_refused(
    issuer: LocalIssuer, verifier: TokenVerifier, claims: dict[str, object], problem: TokenProblem
) -> None:
    assert await refused(verifier, issuer.token(claims=claims)) is problem


async def test_a_refusal_says_what_was_wrong_in_a_word(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    with pytest.raises(InvalidTokenError) as caught:
        await verifier.verify(issuer.token(claims={"exp": now() - 600}))
    assert str(caught.value) == "the access token was refused: expired"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.UNAUTHENTICATED, "invalid_token")


def test_no_token_at_all_is_an_error_of_its_own() -> None:
    error = MissingTokenError()
    assert (error.kind, error.code, str(error)) == (
        ErrorKind.UNAUTHENTICATED,
        "missing_token",
        "a bearer access token is required",
    )


# ── Algorithms and signatures ──────────────────────────────────────────────────


async def test_an_unsigned_token_is_refused_before_any_key_is_fetched(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    assert await refused(verifier, unsigned({"alg": "none", "kid": "key-1"})) is TokenProblem.ALGORITHM
    assert issuer.fetches == 0


async def test_an_hmac_token_keyed_with_the_public_key_is_refused(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    secret = OctKey.import_key(issuer.published())
    forged = jwt.encode(
        {"alg": "HS256", "kid": "key-1"}, {"iss": ISSUER, "aud": AUDIENCE, "sub": "1"}, secret
    )
    assert await refused(verifier, forged) is TokenProblem.ALGORITHM


async def test_an_algorithm_the_policy_does_not_allow_is_refused(
    issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    only_es256 = TokenPolicy(
        issuer=ISSUER, audience=AUDIENCE, keys_url=policy.keys_url, algorithms=frozenset({"ES256"})
    )
    assert await refused(TokenVerifier(only_es256), issuer.token()) is TokenProblem.ALGORITHM


async def test_a_tampered_token_fails_its_signature(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    header, _, signature = issuer.token().split(".")
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "root", "exp": now() + 60, ORGANIZATION_CLAIM: "1"}
    assert await refused(verifier, f"{header}.{segment(claims)}.{signature}") is TokenProblem.SIGNATURE


async def test_a_signed_payload_that_is_not_a_json_object_is_malformed(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    array = jws.serialize_compact({"alg": "RS256", "kid": "key-1"}, b"[1, 2, 3]", issuer.signing_key())
    assert await refused(verifier, array) is TokenProblem.MALFORMED


async def test_a_signed_payload_that_is_not_json_is_malformed(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    token = jws.serialize_compact({"alg": "RS256", "kid": "key-1"}, b"not json", issuer.signing_key())
    assert await refused(verifier, token) is TokenProblem.MALFORMED


@pytest.mark.parametrize(
    "token",
    ["", "not-a-token", "a.b", "a.b.c.d", "é.e30.", "!!!.e30.sig", segment({"alg": "RS256"}) + ".e30.sig"],
    ids=["empty", "word", "two-parts", "four-parts", "non-ascii", "bad-base64", "no-kid"],
)
async def test_text_that_is_not_a_signed_jwt_is_malformed(
    issuer: LocalIssuer, verifier: TokenVerifier, token: str
) -> None:
    assert await refused(verifier, token) is TokenProblem.MALFORMED
    assert issuer.fetches == 0


async def test_a_huge_token_is_refused_unread(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    huge = issuer.token(claims={"padding": "x" * MAX_TOKEN_LENGTH})
    assert await refused(verifier, huge) is TokenProblem.MALFORMED
    assert issuer.fetches == 0


async def test_a_megabyte_header_is_refused_unread(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    header = segment({"alg": "RS256", "kid": "key-1", "padding": "x" * 1_000_000})
    assert await refused(verifier, f"{header}.{segment({})}.c2lnbmF0dXJl") is TokenProblem.MALFORMED
    assert issuer.fetches == 0


@pytest.mark.parametrize("pointer", ["jku", "x5u"])
async def test_keys_a_token_points_to_are_never_fetched(verifier: TokenVerifier, pointer: str) -> None:
    async with LocalIssuer.started() as impostor:
        header = {"alg": "RS256", "kid": "key-1", pointer: impostor.keys_url}
        forged = jwt.encode(header, {"iss": ISSUER, "aud": AUDIENCE}, impostor.signing_key())
        assert await refused(verifier, forged) is TokenProblem.SIGNATURE
    assert impostor.fetches == 0


async def test_a_key_a_token_carries_is_never_trusted(policy: TokenPolicy) -> None:
    own = ECKey.generate_key("P-256", {"kid": "key-1"}, private=True)
    header = {"alg": "ES256", "kid": "key-1", "jwk": own.as_dict(private=False)}
    forged = jwt.encode(header, {"iss": ISSUER, "aud": AUDIENCE, "sub": "1"}, own)
    assert await refused(rsa_or_ec(policy), forged) is TokenProblem.ALGORITHM  # key-1 is an RSA key


async def test_an_algorithm_the_published_key_does_not_fit_is_refused(
    issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    _, payload, signature = issuer.token().split(".")
    retitled = f"{segment({'alg': 'ES256', 'kid': 'key-1'})}.{payload}.{signature}"
    assert await refused(rsa_or_ec(policy), retitled) is TokenProblem.ALGORITHM


@given(st.text(max_size=400))
async def test_no_text_makes_the_verifier_fail_otherwise_than_by_refusing_it(text: str) -> None:
    with pytest.raises(InvalidTokenError):
        await offline_verifier().verify(text)


@given(st.binary(max_size=200), st.binary(max_size=200))
async def test_no_payload_or_signature_under_a_valid_header_breaks_the_verifier(
    payload: bytes, signature: bytes
) -> None:
    token = f"{segment({'alg': 'RS256', 'kid': 'key-1'})}.{encoded(payload)}.{encoded(signature)}"
    with pytest.raises(InvalidTokenError):
        await offline_verifier().verify(token)


# ── Keys ────────────────────────────────────────────────────────────────────────


async def test_the_keys_are_fetched_once_and_kept(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    for _ in range(5):
        await verifier.verify(issuer.token())
    assert issuer.fetches == 1


async def test_the_keys_are_fetched_again_once_they_are_old(
    issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(issuer.token())
    moment.now += policy.keys_ttl
    await verifier.verify(issuer.token())
    assert issuer.fetches == 2


async def test_a_key_the_copy_lacks_refreshes_it_but_no_more_than_once_per_floor(
    issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(issuer.token())
    moment.now += policy.refresh_floor
    issuer.rotate()
    assert await accepted(verifier, issuer.token())  # signed with the key just published
    assert issuer.fetches == 2
    for number in range(20):
        assert await refused(verifier, unsigned({"alg": "RS256", "kid": f"invented-{number}"})) is (
            TokenProblem.UNKNOWN_KEY
        )
    assert issuer.fetches == 2  # within the floor: invented ids cost nothing
    moment.now += policy.refresh_floor
    assert (
        await refused(verifier, unsigned({"alg": "RS256", "kid": "invented-again"}))
        is TokenProblem.UNKNOWN_KEY
    )
    assert issuer.fetches == 3


async def test_a_retired_key_stops_working_at_the_next_refresh(
    issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    old = issuer.token()
    assert await accepted(verifier, old)
    issuer.rotate()
    issuer.retire("key-1")
    moment.now += policy.keys_ttl
    assert await refused(verifier, old) is TokenProblem.UNKNOWN_KEY


async def test_without_keys_no_token_can_be_checked_and_the_provider_is_not_hammered(
    issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    issuer.failure = (HTTPStatus.INTERNAL_SERVER_ERROR, b"oops")
    with capture_logs() as logs, pytest.raises(TokenKeysUnavailableError):
        await verifier.verify(issuer.token())
    with pytest.raises(TokenKeysUnavailableError) as caught:
        await verifier.verify(issuer.token())
    assert issuer.fetches == 1
    assert (caught.value.kind, caught.value.code) == (ErrorKind.UNAVAILABLE, "token_keys_unavailable")
    assert [log["event"] for log in logs] == ["tokens.keys_unavailable"]
    issuer.failure = None
    moment.now += policy.refresh_floor
    assert await accepted(verifier, issuer.token())


async def test_the_keys_held_keep_working_while_the_provider_is_down(
    issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(issuer.token())
    issuer.failure = (HTTPStatus.SERVICE_UNAVAILABLE, b"")
    moment.now += policy.keys_ttl
    with capture_logs():
        assert await accepted(verifier, issuer.token())
    assert issuer.fetches == 2


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
    issuer: LocalIssuer, verifier: TokenVerifier, answer: tuple[HTTPStatus, bytes]
) -> None:
    issuer.failure = answer
    with capture_logs(), pytest.raises(TokenKeysUnavailableError):
        await verifier.verify(issuer.token())


async def test_a_provider_that_does_not_answer_in_time_is_no_keys(
    issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    issuer.delay = 30.0
    impatient = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=policy.keys_url, fetch_timeout=0.1)
    with capture_logs(), pytest.raises(TokenKeysUnavailableError):
        await TokenVerifier(impatient).verify(issuer.token())


async def test_requests_that_find_the_keys_stale_together_share_one_fetch(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    async with asyncio.TaskGroup() as group:
        for _ in range(10):
            group.create_task(verifier.verify(issuer.token()))
    assert issuer.fetches == 1


async def test_a_service_fetches_with_one_client_held_for_its_run(
    issuer: LocalIssuer, policy: TokenPolicy, moment: Moment
) -> None:
    keys = HttpKeys()
    async with keys.opened() as fetch:
        verifier = TokenVerifier(policy, keys=PublishedKeys(policy, fetch=fetch, clock=moment))
        assert await accepted(verifier, issuer.token())
        moment.now += policy.keys_ttl
        assert await accepted(verifier, issuer.token())
    assert issuer.fetches == 2
    with pytest.raises(KeysClientClosedError) as caught:
        await keys(issuer.keys_url)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "keys_client_closed")


async def test_the_environment_s_proxies_do_not_reach_the_key_fetch(
    issuer: LocalIssuer, policy: TokenPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, UNREACHABLE_PROXY)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert await accepted(TokenVerifier(policy), issuer.token())
    async with HttpKeys().opened() as fetch:
        assert await fetch(issuer.keys_url) == issuer.published()


async def test_a_flood_of_invented_key_ids_at_once_costs_one_fetch(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    await verifier.verify(issuer.token())
    async with asyncio.TaskGroup() as group:
        floods = [
            group.create_task(refused(verifier, unsigned({"alg": "RS256", "kid": f"invented-{number}"})))
            for number in range(50)
        ]
    assert {flood.result() for flood in floods} == {TokenProblem.UNKNOWN_KEY}
    assert issuer.fetches == 1


async def test_the_keys_held_keep_working_while_the_provider_stalls(
    issuer: LocalIssuer, moment: Moment, policy: TokenPolicy
) -> None:
    impatient = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=policy.keys_url, fetch_timeout=0.1)
    verifier = TokenVerifier(impatient, keys=PublishedKeys(impatient, clock=moment))
    await verifier.verify(issuer.token())
    issuer.delay = 30.0
    moment.now += impatient.keys_ttl
    with capture_logs() as logs:
        assert await accepted(verifier, issuer.token())
    assert [log["event"] for log in logs] == ["tokens.keys_unavailable"]


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


# ── Policy ──────────────────────────────────────────────────────────────────────


def test_the_policy_comes_from_the_settings() -> None:
    settings = ApiSettings(
        service_name="identity",
        database_url=SecretStr("postgresql+psycopg://identity:secret@localhost/identity"),
        token_issuer=ISSUER,
        token_audience=AUDIENCE,
        token_jwks_url="http://localhost/keys",
        token_leeway_seconds=10,
    )
    expected = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url="http://localhost/keys", leeway=10.0)
    assert TokenPolicy.of(settings) == expected


@pytest.mark.parametrize(
    ("build", "option"),
    [
        (lambda: TokenPolicy(issuer="", audience=AUDIENCE, keys_url=KEYS), "issuer"),
        (lambda: TokenPolicy(issuer=ISSUER, audience="", keys_url=KEYS), "audience"),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=""), "keys_url"),
        (
            lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, algorithms=frozenset()),
            "algorithms",
        ),
        (
            lambda: TokenPolicy(
                issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, algorithms=frozenset({"HS256"})
            ),
            "algorithms",
        ),
        (
            lambda: TokenPolicy(
                issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, algorithms=frozenset({"none"})
            ),
            "algorithms",
        ),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, leeway=-1.0), "leeway"),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, leeway=301.0), "leeway"),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, leeway=float("nan")), "leeway"),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, keys_ttl=0.0), "keys_ttl"),
        (
            lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, refresh_floor=float("inf")),
            "refresh_floor",
        ),
        (
            lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, fetch_timeout=-1.0),
            "fetch_timeout",
        ),
    ],
)
def test_a_policy_that_cannot_work_is_refused(build: Callable[[], TokenPolicy], option: str) -> None:
    with pytest.raises(InvalidTokenOptionError, match=f"token option {option} must be"):
        build()
