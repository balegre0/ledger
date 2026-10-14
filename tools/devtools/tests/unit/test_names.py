from ledger_devtools.names import distribution_of, import_names, renamed


def test_a_requirement_names_its_distribution() -> None:
    assert distribution_of("psycopg[binary]>=3.3.6") == "psycopg"
    assert distribution_of("opentelemetry-api>=1.45.1") == "opentelemetry-api"
    assert distribution_of(">=1") == ""


def test_an_installed_distribution_installs_the_modules_its_metadata_lists() -> None:
    assert import_names("prometheus-client") == ("prometheus_client",)
    assert import_names("grpcio") == ("grpc",)
    assert import_names("pydantic") == ("pydantic",)
    assert import_names("opentelemetry-sdk") == ("opentelemetry",)
    assert import_names("Pydantic_Settings") == ("pydantic_settings",)


def test_private_and_unimportable_names_are_left_out() -> None:
    assert import_names("pytest") == ("py", "pytest")  # never _pytest
    assert import_names("types-lxml") == ("mypy_plugin_lxml",)  # never lxml-stubs


def test_a_distribution_not_installed_is_assumed_to_install_its_own_name() -> None:
    assert import_names("zz-not-installed") == ("zz_not_installed",)
    assert not renamed("zz-not-installed")


def test_a_distribution_is_renamed_when_it_installs_anything_but_its_own_name() -> None:
    assert renamed("grpcio")
    assert not renamed("prometheus-client")  # deptry reads - as _ itself
    assert renamed("pytest")
    assert not renamed("pydantic")
    assert not renamed("typing-extensions")
