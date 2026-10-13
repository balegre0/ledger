import psycopg

from ledger_testing.containers import libpq_url
lazy from ledger_testing.containers import Postgres


def test_a_database_belongs_to_a_login_role_that_cannot_bypass_row_level_security(server: Postgres) -> None:
    url = server.database("probe", role="probe_owner", password="it's-a-password")
    with psycopg.connect(libpq_url(url)) as connection:
        row = connection.execute(
            """SELECT current_user, rolsuper, rolbypassrls, pg_get_userbyid(datdba)
            FROM pg_roles, pg_database WHERE rolname = current_user AND datname = current_database()"""
        ).fetchone()
    assert row == ("probe_owner", False, False, "probe_owner")


def test_every_database_gets_a_role_of_its_own(server: Postgres) -> None:
    first = server.database("first", role="first_owner", password="one")
    server.database("second", role="second_owner", password="two")
    with psycopg.connect(libpq_url(first)) as connection:
        owners = connection.execute(
            """SELECT datname::text, pg_get_userbyid(datdba)::text FROM pg_database
            WHERE datname IN ('first', 'second') ORDER BY datname"""
        ).fetchall()
        roles = connection.execute(
            """SELECT rolname::text, rolsuper, rolbypassrls FROM pg_roles
            WHERE rolname IN ('first_owner', 'second_owner') ORDER BY rolname"""
        ).fetchall()
    assert owners == [("first", "first_owner"), ("second", "second_owner")]
    assert roles == [("first_owner", False, False), ("second_owner", False, False)]


def test_psycopg_takes_the_same_url_without_the_driver() -> None:
    assert libpq_url("postgresql+psycopg://app:secret@host:5432/db") == "postgresql://app:secret@host:5432/db"
