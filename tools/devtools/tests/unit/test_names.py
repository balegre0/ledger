from ledger_devtools.names import distribution_of, import_names, renamed


def test_a_requirement_names_its_distribution() -> None:
    assert distribution_of("psycopg[binary]>=3.3.6") == "psycopg"
    assert distribution_of("opentelemetry-api>=1.45.1") == "opentelemetry-api"
    assert distribution_of(">=1") == ""


def test_a_distribution_installs_its_modules() -> None:
    assert import_names("prometheus-client") == ("prometheus_client",)
    assert import_names("grpcio") == ("grpc",)
    assert import_names("pydantic") == ("pydantic",)
    assert import_names("typing-extensions") == ("typing_extensions",)


def test_only_distributions_known_to_install_other_modules_are_renamed() -> None:
    assert import_names("protobuf") == ("google",)
    assert renamed("protobuf")
    assert renamed("prometheus-client")
    assert not renamed("typing-extensions")
    assert not renamed("pydantic")
