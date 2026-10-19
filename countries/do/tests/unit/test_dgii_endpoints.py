import pytest

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import (
    OFFICIAL_HOSTS,
    PATHS,
    DgiiHosts,
    HostRefusedError,
    LiveHostRefusedError,
    Service,
    ServiceNotInEnvironmentError,
    offered,
)
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.printed import OFFICIAL_QR, QrHosts


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
        "http://example.com",
        "http://127.0.0.2:8471",
        "http://localhost.example.com",
        "http://user@127.0.0.1:8471",
        "http://127.0.0.1:8471/",
        "ftp://127.0.0.1",
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
        "plain-http-elsewhere",
        "plain-http-other-loopback-address",
        "plain-http-lookalike-localhost",
        "plain-http-loopback-userinfo",
        "plain-http-loopback-path",
        "ftp",
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


@pytest.mark.parametrize(
    "host", ["http://127.0.0.1:8471", "http://[::1]:8471", "http://localhost:8471", "http://LOCALHOST"]
)
def test_plain_http_reaches_a_fake_on_this_machine_for_testecf_and_certecf_only(host: str) -> None:
    fake = DgiiHosts(ecf=host, fc=host)
    assert fake.url(Service.SEED, Environment.TESTECF) == f"{host}/testecf/{PATHS[Service.SEED]}"
    assert fake.url(Service.RFCE, Environment.CERTECF) == f"{host}/certecf/{PATHS[Service.RFCE]}"
    assert not fake.official
    with pytest.raises(LiveHostRefusedError):
        fake.url(Service.SEED, Environment.ECF)


def test_a_seed_s_audience_is_the_ecf_host_with_its_port() -> None:
    assert OFFICIAL_HOSTS.audience == "ecf.dgii.gov.do"
    assert DgiiHosts(ecf="https://ecf.fake.test:8443").audience == "ecf.fake.test:8443"
    assert DgiiHosts(ecf="http://[::1]:8471").audience == "[::1]:8471"


@pytest.mark.parametrize("host", ["https://ecf_dgii.fake.test", "https://ecf.fake.test:8443%2F"])
def test_an_ecf_host_the_signer_could_not_audit_as_an_audience_is_refused(host: str) -> None:
    # Checked once, when the hosts are configured, never at the first seed signed.
    with pytest.raises(HostRefusedError, match="https origin"):
        DgiiHosts(ecf=host)


def test_the_qr_s_hosts_are_the_checked_hosts_documents_go_to() -> None:
    assert OFFICIAL_HOSTS.qr == OFFICIAL_QR
    fake = DgiiHosts(ecf="http://127.0.0.1:8471", fc="https://fc.fake.test")
    assert fake.qr == QrHosts(ecf="http://127.0.0.1:8471", fc="https://fc.fake.test")
