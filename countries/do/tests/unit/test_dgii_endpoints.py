import pytest

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import (
    OFFICIAL_HOSTS,
    DgiiHosts,
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
    fake = DgiiHosts(ecf="http://127.0.0.1:8471", fc="http://127.0.0.1:8471")
    assert fake.url(Service.SEED, Environment.TESTECF).startswith("http://127.0.0.1:8471/testecf/")
    with pytest.raises(LiveHostRefusedError):
        fake.url(Service.SEED, Environment.ECF)
    assert OFFICIAL_HOSTS.official
    assert not fake.official


@pytest.mark.parametrize(
    "hosts",
    [
        DgiiHosts(ecf="https://ecf.dgii.gov.do", fc="https://fc.dgii.gov.do.example.test"),
        DgiiHosts(ecf="https://ecf.dgii.gov.do.example.test", fc="https://fc.dgii.gov.do"),
        DgiiHosts(ecf="http://ecf.dgii.gov.do", fc="https://fc.dgii.gov.do"),
        DgiiHosts(ecf="https://ecf.dgii.gov.do/", fc="https://fc.dgii.gov.do"),
    ],
    ids=["fc-lookalike", "ecf-lookalike", "plain-http", "trailing-slash"],
)
def test_nothing_but_dgii_s_exact_hosts_counts_as_dgii_s(hosts: DgiiHosts) -> None:
    assert not hosts.official
    with pytest.raises(LiveHostRefusedError, match="DGII's own hosts only"):
        hosts.url(Service.RFCE, Environment.ECF)
