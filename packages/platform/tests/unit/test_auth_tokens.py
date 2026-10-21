import base64
import json
from time import time
from typing import Final
lazy from collections.abc import Callable, MutableMapping

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from joserfc import jws, jwt
from joserfc.jwk import ECKey, OctKey, RSAKey
from joserfc.jws import JWSRegistry
from pydantic import SecretStr, TypeAdapter

from ledger_kernel.access import PrincipalKind
from ledger_kernel.errors import ErrorKind
from ledger_platform.auth.keys import PublishedKeys
from ledger_platform.auth.providers.zitadel import PRINCIPAL_KIND_CLAIM, organization_of, project_roles_claim
from ledger_platform.auth.tokens import (
    MAX_TOKEN_LENGTH,
    InvalidTokenError,
    MissingTokenError,
    TokenPolicy,
    TokenProblem,
    TokenVerifier,
)
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.config.settings import Settings
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
TOKEN_CLAIMS: Final = TypeAdapter(dict[str, object])
KEY_SET: Final = TypeAdapter(dict[str, list[dict[str, object]]])
JSON_VALUES: Final = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=16),
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=8), inner, max_size=4),
    max_leaves=8,
)
"""Any JSON value, nested."""
REGISTERED_HEADERS: Final = ("crit", "b64", "jwk", "jku", "x5c", "x5u", "typ", "cty", "kid")
UNREACHABLE_PROXY: Final = "http://127.0.0.1:1"
"""A proxy that refuses every connection: a fetch that went through it would fail."""


@pytest.fixture
def policy(local_issuer: LocalIssuer) -> TokenPolicy:
    return TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=local_issuer.keys_url)  # never a race


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


def offline_verifier() -> TokenVerifier:
    return TokenVerifier(OFFLINE_POLICY, keys=PublishedKeys(OFFLINE_POLICY, fetch=offline_keys))


def segment(value: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def encoded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def signed(key: RSAKey, header: dict[str, object], payload: bytes) -> str:
    """A token of ``header`` and ``payload`` as written, signed RS256 with ``key``, whatever they say."""
    signing_input = f"{segment(header)}.{encoded(payload)}"
    signature = JWSRegistry.algorithms["RS256"].sign(signing_input.encode(), key)
    return f"{signing_input}.{encoded(signature)}"


def payload_of(token: str) -> bytes:
    """The payload of a token, as it was signed."""
    raw = token.split(".")[1]
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


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
    assert not token or token not in str(caught.value)
    assert caught.value.__cause__ is None  # nothing attached can quote the token or its claims
    assert caught.value.__context__ is None
    return caught.value.problem


# ── Accepted ────────────────────────────────────────────────────────────────────


async def test_a_token_of_the_provider_proves_who_calls(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    token = local_issuer.token(subject="394763379928989699", organization="394763327332483075")
    principal = await verifier.verify(token)
    assert principal.subject == "394763379928989699"
    assert principal.provider_organization == "394763327332483075"
    assert principal.organization == organization_of("394763327332483075")
    assert principal.roles == frozenset()


async def test_a_user_signed_in_through_an_application_is_a_person(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    """What Zitadel v4.19.2 issues for a login (checked against it): ``client_id`` is the application's id,
    and the application is among the audiences."""
    claims = {
        "client_id": "395736099391012867",
        "aud": ["395736027249049603", "395736099391012867", AUDIENCE],
    }
    principal = await verifier.verify(local_issuer.token(claims=claims))
    assert principal.kind is PrincipalKind.HUMAN


@pytest.mark.parametrize(
    "audiences", [[AUDIENCE], AUDIENCE, ["zitadel", AUDIENCE]], ids=["list", "one", "two"]
)
async def test_a_machine_user_with_its_own_credentials_is_a_machine(
    local_issuer: LocalIssuer, verifier: TokenVerifier, audiences: object
) -> None:
    """What Zitadel v4.19.2 issues to a machine user through client credentials or the JWT profile grant
    (checked against it): ``client_id`` is the machine user's user name, which is no audience."""
    principal = await verifier.verify(
        local_issuer.token(claims={"client_id": "ledger-dev", "aud": audiences})
    )
    assert principal.kind is PrincipalKind.MACHINE


async def test_a_machine_named_like_the_project_is_still_a_machine(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    """The project is no application a person signs in through: a ``client_id`` equal to it is a machine's,
    though it is among the audiences (a machine user renamed to the project's id, a token exchange)."""
    token = local_issuer.token(claims={"client_id": AUDIENCE, "aud": [AUDIENCE]})
    assert (await verifier.verify(token)).kind is PrincipalKind.MACHINE


@pytest.mark.parametrize(
    ("claimed", "client", "kind"),
    [("human", "app", PrincipalKind.HUMAN), ("machine", "ledger-dev", PrincipalKind.MACHINE)],
)
async def test_the_signed_kind_claim_says_who_calls_when_it_agrees(
    local_issuer: LocalIssuer, verifier: TokenVerifier, claimed: str, client: str, kind: PrincipalKind
) -> None:
    audiences = [AUDIENCE, "app"] if client == "app" else [AUDIENCE]
    token = local_issuer.token(claims={PRINCIPAL_KIND_CLAIM: claimed, "client_id": client, "aud": audiences})
    assert (await verifier.verify(token)).kind is kind


@pytest.mark.parametrize(
    ("claimed", "client", "audiences"),
    [
        ("machine", "app", [AUDIENCE, "app"]),  # an application's token claimed by a machine
        ("human", "ledger-dev", [AUDIENCE]),  # a machine's own credential claimed by a person
        ("robot", "app", [AUDIENCE, "app"]),
        (["human"], "app", [AUDIENCE, "app"]),
    ],
    ids=["machine-through-an-application", "person-without-one", "no-such-kind", "not-text"],
)
async def test_a_kind_claim_that_contradicts_the_token_or_is_no_kind_is_refused(
    local_issuer: LocalIssuer, verifier: TokenVerifier, claimed: object, client: str, audiences: list[str]
) -> None:
    token = local_issuer.token(claims={PRINCIPAL_KIND_CLAIM: claimed, "client_id": client, "aud": audiences})
    assert await refused(verifier, token) is TokenProblem.KIND


async def test_a_policy_that_requires_the_kind_claim_refuses_a_token_without_it(
    local_issuer: LocalIssuer,
) -> None:
    strict = TokenVerifier(
        TokenPolicy(
            issuer=ISSUER, audience=AUDIENCE, keys_url=local_issuer.keys_url, kind_claim_required=True
        )
    )
    assert await refused(strict, local_issuer.token()) is TokenProblem.KIND
    claimed = local_issuer.token(claims={PRINCIPAL_KIND_CLAIM: "human"})
    assert (await strict.verify(claimed)).kind is PrincipalKind.HUMAN


async def test_the_roles_of_this_project_granted_in_the_callers_organization_are_read(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    ours = {"operator": {PROVIDER_ORGANIZATION: "ledger.test"}, "auditor": {"9999": "other.test"}}
    token = local_issuer.token(claims={project_roles_claim(AUDIENCE): ours})
    assert (await verifier.verify(token)).roles == frozenset({"operator"})


async def test_roles_of_no_project_or_of_another_project_do_not_count(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    """Zitadel's generic claim gathers the roles of whatever projects the token was asked for: a role there,
    or under another project's id, was granted for something else than this API."""
    granted = {"ledger.organization.admin": {PROVIDER_ORGANIZATION: "ledger.test"}}
    token = local_issuer.token(
        claims={
            "urn:zitadel:iam:org:project:roles": granted,
            project_roles_claim("another-project"): granted,
        }
    )
    assert (await verifier.verify(token)).roles == frozenset()


async def test_an_audience_among_others_or_alone_is_enough(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    assert await accepted(verifier, local_issuer.token(claims={"aud": ["zitadel", AUDIENCE]}))
    assert await accepted(verifier, local_issuer.token(claims={"aud": AUDIENCE}))


# ── Time ────────────────────────────────────────────────────────────────────────


async def test_an_expired_token_is_refused_beyond_the_leeway(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    assert await refused(verifier, local_issuer.token(claims={"exp": now() - 90})) is TokenProblem.EXPIRED
    assert await accepted(verifier, local_issuer.token(claims={"exp": now() - 30}))


@pytest.mark.parametrize("claim", ["nbf", "iat"])
async def test_a_token_from_the_future_is_refused_beyond_the_leeway(
    local_issuer: LocalIssuer, verifier: TokenVerifier, claim: str
) -> None:
    assert (
        await refused(verifier, local_issuer.token(claims={claim: now() + 90})) is TokenProblem.NOT_YET_VALID
    )
    assert await accepted(verifier, local_issuer.token(claims={claim: now() + 30}))


async def test_the_leeway_holds_up_to_its_edge_and_exp_is_exclusive(
    local_issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    moment = now()
    stopped = TokenVerifier(policy, clock=lambda: moment)
    leeway = policy.leeway
    assert await accepted(stopped, local_issuer.token(claims={"exp": moment - leeway + 1}))
    assert await refused(stopped, local_issuer.token(claims={"exp": moment - leeway})) is TokenProblem.EXPIRED
    assert await accepted(
        stopped, local_issuer.token(claims={"nbf": moment + leeway, "iat": moment + leeway})
    )
    for claim in ("nbf", "iat"):
        late = local_issuer.token(claims={claim: moment + leeway + 1})
        assert await refused(stopped, late) is TokenProblem.NOT_YET_VALID


async def test_the_leeway_comes_from_the_policy(local_issuer: LocalIssuer, policy: TokenPolicy) -> None:
    strict = TokenVerifier(TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=policy.keys_url, leeway=0))
    assert await refused(strict, local_issuer.token(claims={"exp": now() - 30})) is TokenProblem.EXPIRED


async def test_a_time_may_have_a_fraction_of_a_second(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    times = {"exp": now() + 60.5, "nbf": now() - 0.5, "iat": now() - 0.5}
    assert await accepted(verifier, local_issuer.token(claims=times))


@pytest.mark.parametrize("claim", ["exp", "nbf", "iat"])
@pytest.mark.parametrize("value", [b"NaN", b"Infinity", b"-Infinity", b"true", b"1e400"])
async def test_a_time_that_is_not_a_finite_number_is_refused(
    local_issuer: LocalIssuer, verifier: TokenVerifier, claim: str, value: bytes
) -> None:
    claims = TOKEN_CLAIMS.validate_json(payload_of(local_issuer.token()))
    claims[claim] = "@"
    payload = json.dumps(claims).encode().replace(b'"@"', value)
    token = jws.serialize_compact({"alg": "RS256", "kid": "key-1"}, payload, local_issuer.signing_key())
    assert await refused(verifier, token) is TokenProblem.CLAIMS


async def test_the_verifier_reads_the_time_from_its_clock(
    local_issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    tomorrow = TokenVerifier(policy, clock=lambda: time() + 86_400)
    assert await refused(tomorrow, local_issuer.token()) is TokenProblem.EXPIRED


# ── Claims ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("claims", "problem"),
    [
        ({"iss": "https://impostor.example"}, TokenProblem.ISSUER),
        ({"iss": 1}, TokenProblem.ISSUER),
        ({"iss": [ISSUER]}, TokenProblem.ISSUER),
        ({"iss": None}, TokenProblem.ISSUER),
        ({"aud": ["someone-else"]}, TokenProblem.AUDIENCE),
        ({"aud": [AUDIENCE, 1]}, TokenProblem.AUDIENCE),
        ({"aud": 1}, TokenProblem.AUDIENCE),
        ({"aud": None}, TokenProblem.AUDIENCE),
        ({"exp": None}, TokenProblem.CLAIMS),
        ({"exp": "tomorrow"}, TokenProblem.CLAIMS),
        ({"sub": None}, TokenProblem.CLAIMS),
        ({"sub": 2001}, TokenProblem.CLAIMS),
        ({"sub": "two thousand one"}, TokenProblem.CLAIMS),
        ({ORGANIZATION_CLAIM: None}, TokenProblem.CLAIMS),
        ({ORGANIZATION_CLAIM: "10 01"}, TokenProblem.CLAIMS),
        ({ORGANIZATION_CLAIM: 1001}, TokenProblem.CLAIMS),
        ({project_roles_claim(AUDIENCE): ["operator"]}, TokenProblem.CLAIMS),
    ],
    ids=[
        "issuer",
        "number-issuer",
        "issuer-in-a-list",
        "no-issuer",
        "audience",
        "audience-beside-a-number",
        "number-audience",
        "no-audience",
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
    local_issuer: LocalIssuer, verifier: TokenVerifier, claims: dict[str, object], problem: TokenProblem
) -> None:
    assert await refused(verifier, local_issuer.token(claims=claims)) is problem


# ── Access tokens, not ID tokens ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "typ", ["JWT", "jwt", "at+jwt", "AT+JWT", "application/jwt", "application/at+jwt", None]
)
async def test_a_token_typed_as_an_access_token_or_untyped_is_accepted(
    local_issuer: LocalIssuer, verifier: TokenVerifier, typ: str | None
) -> None:
    header: dict[str, object] = {"alg": "RS256", "kid": "key-1"}
    if typ is not None:
        header["typ"] = typ
    assert await accepted(
        verifier, signed(local_issuer.signing_key(), header, payload_of(local_issuer.token()))
    )


@pytest.mark.parametrize("typ", ["logout+jwt", "secevent+jwt", "JOSE", "id_token+jwt", ""])
async def test_a_token_typed_as_something_else_is_refused_before_any_key_is_fetched(
    local_issuer: LocalIssuer, verifier: TokenVerifier, typ: str
) -> None:
    header: dict[str, object] = {"alg": "RS256", "kid": "key-1", "typ": typ}
    token = signed(local_issuer.signing_key(), header, payload_of(local_issuer.token()))
    assert await refused(verifier, token) is TokenProblem.WRONG_KIND
    assert local_issuer.fetches == 0


@pytest.mark.parametrize(
    "claims",
    [
        {"jti": None},
        {"jti": ""},
        {"jti": 7},
        {"jti": " "},
        {"jti": "\t\n"},
        {"client_id": None},
        {"client_id": ""},
        {"client_id": ["ledger-test"]},
        {"client_id": "   "},
        {"azp": "ledger-web"},
        {"nonce": "a1b2c3d4"},
        {"at_hash": "x7Kq2Lw9Rt4Vb8Zm1Hc5Jd"},
        {"c_hash": "LDktKdoQak3Pk0cnXxCltA"},
    ],
    ids=[
        "no-jti",
        "empty-jti",
        "number-jti",
        "blank-jti",
        "whitespace-jti",
        "no-client-id",
        "empty-client-id",
        "client-id-in-a-list",
        "blank-client-id",
        "authorized-party",
        "nonce",
        "access-token-hash",
        "code-hash",
    ],
)
async def test_a_token_without_the_claims_of_an_access_token_or_with_an_id_token_s_is_refused(
    local_issuer: LocalIssuer, verifier: TokenVerifier, claims: dict[str, object]
) -> None:
    assert await refused(verifier, local_issuer.token(claims=claims)) is TokenProblem.WRONG_KIND


async def test_an_id_token_of_the_provider_for_the_same_project_is_refused(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    """Zitadel v4.19.2's ID tokens, a login's and a token exchange's, as issued: same issuer, keys, ``typ``
    and organization claim, and the project among their audiences. Only what makes them ID tokens tells them
    from an access token."""
    id_token = {
        "aud": ["394880619080908804", AUDIENCE],
        "azp": "394880619080908804",
        "client_id": "394880619080908804",
        "amr": ["pwd"],
        "at_hash": "x7Kq2Lw9Rt4Vb8Zm1Hc5Jd",
        "auth_time": now(),
        "nonce": "a1b2c3d4",
        "sid": "394880668523298820",
        "jti": None,
        "nbf": None,
    }
    assert await refused(verifier, local_issuer.token(claims=id_token)) is TokenProblem.WRONG_KIND
    exchanged = {
        "aud": [AUDIENCE, "394880531453509636"],
        "azp": "394880531453509636",
        "jti": None,
        "nbf": None,
    }
    assert await refused(verifier, local_issuer.token(claims=exchanged)) is TokenProblem.WRONG_KIND


async def test_a_refusal_says_what_was_wrong_in_a_word(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    with pytest.raises(InvalidTokenError) as caught:
        await verifier.verify(local_issuer.token(claims={"exp": now() - 600}))
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
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    assert await refused(verifier, unsigned({"alg": "none", "kid": "key-1"})) is TokenProblem.ALGORITHM
    assert local_issuer.fetches == 0


async def test_an_hmac_token_keyed_with_the_public_key_is_refused(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    secret = OctKey.import_key(local_issuer.published())
    forged = jwt.encode(
        {"alg": "HS256", "kid": "key-1"}, {"iss": ISSUER, "aud": AUDIENCE, "sub": "1"}, secret
    )
    assert await refused(verifier, forged) is TokenProblem.ALGORITHM


async def test_an_algorithm_the_policy_does_not_allow_is_refused(
    local_issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    only_es256 = TokenPolicy(
        issuer=ISSUER, audience=AUDIENCE, keys_url=policy.keys_url, algorithms=frozenset({"ES256"})
    )
    assert await refused(TokenVerifier(only_es256), local_issuer.token()) is TokenProblem.ALGORITHM


async def test_a_tampered_token_fails_its_signature(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    header, _, signature = local_issuer.token().split(".")
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "root", "exp": now() + 60, ORGANIZATION_CLAIM: "1"}
    assert await refused(verifier, f"{header}.{segment(claims)}.{signature}") is TokenProblem.SIGNATURE


async def test_a_signed_payload_that_is_not_a_json_object_is_malformed(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    array = jws.serialize_compact({"alg": "RS256", "kid": "key-1"}, b"[1, 2, 3]", local_issuer.signing_key())
    assert await refused(verifier, array) is TokenProblem.MALFORMED


async def test_a_signed_payload_that_is_not_json_is_malformed(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    token = jws.serialize_compact({"alg": "RS256", "kid": "key-1"}, b"not json", local_issuer.signing_key())
    assert await refused(verifier, token) is TokenProblem.MALFORMED


@pytest.mark.parametrize(
    "token",
    ["", "not-a-token", "a.b", "a.b.c.d", "é.e30.", "!!!.e30.sig", segment({"alg": "RS256"}) + ".e30.sig"],
    ids=["empty", "word", "two-parts", "four-parts", "non-ascii", "bad-base64", "no-kid"],
)
async def test_text_that_is_not_a_signed_jwt_is_malformed(
    local_issuer: LocalIssuer, verifier: TokenVerifier, token: str
) -> None:
    assert await refused(verifier, token) is TokenProblem.MALFORMED
    assert local_issuer.fetches == 0


async def test_a_huge_token_is_refused_unread(local_issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    huge = local_issuer.token(claims={"padding": "x" * MAX_TOKEN_LENGTH})
    assert await refused(verifier, huge) is TokenProblem.MALFORMED
    assert local_issuer.fetches == 0


async def test_a_megabyte_header_is_refused_unread(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    header = segment({"alg": "RS256", "kid": "key-1", "padding": "x" * 1_000_000})
    assert await refused(verifier, f"{header}.{segment({})}.c2lnbmF0dXJl") is TokenProblem.MALFORMED
    assert local_issuer.fetches == 0


@pytest.mark.parametrize("pointer", ["jku", "x5u"])
async def test_keys_a_token_points_to_are_never_fetched(verifier: TokenVerifier, pointer: str) -> None:
    async with LocalIssuer.started() as impostor:
        header = {"alg": "RS256", "kid": "key-1", pointer: impostor.keys_url}
        forged = jwt.encode(header, {"iss": ISSUER, "aud": AUDIENCE}, impostor.signing_key())
        assert await refused(verifier, forged) is TokenProblem.SIGNATURE
    assert impostor.fetches == 0


async def test_a_key_a_token_carries_is_never_trusted(
    local_issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    own = RSAKey.generate_key(2048, {"kid": "key-1"}, private=True)
    header = {"alg": "RS256", "kid": "key-1", "jwk": own.as_dict(private=False)}
    forged = jwt.encode(header, TOKEN_CLAIMS.validate_json(payload_of(local_issuer.token())), own)
    assert await refused(verifier, forged) is TokenProblem.SIGNATURE


async def test_a_published_key_on_another_curve_than_the_algorithm_s_is_refused() -> None:
    own = ECKey.generate_key("P-256", {"kid": "ec"}, private=True)
    other_curve = ECKey.generate_key("P-384", {"kid": "ec"}, private=True)

    async def published(url: str) -> bytes:
        del url
        return json.dumps({"keys": [other_curve.as_dict(private=False)]}).encode()

    es256 = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, algorithms=frozenset({"ES256"}))
    verifier = TokenVerifier(es256, keys=PublishedKeys(es256, fetch=published))
    token = jwt.encode({"alg": "ES256", "kid": "ec"}, {"iss": ISSUER}, own)
    assert await refused(verifier, token) is TokenProblem.ALGORITHM


@pytest.mark.parametrize(
    "crit",
    [1, None, True, 1.5, [1], [[1]], [], ["exp"], "typ"],
    ids=[
        "number",
        "null",
        "true",
        "fraction",
        "number-in-list",
        "nested-list",
        "empty",
        "absent-name",
        "text",
    ],
)
async def test_a_critical_header_list_that_is_not_one_is_malformed(
    local_issuer: LocalIssuer, verifier: TokenVerifier, crit: object
) -> None:
    header: dict[str, object] = {"alg": "RS256", "kid": "key-1", "crit": crit}
    token = signed(local_issuer.signing_key(), header, payload_of(local_issuer.token()))
    assert await refused(verifier, token) is TokenProblem.MALFORMED


@given(st.sampled_from(REGISTERED_HEADERS), JSON_VALUES)
async def test_no_value_of_a_registered_header_makes_the_verifier_fail_otherwise_than_by_refusing_it(
    name: str, value: object
) -> None:
    token = signed(OFFLINE.signing_key(), {"alg": "RS256", "kid": "key-1", name: value}, b"{}")
    with pytest.raises(InvalidTokenError):
        await offline_verifier().verify(token)


async def test_an_algorithm_the_published_key_does_not_fit_is_refused(
    local_issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    _, payload, signature = local_issuer.token().split(".")
    retitled = f"{segment({'alg': 'ES256', 'kid': 'key-1'})}.{payload}.{signature}"
    assert await refused(rsa_or_ec(policy), retitled) is TokenProblem.ALGORITHM


@given(st.text(max_size=400))
@example("0/..")  # standard Base64's alphabet: Python 3.15 warns of it in URL-safe data
@example("0+..")
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


# ── Policy ──────────────────────────────────────────────────────────────────────


def test_the_policy_comes_from_the_settings() -> None:
    settings = Settings(
        environment="local",
        database_url=SecretStr("postgresql+psycopg://identity:secret@localhost/identity"),
        token_issuer=ISSUER,
        token_audience=AUDIENCE,
        token_jwks_url="http://localhost/keys",
        token_leeway_seconds=10,
    )
    expected = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url="http://localhost/keys", leeway=10)
    assert TokenPolicy.of_settings(settings) == expected


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
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, leeway=-1), "leeway"),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, leeway=301), "leeway"),
        (
            lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, keys_max_age=599.0),
            "keys_max_age",
        ),
        (
            lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, keys_max_age=float("inf")),
            "keys_max_age",
        ),
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
    with pytest.raises(InvalidOptionError, match=f"token option {option} must be") as caught:
        build()
    assert caught.value.component == "token"
