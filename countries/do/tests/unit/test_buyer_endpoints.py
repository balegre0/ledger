from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Final
lazy from collections.abc import Sequence

import pytest

from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import (
    BuyerEndpointRefusedError,
    Endpoint,
    endpoint,
    pinned,
    public,
    system_resolve,
)

PUBLIC: Final = IPv4Address("93.184.215.14")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://buyer.example.com", Endpoint("buyer.example.com", 443, "")),
        ("https://Buyer.Example.COM/fe/", Endpoint("buyer.example.com", 443, "/fe")),
        (
            "https://ecf.dgii.gov.do/testecf/emisorreceptor",
            Endpoint("ecf.dgii.gov.do", 443, "/testecf/emisorreceptor"),
        ),
        ("https://buyer.example.com:8443/a/b", Endpoint("buyer.example.com", 8443, "/a/b")),
        ("https://xn--fiscalizacin-xkb.example.do/x", Endpoint("xn--fiscalizacin-xkb.example.do", 443, "/x")),
    ],
)
def test_a_public_https_name_with_a_path_is_an_endpoint(url: str, expected: Endpoint) -> None:
    assert endpoint(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://buyer.example.com",
        "ftp://buyer.example.com",
        "buyer.example.com/fe",
        "https://user@buyer.example.com",
        "https://user:secret@buyer.example.com",
        "https://@buyer.example.com",
        "https://127.0.0.1",
        "https://10.0.0.7:443/fe",
        "https://[::1]",
        "https://[fe80::1%25eth0]",
        "https://2130706433",
        "https://0x7f000001",
        "https://localhost",
        "https://intranet",
        "https://buyer.example.com.",
        "https://-buyer.example.com",
        "https://buyer_x.example.com",
        "https://compradór.example.com",
        "https://buyer.example.com?",
        "https://buyer.example.com/fe?next=1",
        "https://buyer.example.com/fe#top",
        "https://buyer.example.com:99999",
        "https://buyer.example.com:0",
        "https://buyer.example.com:x",
        "https://buyer.example.com/a b",
        "https://buyer.example.com\\@evil.example.com",
        "https://buyer.example.com/\x00",
        "https:///fe",
        "",
        "https://" + "a" * 64 + ".example.com",
        "https://" + ".".join(["abcdefghij"] * 26) + ".do",
    ],
)
def test_anything_else_is_refused(url: str) -> None:
    with pytest.raises(
        BuyerEndpointRefusedError, match=r"^a buyer's endpoint from DGII's directory is refused: "
    ):
        endpoint(url)


def test_an_endpoint_s_url_names_the_address_and_its_authority_the_name() -> None:
    at = Endpoint("buyer.example.com", 443, "/fe")
    assert at.url(PUBLIC, "/fe/recepcion/api/ecf") == "https://93.184.215.14:443/fe/fe/recepcion/api/ecf"
    assert at.url(IPv6Address("2606:4700::1111"), "/x") == "https://[2606:4700::1111]:443/fe/x"
    assert at.authority == "buyer.example.com"
    assert Endpoint("buyer.example.com", 8443, "").authority == "buyer.example.com:8443"


@pytest.mark.parametrize(
    "address",
    [
        "93.184.215.14",
        "8.8.8.8",
        "2606:4700::1111",
        "::ffff:93.184.215.14",
        "2001:4860::1",
    ],
)
def test_a_global_address_is_public(address: str) -> None:
    assert public(ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.1",
        "192.0.2.1",
        "198.18.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::",
        "::1",
        "fe80::1",
        "fc00::1",
        "fd12:3456::1",
        "ff0e::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "64:ff9b::a00:1",
        "64:ff9b:1::1",
        "2002:5db8:d70e::1",
        "2002:7f00:1::1",
        "2002:a00:1::1",
        "2001::1",
        "2001:2::1",
        "2001:10::1",
        "::7f00:1",
        "::a00:1",
        "::ffff:0:7f00:1",
        "fec0::1",
        "5f00::1",
        "4000::1",
    ],
)
def test_a_private_loopback_link_local_reserved_or_translated_address_is_not(address: str) -> None:
    assert not public(ip_address(address))


def test_the_unspecified_addresses_are_not_public() -> None:
    assert not public(IPv4Address(0))
    assert not public(IPv6Address(0))


async def test_a_name_is_pinned_to_its_addresses_in_order_when_every_one_is_public() -> None:
    async def resolve(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
        assert (host, port) == ("buyer.example.com", 8443)
        return (PUBLIC, IPv6Address("2606:4700::1111"))

    assert await pinned(Endpoint("buyer.example.com", 8443, ""), resolve) == (
        PUBLIC,
        IPv6Address("2606:4700::1111"),
    )


@pytest.mark.parametrize(
    "addresses",
    [(), (PUBLIC, IPv4Address("10.0.0.1")), (IPv6Address("::1"),)],
    ids=["none", "one-private", "loopback"],
)
async def test_a_name_with_no_address_or_any_not_public_is_refused(
    addresses: tuple[IPv4Address | IPv6Address, ...],
) -> None:
    async def resolve(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
        del host, port
        return addresses

    with pytest.raises(BuyerEndpointRefusedError, match="resolves to an address"):
        await pinned(Endpoint("buyer.example.com", 443, ""), resolve)


async def test_the_system_resolver_answers_addresses() -> None:
    found = await system_resolve("localhost", 443)
    assert found
    assert all(address.is_loopback for address in found)
