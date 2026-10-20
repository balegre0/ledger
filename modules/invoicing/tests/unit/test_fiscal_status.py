"""The fiscal mirror's value (§7.5): a channel's status as invoicing keeps it, bounded and read-only.

What it is checked against comes from our own code (a channel, through ``fiscal``): a value out of bounds is a
defect (``InvalidValueError``). What decides the document is whether a channel accepted it.
"""

from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Callable

import pytest

from ledger_invoicing.domain.fiscal import (
    ACCEPTANCES,
    MAX_MESSAGES,
    Applied,
    AuthorityMessage,
    FiscalStatus,
    FiscalStatusName,
    accepted,
)
from ledger_kernel.errors import InvalidValueError
lazy from ledger_invoicing.domain.fiscal import Detail

NOW: Final = datetime(2026, 10, 20, 12, tzinfo=UTC)
MESSAGE: Final = AuthorityMessage(code="1", message="Aceptado con observaciones: Juan Perez", lang="es")
PENDING: Final = FiscalStatus(
    channel="do-ecf-v1",
    environment="testecf",
    status=FiscalStatusName.PENDING,
    version=1,
    authority_number=None,
    messages=(),
    regime="do",
    details={"encf": "E310000000001", "nested": {"qr": ["a", 1, True, None]}},
    updated_at=NOW,
)


def test_the_statuses_are_the_kit_s_words_and_two_of_them_are_acceptances() -> None:
    assert [status.value for status in FiscalStatusName] == [
        "pending",
        "accepted",
        "accepted_with_observations",
        "rejected",
        "voided",
    ]
    assert {FiscalStatusName.ACCEPTED, FiscalStatusName.ACCEPTED_WITH_OBSERVATIONS} == ACCEPTANCES
    assert [outcome.value for outcome in Applied] == ["applied", "stale"]


def test_a_document_is_accepted_once_any_channel_says_so() -> None:
    assert not accepted(())
    assert not accepted([PENDING, replace(PENDING, channel="do-other-v1", status=FiscalStatusName.REJECTED)])
    for status in ACCEPTANCES:
        assert accepted([PENDING, replace(PENDING, channel="do-other-v2", status=status)])


def test_details_are_a_read_only_copy_of_what_the_channel_said() -> None:
    sent: dict[str, object] = {"encf": "E310000000001", "nested": {"qr": ["a"]}}
    qr: list[Detail] = ["a"]
    nested: dict[str, Detail] = {"qr": qr}
    details: dict[str, Detail] = {"encf": "E310000000001", "nested": nested}
    kept = replace(PENDING, details=details)
    details["encf"] = "changed"
    nested["more"] = "x"
    qr.append("b")
    assert dict(kept.details) == sent
    assert isinstance(kept.details, MappingProxyType)


def test_its_repr_shows_its_channel_status_and_version_only() -> None:
    shown = repr(replace(PENDING, messages=(MESSAGE,), authority_number="E310000000001"))
    assert shown == "FiscalStatus(channel='do-ecf-v1', status='pending', version=1)"
    assert "Juan" not in repr(MESSAGE)


type Change = Callable[[FiscalStatus], FiscalStatus]


OUT_OF_BOUNDS: Final[list[tuple[Change, str]]] = [
    (lambda kept: replace(kept, channel="ecf"), r"^fiscal_status\.channel must be a channel's key"),
    (
        lambda kept: replace(kept, environment=""),
        r"^fiscal_status\.environment must be a lower-case word",
    ),
    (lambda kept: replace(kept, environment="Test"), r"^fiscal_status\.environment must be"),
    (lambda kept: replace(kept, environment="e" * 33), r"^fiscal_status\.environment must be"),
    (lambda kept: replace(kept, version=0), r"^fiscal_status\.version must be 1 or more$"),
    (
        lambda kept: replace(kept, authority_number=""),
        r"^fiscal_status\.authority_number must be 1 to 64",
    ),
    (lambda kept: replace(kept, authority_number="E" * 65), r"^fiscal_status\.authority_number must be"),
    (
        lambda kept: replace(kept, messages=(MESSAGE,) * (MAX_MESSAGES + 1)),
        r"^fiscal_status\.messages must be at most 20$",
    ),
    (lambda kept: replace(kept, regime="dom"), r"^fiscal_status\.regime must be a regime's key$"),
    (
        lambda kept: replace(kept, updated_at=NOW.replace(tzinfo=None)),
        r"^fiscal_status\.updated_at must be an aware datetime$",
    ),
]
"""Each way out of a status's bounds, and how its refusal reads."""


@pytest.mark.parametrize(("change", "message"), OUT_OF_BOUNDS)
def test_a_status_out_of_its_bounds_is_a_defect(change: Change, message: str) -> None:
    with pytest.raises(InvalidValueError, match=message):
        change(PENDING)


def test_a_subdivision_s_regime_and_a_message_s_bounds() -> None:
    assert replace(PENDING, regime="us-ca").regime == "us-ca"
    with pytest.raises(InvalidValueError, match=r"^fiscal_status\.messages\.code must be 1 to 64"):
        AuthorityMessage(code="", message="x", lang="es")
    with pytest.raises(InvalidValueError, match=r"^fiscal_status\.messages\.message must be at most 500"):
        AuthorityMessage(code="1", message="x" * 501, lang="es")
    with pytest.raises(InvalidValueError, match=r"^fiscal_status\.messages\.lang must be a language tag"):
        AuthorityMessage(code="1", message="", lang="")
