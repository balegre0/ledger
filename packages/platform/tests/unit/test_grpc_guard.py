import hashlib
import hmac
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr

from ledger_kernel.errors import ErrorKind
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.grpc.guard import (
    MIN_TOKEN_LENGTH,
    CallerPolicy,
    CallerTokenRefusedError,
    CallerTokens,
    CallerUnboundError,
    CallerUnknownError,
    MethodNotAllowedError,
    calling_as,
    current_caller,
)
from ledger_platform.grpc.metadata import (
    AUTHORIZATION_KEY,
    CALL_ID_KEY,
    TRACEPARENT_KEY,
    bearer_token,
    call_id_of,
    call_metadata,
    metadata_value,
)
lazy from ledger_platform.observability.tracing import Tracing

API_TOKEN: Final = "api-token-0123456789abcdefghijklmnopqrstuv"
API_PREVIOUS: Final = "api-previous-0123456789abcdefghijklmnopq"
WORKER_TOKEN: Final = "worker-token-0123456789abcdefghijklmnopq"
OTHER_TOKEN: Final = "other-token-0123456789abcdefghijklmnopqrs"
IMPORT: Final = "/ledger.signer.v1.SignerService/ImportCertificate"
SIGN: Final = "/ledger.signer.v1.SignerService/SignXml"
DESCRIBE: Final = "/ledger.signer.v1.SignerService/DescribeCertificates"


SIGNING: Final = frozenset({SIGN})


def policy(name: str, *tokens: str, methods: frozenset[str] = SIGNING) -> CallerPolicy:
    return CallerPolicy(name=name, tokens=tuple(SecretStr(token) for token in tokens), methods=methods)


def callers() -> CallerTokens:
    return CallerTokens(
        [
            policy("api", API_TOKEN, API_PREVIOUS, methods=frozenset({IMPORT, DESCRIBE})),
            policy("worker", WORKER_TOKEN, methods=frozenset({SIGN, DESCRIBE})),
        ]
    )


def bearer(token: str) -> str:
    return f"Bearer {token}"


def test_a_known_token_names_its_caller() -> None:
    tokens = callers()
    assert tokens.caller_of(bearer(API_TOKEN)) == "api"
    assert tokens.caller_of(bearer(WORKER_TOKEN)) == "worker"


def test_a_previous_token_still_names_its_caller() -> None:
    assert callers().caller_of(bearer(API_PREVIOUS)) == "api"


def test_the_scheme_is_case_insensitive() -> None:
    assert callers().caller_of(f"bearer {WORKER_TOKEN}") == "worker"


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic x",
        f"Basic {API_TOKEN}",
        API_TOKEN,
        f"Bearer  {API_TOKEN}",
        f"Bearer {API_TOKEN} ",
        f"Bearer {API_TOKEN[:-1]}",
        bearer(OTHER_TOKEN),
        "Bearer \udcff",
    ],
    ids=[
        "none",
        "empty",
        "scheme-alone",
        "scheme-and-space",
        "basic",
        "basic-with-a-known-token",
        "token-without-scheme",
        "two-spaces",
        "trailing-space",
        "a-character-short",
        "another-token",
        "not-encodable",
    ],
)
def test_an_unknown_or_malformed_authorization_names_nobody(authorization: str | None) -> None:
    assert callers().caller_of(authorization) is None


def test_tokens_are_compared_as_digests_against_every_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every stored token is compared, the match too: how long it takes says nothing of which matched."""
    tokens = callers()
    compared: list[tuple[bytes, bytes]] = []
    compare = hmac.compare_digest

    def recording(stored: bytes, presented: bytes) -> bool:
        compared.append((stored, presented))
        return compare(stored, presented)

    monkeypatch.setattr(hmac, "compare_digest", recording)
    assert tokens.caller_of(bearer(API_TOKEN)) == "api"
    presented = hashlib.sha256(API_TOKEN.encode()).digest()
    assert {stored for stored, _ in compared} == {
        hashlib.sha256(token.encode()).digest() for token in (API_TOKEN, API_PREVIOUS, WORKER_TOKEN)
    }
    assert {sent for _, sent in compared} == {presented}
    assert len(compared) == 3


def test_neither_the_guard_nor_its_policies_show_a_token() -> None:
    tokens = callers()
    shown = repr(tokens) + repr(policy("api", API_TOKEN)) + str(policy("api", API_TOKEN))
    for token in (API_TOKEN, API_PREVIOUS, WORKER_TOKEN):
        assert token not in shown
    assert repr(tokens) == "CallerTokens(callers=('api', 'worker'))"


def test_a_caller_may_call_only_its_methods() -> None:
    tokens = callers()
    assert tokens.allows("api", IMPORT)
    assert tokens.allows("worker", SIGN)
    assert tokens.allows("worker", DESCRIBE)
    assert not tokens.allows("api", SIGN)
    assert not tokens.allows("worker", IMPORT)
    assert not tokens.allows("receiver", SIGN)


def refusal(*policies: CallerPolicy) -> CallerTokenRefusedError:
    with pytest.raises(CallerTokenRefusedError) as caught:
        CallerTokens(policies)
    return caught.value


@pytest.mark.parametrize("token", ["", "x", "y" * (MIN_TOKEN_LENGTH - 1)], ids=["empty", "one", "one-short"])
def test_a_short_token_is_refused(token: str) -> None:
    error = refusal(policy("api", API_TOKEN, token), policy("worker", WORKER_TOKEN))
    assert str(error) == f"gRPC caller api has a token shorter than {MIN_TOKEN_LENGTH} characters"
    assert error.code == "grpc_caller_token_refused"
    assert error.kind is ErrorKind.INTERNAL


@pytest.mark.parametrize(
    "token",
    [
        "with a space " + "x" * MIN_TOKEN_LENGTH,
        "tab\t" + "x" * MIN_TOKEN_LENGTH,
        "accent-é" + "x" * MIN_TOKEN_LENGTH,
        "x" * MIN_TOKEN_LENGTH + "\n",
    ],
    ids=["space", "tab", "non-ascii", "newline"],
)
def test_a_token_of_other_than_visible_ascii_is_refused(token: str) -> None:
    error = refusal(policy("worker", token))
    assert str(error) == "gRPC caller worker has a token of other than visible ASCII characters"
    assert token not in str(error)


@given(
    token=st.text(alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E), min_size=MIN_TOKEN_LENGTH)
)
def test_every_long_enough_visible_ascii_token_is_taken(token: str) -> None:
    tokens = CallerTokens([policy("worker", token)])
    assert tokens.caller_of(bearer(token)) == "worker"


def test_a_token_shared_by_two_callers_is_refused() -> None:
    error = refusal(policy("api", API_TOKEN), policy("worker", WORKER_TOKEN, API_TOKEN))
    assert str(error) == "gRPC caller worker has a token another caller has too"
    assert API_TOKEN not in str(error)


def test_one_caller_may_hold_a_token_twice_during_a_rotation() -> None:
    tokens = CallerTokens([policy("api", API_TOKEN, API_TOKEN)])
    assert tokens.caller_of(bearer(API_TOKEN)) == "api"


def test_a_caller_without_a_token_is_refused() -> None:
    error = refusal(policy("api", API_TOKEN), policy("receiver"))
    assert str(error) == "gRPC caller receiver has no token"


def test_a_guard_without_callers_is_refused() -> None:
    with pytest.raises(InvalidOptionError, match=r"^gRPC guard option callers must be at least one caller, "):
        CallerTokens([])


@pytest.mark.parametrize("name", ["", "Api", "api-2", "1api"], ids=["empty", "upper", "dash", "digit-first"])
def test_a_caller_s_name_is_snake_case(name: str) -> None:
    with pytest.raises(InvalidOptionError, match=r"^gRPC guard option name must be snake_case, "):
        CallerTokens([policy(name, API_TOKEN)])


def test_a_caller_named_twice_is_refused() -> None:
    with pytest.raises(InvalidOptionError, match=r"^gRPC guard option name must be one per caller, not "):
        CallerTokens([policy("api", API_TOKEN), policy("api", WORKER_TOKEN)])


def test_the_refusals_have_stable_codes_and_kinds() -> None:
    unknown, denied = CallerUnknownError(), MethodNotAllowedError()
    assert (unknown.kind, unknown.code, str(unknown)) == (
        ErrorKind.UNAUTHENTICATED,
        "signer_caller_unknown",
        "the caller is not known",
    )
    assert (denied.kind, denied.code, str(denied)) == (
        ErrorKind.FORBIDDEN,
        "signer_method_not_allowed",
        "the caller may not call this method",
    )


def test_outside_a_guarded_call_there_is_no_caller() -> None:
    with pytest.raises(CallerUnboundError, match=r"^no gRPC caller is bound: ") as caught:
        current_caller()
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "grpc_caller_unbound")


def test_calling_as_binds_the_caller_for_the_block_and_restores_the_outer_one() -> None:
    with calling_as("api"):
        with calling_as("worker"):
            assert current_caller() == "worker"
        assert current_caller() == "api"
    with pytest.raises(CallerUnboundError):
        current_caller()


def test_call_metadata_carries_the_token_the_call_id_and_the_trace(tracing: Tracing) -> None:
    with tracing.tracer.start_as_current_span("caller") as span:
        sent = call_metadata(SecretStr(WORKER_TOKEN), call_id="req_1")
        context = span.get_span_context()
    assert sent == (
        (AUTHORIZATION_KEY, bearer(WORKER_TOKEN)),
        (CALL_ID_KEY, "req_1"),
        (TRACEPARENT_KEY, f"00-{context.trace_id:032x}-{context.span_id:016x}-{context.trace_flags:02x}"),
    )
    assert callers().caller_of(metadata_value(sent, AUTHORIZATION_KEY)) == "worker"


def test_call_metadata_outside_a_trace_carries_none() -> None:
    assert call_metadata(SecretStr(WORKER_TOKEN), call_id="req_1") == (
        ("authorization", bearer(WORKER_TOKEN)),
        ("ledger-call-id", "req_1"),
    )


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ((), None),
        ((("other", "x"),), None),
        ((("key", "x"),), "x"),
        ((("key", "x"), ("key", "y")), None),
        ((("key", b"x"),), None),
    ],
    ids=["empty", "absent", "present", "repeated", "binary"],
)
def test_a_metadata_value_is_one_text_value_or_none(
    metadata: tuple[tuple[str, str | bytes], ...], expected: str | None
) -> None:
    assert metadata_value(metadata, "key") == expected


@pytest.mark.parametrize(
    ("authorization", "expected"),
    [(None, None), ("Bearer abc", "abc"), ("BEARER abc", "abc"), ("Bearer", None), ("Token abc", None)],
)
def test_the_bearer_token_is_what_follows_the_scheme(authorization: str | None, expected: str | None) -> None:
    assert bearer_token(authorization) == expected


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        (None, None),
        ("req_01HZX-9.a:b", "req_01HZX-9.a:b"),
        ("", None),
        ("x" * 128, "x" * 128),
        ("x" * 129, None),
        ("has space", None),
        ("new\nline", None),
    ],
    ids=["absent", "usual", "empty", "longest", "too-long", "space", "newline"],
)
def test_a_call_id_is_taken_only_when_it_has_the_shape_of_one(sent: str | None, expected: str | None) -> None:
    metadata = () if sent is None else ((CALL_ID_KEY, sent),)
    assert call_id_of(metadata) == expected
