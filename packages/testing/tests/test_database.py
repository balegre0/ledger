from ledger_testing.database import TEST_LOGINS, app_url, libpq_as, migrator_url
from ledger_testing.postgres import PostgresServer, WorkerDatabase

WORKER = WorkerDatabase(PostgresServer(host="db", port=5433, user="admin", password="secret"), "ledger_gw1")


def test_tests_connect_as_the_logins_the_apps_use() -> None:
    assert app_url(WORKER) == "postgresql+psycopg://ledger_app:app-password@db:5433/ledger_gw1"
    assert migrator_url(WORKER) == "postgresql+psycopg://ledger_migrator:migrator-password@db:5433/ledger_gw1"
    assert (
        libpq_as(WORKER, role="ledger_app", password="pw") == "postgresql://ledger_app:pw@db:5433/ledger_gw1"
    )


def test_every_login_has_a_test_password() -> None:
    assert (TEST_LOGINS.app, TEST_LOGINS.migrator, TEST_LOGINS.signer) == (
        "app-password",
        "migrator-password",
        "signer-password",
    )
