from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import override

import pytest
from google.protobuf.struct_pb2 import Struct
from hypothesis import given
from hypothesis import strategies as st

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import (
    MAX_ATTRIBUTE_LENGTH,
    Envelope,
    MalformedEventError,
    UnexpectedEventError,
)
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
OFFSETS = st.integers(min_value=-86_399, max_value=86_399).map(
    lambda seconds: timezone(timedelta(seconds=seconds))
)
VISIBLE_ASCII = st.characters(min_codepoint=ord("!"), max_codepoint=ord("~"))
IDENTIFIERS = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,9}", fullmatch=True)
NOISE = st.text(st.characters(max_codepoint=0x200) | st.sampled_from([chr(0xD800), chr(0xDFFF)]), max_size=40)
"""Controls, spaces, non-ASCII and lone surrogates among ordinary characters."""
TENANT = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
BAD_TRACEPARENTS = {
    "empty": "",
    "word": "garbage",
    "uppercase": TRACEPARENT.upper(),
    "newline": TRACEPARENT + "\n",
    "suffix": TRACEPARENT + "-extra",
    "no-flags": TRACEPARENT.removesuffix("-01"),
    "other-version": "01" + TRACEPARENT.removeprefix("00"),
    "zero-trace-id": "00-" + "0" * 32 + "-b7ad6b7169203331-01",
    "zero-parent-id": "00-0af7651916cd43dd8448eb211c80319c-" + "0" * 16 + "-01",
    "huge": "0" * 100_000,
}
BAD_TYPES = {
    "empty": "",
    "no-package": "Money",
    "leading-dot": ".ledger.Money",
    "trailing-dot": "ledger.Money.",
    "empty-segment": "ledger..Money",
    "digit-first": "ledger.1Money",
    "hyphen": "ledger-common.Money",
    "space": "ledger.common Money",
    "trailing-newline": "ledger.common.v1.Money\n",
    "crlf-injection": "ledger.Money\r\nNats-Expected-Last-Sequence: 0",
    "nul": "ledger.Money\x00",
    "lone-surrogate": "ledger.Money\ud800",
    "non-ascii": "ledger.common.v1.M\u00f6ney",
    "too-long": "ledger." + "x" * MAX_ATTRIBUTE_LENGTH,
    "huge": "ledger." + "x" * 100_000,
}
BAD_SOURCES = {
    "empty": "",
    "inner-space": "invoicing service",
    "leading-space": " invoicing",
    "trailing-space": "invoicing ",
    "tab": "invoicing\t",
    "cr": "invoicing\r",
    "lf": "invoicing\n",
    "crlf-injection": "invoicing\r\nNats-Expected-Last-Sequence: 0",
    "nul": "invoicing\x00",
    "delete": "invoicing\x7f",
    "lone-surrogate": "invoicing\ud800",
    "non-ascii": "facturaci\u00f3n",
    "full-width": "\uff49\uff4e\uff56",
    "too-long": "x" * (MAX_ATTRIBUTE_LENGTH + 1),
    "huge": "x" * 100_000,
}


class Floating(tzinfo):
    """A time zone that knows no offset: a ``datetime`` carrying it is not aware."""

    @override
    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        del dt
        return None

    @override
    def dst(self, dt: datetime | None) -> timedelta | None:
        del dt
        return None

    @override
    def tzname(self, dt: datetime | None) -> str | None:
        del dt
        return None


def can_be_published(envelope: Envelope) -> bool:
    """Whether every header of ``envelope`` is visible ASCII: no framing character, no space to trim."""
    return all("!" <= character <= "~" for pair in envelope.headers().items() for character in "".join(pair))


def wrapped(tenant: Tenant | None = TENANT) -> Envelope:
    return Envelope.wrap(
        Money(amount="10.50", currency="DOP"), source="invoicing", tenant=tenant, clock=lambda: NOW
    )


def test_wrapping_records_the_payload_type_and_cloudevents_attributes() -> None:
    envelope = wrapped()
    assert envelope.type == "ledger.common.v1.Money"
    assert envelope.id.version == 7
    assert envelope.headers() == {
        "ce-specversion": "1.0",
        "ce-id": str(envelope.id),
        "ce-type": "ledger.common.v1.Money",
        "ce-source": "invoicing",
        "ce-time": "2026-10-11T12:00:00+00:00",
        "ce-organizationid": str(TENANT.organization),
        "ce-subject": str(TENANT.company),
        "content-type": "application/protobuf",
        "Nats-Msg-Id": str(envelope.id),
    }


@pytest.mark.parametrize("tenant", [TENANT, None], ids=["tenant", "platform-wide"])
@pytest.mark.parametrize("traceparent", [TRACEPARENT, None], ids=["traced", "untraced"])
def test_an_envelope_survives_the_wire(tenant: Tenant | None, traceparent: str | None) -> None:
    original = replace(wrapped(tenant), traceparent=traceparent)
    received = Envelope.from_headers(original.headers(), original.data)
    assert received == original
    assert received.unwrap(Money) == Money(amount="10.50", currency="DOP")


@given(st.text(VISIBLE_ASCII, min_size=1, max_size=MAX_ATTRIBUTE_LENGTH))
def test_any_source_survives_the_wire(source: str) -> None:
    original = replace(wrapped(), source=source)
    assert Envelope.from_headers(original.headers(), original.data) == original


@given(st.lists(IDENTIFIERS, min_size=2, max_size=8).map(".".join))
def test_any_dotted_name_is_a_type_that_survives_the_wire(name: str) -> None:
    original = replace(wrapped(), type=name)
    assert Envelope.from_headers(original.headers(), original.data) == original


def test_the_longest_type_and_source_survive_the_wire() -> None:
    original = replace(
        wrapped(), type="t." + "x" * (MAX_ATTRIBUTE_LENGTH - 2), source="s" * MAX_ATTRIBUTE_LENGTH
    )
    assert Envelope.from_headers(original.headers(), original.data) == original


DOTTED_NOISE = st.lists(NOISE, min_size=2, max_size=3).map(".".join)


@given(
    st.one_of(st.just("ledger.common.v1.Money"), NOISE, DOTTED_NOISE), st.one_of(st.just("invoicing"), NOISE)
)
def test_an_envelope_that_exists_can_be_published(type_name: str, source: str) -> None:
    """Whatever text is offered, no header of an envelope that exists can break the NATS framing."""
    with suppress(MalformedEventError):
        assert can_be_published(replace(wrapped(), type=type_name, source=source))


@given(
    st.datetimes(
        min_value=NOW.replace(year=1900, tzinfo=None),  # naive: hypothesis takes the zone from the strategy
        max_value=NOW.replace(year=2100, tzinfo=None),
        timezones=OFFSETS,
    )
)
def test_any_aware_time_survives_the_wire(time: datetime) -> None:
    original = replace(wrapped(), time=time)
    received = Envelope.from_headers(original.headers(), original.data)
    assert received == original
    assert received.time.utcoffset() == original.time.utcoffset()
    assert can_be_published(original)


def test_a_time_written_with_z_is_read_as_utc() -> None:
    headers = wrapped().headers() | {"ce-time": "2026-10-11T12:00:00Z"}
    assert Envelope.from_headers(headers, b"").time == NOW


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"ce-specversion": "0.3"}, "ce-specversion"),
        ({"ce-id": "not-an-id"}, "ce-id"),
        ({"ce-type": ""}, "ce-type"),
        ({"ce-source": ""}, "ce-source"),
        ({"ce-time": "yesterday"}, "ce-time"),
        ({"ce-time": "2026-10-11T12:00:00"}, "ce-time"),
        ({"ce-subject": "not-an-id"}, "ce-subject"),
        ({"ce-organizationid": "not-an-id"}, "ce-organizationid"),
        ({"content-type": "application/json"}, "content-type"),
        ({"content-type": "application/protobuf; charset=utf-8"}, "content-type"),
    ],
)
def test_a_malformed_header_is_named(change: dict[str, str], field: str) -> None:
    with pytest.raises(MalformedEventError, match=field) as caught:
        Envelope.from_headers(wrapped().headers() | change, b"")
    assert caught.value.field == field


@pytest.mark.parametrize(
    "missing", ["ce-specversion", "ce-id", "ce-type", "ce-source", "ce-time", "content-type"]
)
def test_a_missing_header_is_named(missing: str) -> None:
    headers = wrapped().headers()
    del headers[missing]
    with pytest.raises(MalformedEventError, match=missing) as caught:
        Envelope.from_headers(headers, b"")
    assert caught.value.field == missing


@pytest.mark.parametrize("missing", ["ce-organizationid", "ce-subject"])
def test_half_a_tenant_is_malformed(missing: str) -> None:
    headers = wrapped().headers()
    del headers[missing]
    with pytest.raises(MalformedEventError, match=missing):
        Envelope.from_headers(headers, b"")


@pytest.mark.parametrize("source", BAD_SOURCES.values(), ids=BAD_SOURCES.keys())
def test_a_source_that_cannot_be_a_header_value_is_refused(source: str) -> None:
    with pytest.raises(MalformedEventError, match="ce-source"):
        replace(wrapped(), source=source)
    with pytest.raises(MalformedEventError, match="ce-source"):
        Envelope.from_headers(wrapped().headers() | {"ce-source": source}, b"")


@pytest.mark.parametrize("type_name", BAD_TYPES.values(), ids=BAD_TYPES.keys())
def test_a_type_that_is_not_a_protobuf_full_name_is_refused(type_name: str) -> None:
    with pytest.raises(MalformedEventError, match="ce-type"):
        replace(wrapped(), type=type_name)
    with pytest.raises(MalformedEventError, match="ce-type"):
        Envelope.from_headers(wrapped().headers() | {"ce-type": type_name}, b"")


def test_an_invalid_traceparent_is_dropped_not_fatal() -> None:
    headers = wrapped().headers() | {"traceparent": "garbage"}
    assert Envelope.from_headers(headers, b"").traceparent is None


@pytest.mark.parametrize("traceparent", BAD_TRACEPARENTS.values(), ids=BAD_TRACEPARENTS.keys())
def test_only_a_valid_traceparent_is_kept(traceparent: str) -> None:
    headers = wrapped().headers() | {"traceparent": traceparent}
    assert Envelope.from_headers(headers, b"").traceparent is None


@pytest.mark.parametrize(
    "time", [NOW.replace(tzinfo=None), NOW.replace(tzinfo=Floating())], ids=["naive", "no-offset"]
)
def test_a_time_that_is_not_aware_cannot_be_built(time: datetime) -> None:
    with pytest.raises(MalformedEventError, match="ce-time"):
        replace(wrapped(), time=time)


@pytest.mark.parametrize("traceparent", BAD_TRACEPARENTS.values(), ids=BAD_TRACEPARENTS.keys())
def test_an_invalid_traceparent_cannot_be_built(traceparent: str) -> None:
    with pytest.raises(MalformedEventError, match="traceparent"):
        replace(wrapped(), traceparent=traceparent)


def test_direct_construction_is_checked_too() -> None:
    with pytest.raises(MalformedEventError, match="ce-source"):
        Envelope(
            id=new_id(),
            type="ledger.common.v1.Money",
            source=BAD_SOURCES["crlf-injection"],
            tenant=None,
            time=NOW,
            data=b"",
        )


def test_unwrapping_checks_the_type_and_the_bytes() -> None:
    envelope = wrapped()
    with pytest.raises(UnexpectedEventError, match=r"expected ledger\.common\.v1\.Money"):
        replace(envelope, type="ledger.other.v1.Thing").unwrap(Money)
    with pytest.raises(MalformedEventError, match="data"):
        replace(envelope, data=b"\x0a\xff").unwrap(Money)


@pytest.mark.parametrize(
    "data", [b"\x0a\xff", b"\x0a\x01\xff", b"\x0a\x05ab"], ids=["varint", "utf8", "truncated"]
)
def test_an_unreadable_payload_is_malformed_not_a_crash(data: bytes) -> None:
    with pytest.raises(MalformedEventError, match="data") as caught:
        replace(wrapped(), data=data).unwrap(Money)
    assert caught.value.field == "data"


def test_the_payload_bytes_do_not_depend_on_map_insertion_order() -> None:
    """The same message must always produce the same bytes (deduplication, signatures, golden files)."""
    keys = [f"key{index}" for index in range(40)]

    def structure(order: list[str]) -> Struct:
        message = Struct()
        for key in order:
            message.fields[key].number_value = 1.0
        return message

    forward = Envelope.wrap(structure(keys), source="invoicing", tenant=None)
    backward = Envelope.wrap(structure(keys[::-1]), source="invoicing", tenant=None)
    assert forward.data == backward.data
    assert forward.unwrap(Struct) == backward.unwrap(Struct)


@pytest.mark.parametrize(
    "header",
    [
        "ce-specversion",
        "ce-id",
        "ce-type",
        "ce-source",
        "ce-time",
        "ce-subject",
        "ce-organizationid",
        "content-type",
    ],
)
def test_huge_header_values_are_not_echoed(header: str) -> None:
    with pytest.raises(MalformedEventError) as caught:
        Envelope.from_headers(wrapped().headers() | {header: "x" * 100_000}, b"")
    assert len(str(caught.value)) < 120


def test_a_huge_traceparent_is_not_echoed() -> None:
    with pytest.raises(MalformedEventError) as caught:
        replace(wrapped(), traceparent="x" * 100_000)
    assert len(str(caught.value)) < 120


def test_the_longest_unexpected_event_type_is_not_echoed() -> None:
    with pytest.raises(UnexpectedEventError) as caught:
        replace(wrapped(), type="t." + "x" * (MAX_ATTRIBUTE_LENGTH - 2)).unwrap(Money)
    assert len(str(caught.value)) < 120
