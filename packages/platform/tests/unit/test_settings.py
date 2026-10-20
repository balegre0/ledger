"""``Settings``: the one class every app reads its ``LEDGER_*`` environment with."""

from importlib.metadata import EntryPoint
from typing import ClassVar, Final
lazy from collections.abc import Mapping
lazy from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict
lazy from pydantic import SecretStr

from ledger_platform.config import settings as settings_module
from ledger_platform.config.settings import (
    DATABASE_URL_SCHEME,
    SETTINGS_GROUP,
    LogFormat,
    PackageSettings,
    Settings,
    declared_sections,
    section_prefix,
)

URL: Final = "postgresql+psycopg://ledger_app:s3cret@pooler:5432/ledger"
DIRECT: Final = "postgresql+psycopg://ledger_app:d1rect@db:5432/ledger"
REQUIRED: Final = {"LEDGER_DATABASE_URL": URL, "LEDGER_ENVIRONMENT": "local"}


class SectionedSettings(Settings):
    """What a section of settings of a module's own looks like to the environment."""

    invoice_prefix: str = "INV"


def environment(monkeypatch: pytest.MonkeyPatch, variables: Mapping[str, str]) -> None:
    for name, value in variables.items():
        monkeypatch.setenv(name, value)


def problems(error: ValidationError) -> set[str]:
    """Where each problem is: a field, or the name of an environment variable nobody declared."""
    return {str(detail["loc"][0]) for detail in error.errors(include_input=False)}


def test_settings_need_only_the_database_and_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED)
    settings = Settings()
    assert settings.database_url.get_secret_value() == URL
    assert (settings.environment, settings.log_level, settings.log_format) == (
        "local",
        "INFO",
        LogFormat.CONSOLE,
    )
    assert (settings.database_direct_url, settings.http_port, settings.otlp_endpoint) == (None, 8100, None)
    assert settings.http_host == "127.0.0.1"  # a run on a host binds loopback; the image says otherwise
    assert (settings.token_issuer, settings.token_audience, settings.token_jwks_url) == (None, None, None)
    assert (settings.token_algorithms, settings.token_leeway_seconds) == (frozenset({"RS256"}), 60)
    assert (settings.rate_limit_per_second, settings.rate_limit_burst) == (50.0, 100)
    # the receiver's per-address limit (§10), its peer the client unless proxies are declared
    assert (
        settings.receiver_rate_per_second,
        settings.receiver_rate_burst,
        settings.receiver_forwarded_hops,
    ) == (20.0, 40, 0)


def test_settings_come_from_ledger_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(
        monkeypatch,
        REQUIRED
        | {
            "LEDGER_LOG_FORMAT": "json",
            "LEDGER_LOG_LEVEL": "WARNING",
            "LEDGER_HTTP_HOST": "::1",
            "LEDGER_HTTP_PORT": "9000",
        },
    )
    settings = Settings()
    assert (settings.log_format, settings.log_level, settings.http_port) == (LogFormat.JSON, "WARNING", 9000)
    assert settings.http_host == "::1"


def test_a_pooled_database_url_is_said_so(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_DATABASE_POOLED": "true"})
    assert Settings().database_pooled
    environment(monkeypatch, {**REQUIRED, "LEDGER_DATABASE_POOLED": "false"})
    assert not Settings().database_pooled


def test_the_pool_is_sized_by_two_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED)
    assert (Settings().database_pool_size, Settings().database_pool_overflow) == (30, 10)
    environment(monkeypatch, {"LEDGER_DATABASE_POOL_SIZE": "24", "LEDGER_DATABASE_POOL_OVERFLOW": "0"})
    assert (Settings().database_pool_size, Settings().database_pool_overflow) == (24, 0)


@pytest.mark.parametrize(
    ("name", "value"), [("LEDGER_DATABASE_POOL_SIZE", "0"), ("LEDGER_DATABASE_POOL_OVERFLOW", "-1")]
)
def test_a_pool_needs_a_connection_and_no_negative_overflow(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    environment(monkeypatch, REQUIRED | {name: value})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {name.removeprefix("LEDGER_").lower()}


def test_without_a_database_or_an_environment_the_start_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_DATABASE_URL", raising=False)
    monkeypatch.delenv("LEDGER_ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"database_url", "environment"}


def test_an_empty_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_OTLP_ENDPOINT": "", "LEDGER_DATABASE_URL": ""})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"database_url"}


def test_settings_do_not_change_after_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED)
    settings = Settings()
    field = "http_port"  # a variable: the type checkers rightly refuse a plain assignment
    with pytest.raises(ValidationError):
        setattr(settings, field, 9000)


def test_an_unknown_key_in_given_values_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        Settings.model_validate({"environment": "local", "database_url": URL, "http_prot": 9000})
    assert problems(caught.value) == {"http_prot"}


def test_an_unknown_ledger_variable_fails_the_start_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_HTTP_PROT": "9000"})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"LEDGER_HTTP_PROT"}


def test_every_problem_is_listed_at_once_and_no_value_is_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(
        monkeypatch,
        {
            "LEDGER_ENVIRONMENT": "local",
            "LEDGER_DB_URL": URL,
            "LEDGER_LOGLEVEL": "x",
            "LEDGER_HTTP_PROT": "9000",
        },
    )
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"LEDGER_DB_URL", "LEDGER_LOGLEVEL", "LEDGER_HTTP_PROT", "database_url"}
    assert "s3cret" not in str(caught.value)


def test_a_subclass_accepts_the_variables_of_its_own_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_INVOICE_PREFIX": "BIL"})
    assert SectionedSettings().invoice_prefix == "BIL"


def test_a_variable_is_unknown_to_a_class_that_does_not_declare_it(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_INVOICE_PREFIX": "BIL"})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"LEDGER_INVOICE_PREFIX"}


def test_a_subclass_refuses_a_misspelling_of_its_own_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_INVOICE_PREFX": "BIL"})
    with pytest.raises(ValidationError) as caught:
        SectionedSettings()
    assert problems(caught.value) == {"LEDGER_INVOICE_PREFX"}


def test_variables_that_are_not_ledger_prefixed_are_not_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"HTTP_PROT": "x", "OTHER_LEDGER_NAME": "y", "LEDGERISH_NAME": "z"})
    assert Settings().environment == "local"


def test_variable_names_are_matched_without_regard_to_case(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(
        monkeypatch, {"ledger_environment": "test", "Ledger_Database_Url": URL, "ledger_http_port": "9000"}
    )
    settings = Settings()
    assert (settings.environment, settings.http_port) == ("test", 9000)


def test_a_misspelled_variable_is_refused_in_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"Ledger_Http_Prot": "9000"})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert {name.lower() for name in problems(caught.value)} == {"ledger_http_prot"}


@pytest.mark.parametrize("name", ["LEDGER_DATABASE_URL", "LEDGER_DATABASE_DIRECT_URL"])
@pytest.mark.parametrize(
    "url",
    [
        "postgresql://app:s3cret@db/ledger",
        "postgresql+psycopg2://app:s3cret@db/ledger",
        "postgresql+asyncpg://app:s3cret@db/ledger",
        "mysql+pymysql://app:s3cret@db/ledger",
        "app:s3cret@db/ledger",
    ],
    ids=["no-driver", "psycopg2", "asyncpg", "mysql", "no-scheme"],
)
def test_a_database_url_of_another_driver_is_refused_without_repeating_it(
    monkeypatch: pytest.MonkeyPatch, name: str, url: str
) -> None:
    environment(monkeypatch, {**REQUIRED, name: url})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {name.removeprefix("LEDGER_").lower()}
    assert DATABASE_URL_SCHEME in str(caught.value)
    assert "s3cret" not in str(caught.value)


@pytest.mark.parametrize(
    ("environment_name", "outcome"),
    [("staging", "refused"), ("production", "refused"), ("local", "accepted"), ("test", "accepted")],
)
def test_keys_fetched_in_clear_are_refused_beyond_a_laptop(
    monkeypatch: pytest.MonkeyPatch, environment_name: str, outcome: str
) -> None:
    environment(
        monkeypatch,
        {
            **REQUIRED,
            "LEDGER_ENVIRONMENT": environment_name,
            "LEDGER_TOKEN_JWKS_URL": "http://zitadel:8080/oauth/v2/keys",
            "LEDGER_TOKEN_KIND_CLAIM_REQUIRED": "true",
        },
    )
    if outcome == "refused":
        with pytest.raises(ValidationError, match="https"):
            Settings()
    else:
        assert Settings().token_jwks_url == "http://zitadel:8080/oauth/v2/keys"


@pytest.mark.parametrize("environment_name", ["staging", "production"])
def test_beyond_a_laptop_every_token_must_say_who_calls(
    monkeypatch: pytest.MonkeyPatch, environment_name: str
) -> None:
    """The signed kind claim (``urn:ledger:principal_kind``) is mandatory where no heuristic may decide."""
    environment(monkeypatch, {**REQUIRED, "LEDGER_ENVIRONMENT": environment_name})
    with pytest.raises(ValidationError, match="token_kind_claim_required"):
        Settings()
    monkeypatch.setenv("LEDGER_TOKEN_KIND_CLAIM_REQUIRED", "true")
    assert Settings().token_kind_claim_required


def test_a_laptop_may_do_without_the_kind_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_ENVIRONMENT": "local"})
    assert not Settings().token_kind_claim_required


def test_https_keys_and_no_keys_are_fine_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(
        monkeypatch,
        {**REQUIRED, "LEDGER_ENVIRONMENT": "production", "LEDGER_TOKEN_KIND_CLAIM_REQUIRED": "true"},
    )
    assert Settings().token_jwks_url is None
    monkeypatch.setenv("LEDGER_TOKEN_JWKS_URL", "https://auth.example.com/oauth/v2/keys")
    assert Settings().token_jwks_url == "https://auth.example.com/oauth/v2/keys"


@pytest.mark.parametrize("algorithms", ['["HS256"]', '["none"]', '["RS256", "HS512"]', "[]"])
def test_only_asymmetric_algorithms_can_be_configured(
    monkeypatch: pytest.MonkeyPatch, algorithms: str
) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_TOKEN_ALGORITHMS": algorithms})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"token_algorithms"}


@pytest.mark.parametrize("leeway", ["-1", "301"])
def test_the_leeway_is_at_most_five_minutes(monkeypatch: pytest.MonkeyPatch, leeway: str) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_TOKEN_LEEWAY_SECONDS": leeway})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"token_leeway_seconds"}


@pytest.mark.parametrize(
    ("name", "value", "field"),
    [
        ("LEDGER_RATE_LIMIT_PER_SECOND", "0", "rate_limit_per_second"),
        ("LEDGER_RATE_LIMIT_PER_SECOND", "100001", "rate_limit_per_second"),
        ("LEDGER_RATE_LIMIT_BURST", "0", "rate_limit_burst"),
        ("LEDGER_RATE_LIMIT_BURST", "100001", "rate_limit_burst"),
        ("LEDGER_RECEIVER_RATE_PER_SECOND", "0", "receiver_rate_per_second"),
        ("LEDGER_RECEIVER_RATE_PER_SECOND", "100001", "receiver_rate_per_second"),
        ("LEDGER_RECEIVER_RATE_BURST", "0", "receiver_rate_burst"),
        ("LEDGER_RECEIVER_RATE_BURST", "100001", "receiver_rate_burst"),
        ("LEDGER_RECEIVER_FORWARDED_HOPS", "-1", "receiver_forwarded_hops"),
        ("LEDGER_RECEIVER_FORWARDED_HOPS", "6", "receiver_forwarded_hops"),
    ],
)
def test_the_rate_limit_must_be_a_positive_rate_and_a_burst_of_at_least_one(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, field: str
) -> None:
    environment(monkeypatch, REQUIRED | {name: value})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {field}


def test_listen_url_is_the_direct_url_in_libpq_form(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED)
    listen = Settings().listen_url
    assert listen.get_secret_value() == "postgresql://ledger_app:s3cret@pooler:5432/ledger"
    assert "s3cret" not in f"{listen!r} {listen}"
    monkeypatch.setenv("LEDGER_DATABASE_DIRECT_URL", DIRECT)
    assert Settings().listen_url.get_secret_value() == "postgresql://ledger_app:d1rect@db:5432/ledger"


def test_secrets_never_show(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_DATABASE_DIRECT_URL": DIRECT})
    settings = Settings()
    for shown in (repr(settings), str(settings), settings.model_dump_json()):
        assert "s3cret" not in shown
        assert "d1rect" not in shown


# ── Package sections (LEDGER_<SECTION>_*) ───────────────────────────────────────


class BooksSettings(PackageSettings):
    """A module's section: ``LEDGER_BOOKS_*``."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_prefix=section_prefix("books"))

    limit: int = 10
    token: SecretStr | None = None


@pytest.fixture
def books_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed package declares the section ``books`` (entry point group ``ledger.settings``)."""

    def installed(*, group: str) -> tuple[EntryPoint, ...]:
        assert group == SETTINGS_GROUP
        return (EntryPoint(name="books", value="ledger_books.assembly:MODULE", group=group),)

    monkeypatch.setattr(settings_module, "entry_points", installed)


def test_a_section_s_prefix_is_its_name_in_capitals() -> None:
    assert section_prefix("books") == "LEDGER_BOOKS_"
    assert section_prefix("do_ecf") == "LEDGER_DO_ECF_"


@pytest.mark.usefixtures("books_declared")
def test_the_installed_packages_declare_the_sections() -> None:
    assert declared_sections() == frozenset({"books"})


@pytest.mark.usefixtures("books_declared")
def test_settings_leave_a_declared_section_s_variables_to_it(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_BOOKS_LIMIT": "5", "LEDGER_BOOKS_ANYTHING": "x"})
    assert Settings().environment == "local"


@pytest.mark.usefixtures("books_declared")
def test_a_variable_under_no_declared_section_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_BOOK_LIMIT": "5", "LEDGER_STOCK_LIMIT": "1"})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"LEDGER_BOOK_LIMIT", "LEDGER_STOCK_LIMIT"}


def nothing_installed(*, group: str) -> tuple[EntryPoint, ...]:
    del group
    return ()


def test_without_installed_sections_every_unknown_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module, "entry_points", nothing_installed)
    environment(monkeypatch, REQUIRED | {"LEDGER_BOOKS_LIMIT": "5"})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"LEDGER_BOOKS_LIMIT"}


def test_a_section_reads_its_own_variables_only(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_BOOKS_LIMIT": "5", "LEDGER_BOOKS_TOKEN": "t0ken"})
    books = BooksSettings()
    assert books.limit == 5
    assert books.token is not None
    assert books.token.get_secret_value() == "t0ken"
    assert "t0ken" not in repr(books)


def test_a_section_refuses_a_name_of_its_prefix_it_does_not_declare(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {"LEDGER_BOOKS_CEILING": "5", "LEDGER_BOOKS_LIMIT": "many"})
    with pytest.raises(ValidationError) as caught:
        BooksSettings()
    assert problems(caught.value) == {"LEDGER_BOOKS_CEILING", "limit"}
    assert "many" not in str(caught.value)


def test_a_section_is_frozen() -> None:
    field = "limit"  # a variable: the type checkers rightly refuse a plain assignment
    with pytest.raises(ValidationError):
        setattr(BooksSettings(), field, 1)


def test_no_archive_is_configured_unless_a_backend_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED)
    settings = Settings()
    assert (settings.archive_backend, settings.archive_path, settings.archive_retention_years) == (
        None,
        None,
        10,
    )


def test_the_file_archive_is_a_directory_named_absolutely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "archive"
    environment(
        monkeypatch,
        {
            **REQUIRED,
            "LEDGER_ARCHIVE_BACKEND": "file",
            "LEDGER_ARCHIVE_PATH": str(archive),
            "LEDGER_ARCHIVE_RETENTION_YEARS": "12",
        },
    )
    settings = Settings()
    assert (settings.archive_backend, settings.archive_path, settings.archive_retention_years) == (
        "file",
        archive,
        12,
    )


@pytest.mark.parametrize("path", [None, "archive"])
def test_the_file_archive_needs_an_absolute_path(monkeypatch: pytest.MonkeyPatch, path: str | None) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_ARCHIVE_BACKEND": "file"})
    if path is not None:
        monkeypatch.setenv("LEDGER_ARCHIVE_PATH", path)
    with pytest.raises(ValidationError, match="archive_path must be an absolute path") as caught:
        Settings()
    assert "'archive'" not in str(caught.value)


def test_production_never_archives_to_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret = tmp_path / "s3cret-archive"
    environment(
        monkeypatch,
        {
            **REQUIRED,
            "LEDGER_ENVIRONMENT": "production",
            "LEDGER_TOKEN_KIND_CLAIM_REQUIRED": "true",
            "LEDGER_ARCHIVE_BACKEND": "file",
            "LEDGER_ARCHIVE_PATH": str(secret),
        },
    )
    with pytest.raises(ValidationError, match="archive_backend file is refused in production") as caught:
        Settings()
    assert "s3cret" not in str(caught.value)
    monkeypatch.setenv("LEDGER_ENVIRONMENT", "staging")
    assert Settings().archive_path == secret


@pytest.mark.parametrize("years", ["0", "101"])
def test_the_retention_is_one_to_a_hundred_years(monkeypatch: pytest.MonkeyPatch, years: str) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_ARCHIVE_RETENTION_YEARS": years})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"archive_retention_years"}


@pytest.mark.parametrize("environment_name", ["staging", "production"])
def test_beyond_a_laptop_the_archive_keeps_what_the_law_requires(
    monkeypatch: pytest.MonkeyPatch, environment_name: str
) -> None:
    """Ten years (Código Tributario art. 50 h) at least; a laptop or a test may keep less."""
    environment(
        monkeypatch,
        {
            **REQUIRED,
            "LEDGER_ENVIRONMENT": environment_name,
            "LEDGER_TOKEN_KIND_CLAIM_REQUIRED": "true",
            "LEDGER_ARCHIVE_RETENTION_YEARS": "9",
        },
    )
    with pytest.raises(ValidationError, match="archive_retention_years must be at least 10"):
        Settings()
    monkeypatch.setenv("LEDGER_ARCHIVE_RETENTION_YEARS", "10")
    assert Settings().archive_retention_years == 10
    monkeypatch.setenv("LEDGER_ENVIRONMENT", "local")
    monkeypatch.setenv("LEDGER_ARCHIVE_RETENTION_YEARS", "1")
    assert Settings().archive_retention_years == 1


S3: Final = {
    "LEDGER_ARCHIVE_BACKEND": "s3",
    "LEDGER_ARCHIVE_BUCKET": "ledger-archive",
    "LEDGER_ARCHIVE_REGION": "us-east-1",
}
PRODUCTION: Final = {
    **REQUIRED,
    "LEDGER_ENVIRONMENT": "production",
    "LEDGER_TOKEN_KIND_CLAIM_REQUIRED": "true",
}


def test_the_s3_archive_is_a_bucket_in_a_region_encrypted_by_s3_unless_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment(monkeypatch, {**PRODUCTION, **S3})
    settings = Settings()
    assert (settings.archive_backend, settings.archive_bucket, settings.archive_region) == (
        "s3",
        "ledger-archive",
        "us-east-1",
    )
    assert (settings.archive_endpoint_url, settings.archive_encryption, settings.archive_kms_key_id) == (
        None,
        "AES256",
        None,
    )
    monkeypatch.setenv("LEDGER_ARCHIVE_ENCRYPTION", "aws:kms")
    monkeypatch.setenv("LEDGER_ARCHIVE_KMS_KEY_ID", "alias/ledger-archive")
    assert (Settings().archive_encryption, Settings().archive_kms_key_id) == (
        "aws:kms",
        "alias/ledger-archive",
    )


@pytest.mark.parametrize("missing", ["LEDGER_ARCHIVE_BUCKET", "LEDGER_ARCHIVE_REGION"])
def test_the_s3_archive_needs_its_bucket_and_region(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    environment(monkeypatch, {**REQUIRED, **S3})
    monkeypatch.delenv(missing)
    with pytest.raises(ValidationError, match="archive_bucket and archive_region must be set"):
        Settings()


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [
        ("LEDGER_ARCHIVE_BUCKET", "Ledger_Archive", "archive_bucket"),
        ("LEDGER_ARCHIVE_BUCKET", "ab", "archive_bucket"),
        ("LEDGER_ARCHIVE_REGION", "Mars", "archive_region"),
        ("LEDGER_ARCHIVE_ENCRYPTION", "none", "archive_encryption"),
        ("LEDGER_ARCHIVE_ENDPOINT_URL", "ftp://localstack:4566", "archive_endpoint_url"),
    ],
)
def test_s3_options_are_checked_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, field: str
) -> None:
    environment(monkeypatch, {**REQUIRED, **S3, variable: value})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {field}
    assert value not in str(caught.value)


def test_a_kms_key_is_named_only_for_kms_encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {**REQUIRED, **S3, "LEDGER_ARCHIVE_KMS_KEY_ID": "alias/s3cret-key"})
    with pytest.raises(
        ValidationError, match="archive_kms_key_id is only for archive_encryption aws:kms"
    ) as caught:
        Settings()
    assert "s3cret" not in str(caught.value)


@pytest.mark.parametrize("environment_name", ["staging", "production"])
def test_beyond_a_laptop_the_archive_is_aws_s_s3(
    monkeypatch: pytest.MonkeyPatch, environment_name: str
) -> None:
    """An endpoint of our naming (LocalStack) is a laptop's and the tests', never the law's archive."""
    endpoint = "http://s3cret-host:4566"
    environment(
        monkeypatch,
        {**PRODUCTION, **S3, "LEDGER_ENVIRONMENT": environment_name, "LEDGER_ARCHIVE_ENDPOINT_URL": endpoint},
    )
    with pytest.raises(
        ValidationError, match="archive_endpoint_url is refused in staging and production"
    ) as caught:
        Settings()
    assert "s3cret" not in str(caught.value)
    monkeypatch.setenv("LEDGER_ENVIRONMENT", "test")
    assert Settings().archive_endpoint_url == endpoint


@pytest.mark.parametrize(
    ("variable", "value"),
    [("LEDGER_ARCHIVE_KMS_KEY_ID", "alias/s3cret"), ("LEDGER_ARCHIVE_ENDPOINT_URL", "http://s3cret:4566")],
)
@pytest.mark.parametrize("backend", ["file", None])
def test_s3_options_are_refused_for_another_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str, value: str, backend: str | None
) -> None:
    """An S3 option beside another backend is a mistake: the operator meant one of the two."""
    environment(monkeypatch, {**REQUIRED, variable: value})
    if backend is not None:
        monkeypatch.setenv("LEDGER_ARCHIVE_BACKEND", backend)
        monkeypatch.setenv("LEDGER_ARCHIVE_PATH", str(tmp_path))
    if variable == "LEDGER_ARCHIVE_KMS_KEY_ID":
        monkeypatch.setenv("LEDGER_ARCHIVE_ENCRYPTION", "aws:kms")
    with pytest.raises(ValidationError, match="are only for archive_backend s3") as caught:
        Settings()
    assert "s3cret" not in str(caught.value)
