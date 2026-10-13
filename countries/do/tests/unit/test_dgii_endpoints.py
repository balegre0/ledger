import pytest

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import (
    OFFICIAL_HOSTS,
    DgiiHosts,
    HostRefusedError,
    LiveHostRefusedError,
    Service,
    ServiceNotInEnvironmentError,
    offered,
)
from ledger_country_do.channels.ecf_v1.domain.environments import Environment


def test_dgii_s_documented_urls() -> None:
    assert OFFICIAL_HOSTS.url(Service.RECEPTION, Environment.TESTECF) == (
        "https://ecf.dgii.gov.do/testecf/recepcion/api/facturaselectronicas"
    )
    assert OFFICIAL_HOSTS.url(Service.RFCE, Environment.CERTECF) == (
        "https://fc.dgii.gov.do/certecf/recepcionfc/api/recepcion/ecf"
    )
    assert OFFICIAL_HOSTS.url(Service.VALIDATE_SEED, Environment.ECF) == (
        "https://ecf.dgii.gov.do/ecf/autenticacion/api/autenticacion/validarsemilla"
    )


def test_certecf_publishes_neither_track_ids_nor_the_directory() -> None:
    assert not offered(Service.TRACK_IDS, Environment.CERTECF)
    assert not offered(Service.DIRECTORY, Environment.CERTECF)
    assert offered(Service.TRACK_IDS, Environment.TESTECF)
    with pytest.raises(ServiceNotInEnvironmentError, match="no track_ids service in certecf"):
        OFFICIAL_HOSTS.url(Service.TRACK_IDS, Environment.CERTECF)


def test_the_live_environment_is_reached_on_dgii_s_hosts_only() -> None:
    fake = DgiiHosts(ecf="https://127.0.0.1:8471", fc="https://127.0.0.1:8471")
    assert fake.url(Service.SEED, Environment.TESTECF).startswith("https://127.0.0.1:8471/testecf/")
    with pytest.raises(LiveHostRefusedError):
        fake.url(Service.SEED, Environment.ECF)
    assert OFFICIAL_HOSTS.official
    assert not fake.official


@pytest.mark.parametrize(
    "hosts",
    [
        DgiiHosts(ecf="https://ecf.dgii.gov.do", fc="https://fc.dgii.gov.do.example.test"),
        DgiiHosts(ecf="https://ecf.dgii.gov.do.example.test", fc="https://fc.dgii.gov.do"),
        DgiiHosts(ecf="https://ecf.dgii.gov.do:443", fc="https://fc.dgii.gov.do"),
    ],
    ids=["fc-lookalike", "ecf-lookalike", "explicit-port"],
)
def test_nothing_but_dgii_s_exact_hosts_counts_as_dgii_s(hosts: DgiiHosts) -> None:
    assert not hosts.official
    with pytest.raises(LiveHostRefusedError, match="DGII's own hosts only"):
        hosts.url(Service.RFCE, Environment.ECF)


@pytest.mark.parametrize(
    "host",
    [
        "http://ecf.dgii.gov.do",
        "ecf.dgii.gov.do",
        "https://",
        "https://ecf.dgii.gov.do/",
        "https://ecf.dgii.gov.do/ecf",
        "https://user:secret@ecf.dgii.gov.do",
        "https://ecf.dgii.gov.do?x=1",
        "https://ecf.dgii.gov.do#x",
        "https://ecf.dgii.gov.do?",
        "https://[::1",
    ],
    ids=[
        "plain-http",
        "no-scheme",
        "no-host",
        "trailing-slash",
        "path",
        "userinfo",
        "query",
        "fragment",
        "empty-query",
        "unreadable",
    ],
)
def test_a_host_is_an_https_origin_and_nothing_more(host: str) -> None:
    with pytest.raises(HostRefusedError, match="https origin"):
        DgiiHosts(ecf=host)
    with pytest.raises(HostRefusedError, match="https origin"):
        DgiiHosts(fc=host)
