import psycopg
import pytest

from ledger_testing.containers import libpq_url
from ledger_testing.postgres import TEMPLATE, PostgresServer, Template, clone, ensure_template, server_wide
lazy from ledger_testing.postgres import WorkerDatabase


def test_the_server_round_trips_through_json(postgres: PostgresServer) -> None:
    assert PostgresServer.from_json(postgres.to_json()) == postgres


def test_a_database_belongs_to_a_login_role_that_cannot_bypass_row_level_security(
    postgres: PostgresServer,
) -> None:
    url = postgres.database("owned", role="owned_login", password="it's-a-password")
    assert url.startswith("postgresql+psycopg://owned_login:")
    with psycopg.connect(libpq_url(url)) as connection:
        row = connection.execute(
            """SELECT current_user, rolsuper, rolbypassrls, pg_get_userbyid(datdba)
            FROM pg_roles, pg_database WHERE rolname = current_user AND datname = current_database()"""
        ).fetchone()
    assert row == ("owned_login", False, False, "owned_login")


def test_every_database_gets_a_role_of_its_own(postgres: PostgresServer) -> None:
    first = postgres.database("first", role="first_owner", password="one")
    postgres.database("second", role="second_owner", password="two")
    with psycopg.connect(libpq_url(first)) as connection:
        owners = connection.execute(
            """SELECT datname::text, pg_get_userbyid(datdba)::text FROM pg_database
            WHERE datname IN ('first', 'second') ORDER BY datname"""
        ).fetchall()
    assert owners == [("first", "first_owner"), ("second", "second_owner")]


def test_a_template_is_built_once_and_cloned_per_worker(postgres: PostgresServer) -> None:
    built: list[str] = []

    def prepare(template: Template) -> None:
        built.append(template.name)
        assert template.server == postgres
        template.execute(("CREATE TABLE probe (n int)", "INSERT INTO probe VALUES (7)"))
        template.execute(("CREATE TABLE elsewhere (n int)",), database="postgres")

    ensure_template(postgres, prepare, name="probe_template")
    ensure_template(postgres, prepare, name="probe_template")
    clone(postgres, "probe_clone", template="probe_template")
    with psycopg.connect(postgres.libpq("probe_clone")) as connection:
        assert connection.execute("SELECT n FROM probe").fetchone() == (7,)
    with psycopg.connect(postgres.libpq()) as connection:
        assert connection.execute("SELECT count(*) FROM elsewhere").fetchone() == (0,)
    assert built == ["probe_template_building"]


def test_a_template_whose_preparation_fails_is_not_left_behind(postgres: PostgresServer) -> None:
    def fail(template: Template) -> None:
        template.execute(("CREATE TABLE half (n int)",))
        raise RuntimeError(template.name)

    with pytest.raises(RuntimeError, match=r"^broken_template_building$"):
        ensure_template(postgres, fail, name="broken_template")
    ensure_template(
        postgres, lambda template: template.execute(("CREATE TABLE whole (n int)",)), name="broken_template"
    )
    clone(postgres, "broken_clone", template="broken_template")
    with psycopg.connect(postgres.libpq("broken_clone")) as connection:
        tables = connection.execute(
            "SELECT tablename::text FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    assert tables == [("whole",)]


def test_a_template_s_url_names_the_role_that_connects(postgres: PostgresServer) -> None:
    url = Template(postgres, "some_template").url(role="migrator", password="secret")
    assert url == f"postgresql+psycopg://migrator:secret@{postgres.host}:{postgres.port}/some_template"


def test_a_worker_database_is_the_plugins_clone(worker_database: WorkerDatabase, worker_name: str) -> None:
    assert worker_database.name == f"ledger_{worker_name}"
    with psycopg.connect(worker_database.admin_url()) as connection:
        assert connection.execute("SELECT current_database()").fetchone() == (worker_database.name,)
    assert worker_database.url(role="app", password="pw") == postgres_url(worker_database, "app", "pw")


def postgres_url(database: WorkerDatabase, role: str, password: str) -> str:
    server = database.server
    return f"postgresql+psycopg://{role}:{password}@{server.host}:{server.port}/{database.name}"


def test_truncate_empties_the_named_schemas_but_keeps_what_it_must(worker_database: WorkerDatabase) -> None:
    with psycopg.connect(worker_database.admin_url(), autocommit=True) as connection:
        connection.execute(
            """
            CREATE SCHEMA IF NOT EXISTS scratch;
            CREATE TABLE IF NOT EXISTS scratch.t (n int);
            CREATE TABLE IF NOT EXISTS scratch.u (n int);
            CREATE TABLE IF NOT EXISTS scratch.alembic_version (version_num text);
            CREATE SCHEMA IF NOT EXISTS platform;
            CREATE TABLE IF NOT EXISTS platform.routing (version bigint);
            CREATE SCHEMA IF NOT EXISTS untouched;
            CREATE TABLE IF NOT EXISTS untouched.t (n int);
            INSERT INTO scratch.t VALUES (1);
            INSERT INTO scratch.u VALUES (2);
            INSERT INTO scratch.alembic_version VALUES ('r0001');
            INSERT INTO platform.routing SELECT 3 WHERE NOT EXISTS (SELECT FROM platform.routing);
            INSERT INTO untouched.t VALUES (4);
            """
        )
    worker_database.truncate(("scratch", "platform"))
    worker_database.truncate(("nothing_here",))
    with psycopg.connect(worker_database.admin_url()) as connection:
        counts = connection.execute(
            """SELECT (SELECT count(*) FROM scratch.t), (SELECT count(*) FROM scratch.u),
            (SELECT count(*) FROM scratch.alembic_version), (SELECT count(*) FROM platform.routing),
            (SELECT count(*) FROM untouched.t)"""
        ).fetchone()
    assert counts == (0, 0, 1, 1, 1)


def test_the_default_template_is_named_for_the_repository() -> None:
    assert TEMPLATE == "ledger_template"


def test_server_wide_statements_hold_the_lock_template_builds_and_clones_take(
    postgres: PostgresServer,
) -> None:
    held = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = %s AND granted"
    with server_wide(postgres) as admin:
        (pid,) = admin.execute("SELECT pg_backend_pid()").fetchone() or (0,)
        with psycopg.connect(postgres.libpq(), autocommit=True) as other:
            assert other.execute(held, (pid,)).fetchone() == (1,)
    with psycopg.connect(postgres.libpq(), autocommit=True) as other:
        assert other.execute(held, (pid,)).fetchone() == (0,)
