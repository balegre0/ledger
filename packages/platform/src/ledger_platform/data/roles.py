"""The roles, schemas and grants of §6.2, as SQL statements.

One builder for ``init.sql`` (devtools), the test template (``ledger_testing``) and ``ledger-migrate``'s
grants. Roles are the cluster's, shared by every database on it: each is created if missing, then altered to
exactly the options it must have, so a role that existed with others (``INHERIT``, ``BYPASSRLS``, a stale
password) is normalized; memberships are granted with their options spelled out for the same reason. No role
is a superuser or ``BYPASSRLS``. Logins are ``NOINHERIT``: ``ledger_app`` reaches a module's tables only after
``SET LOCAL ROLE <schema>_rw`` (one unit at a time), ``ledger_migrator`` a schema only after
``SET ROLE <schema>_owner``.

Every identifier is quoted (``identifier``) after its name was checked; passwords are escape-string literals
outside any dollar-quoted block.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import Sequence

from ledger_kernel.errors import LedgerError, excerpt

APP: Final = "ledger_app"
MIGRATOR: Final = "ledger_migrator"
SIGNER: Final = "ledger_signer"
RELAY: Final = "ledger_relay"
PLATFORM: Final = "platform"
"""The platform's own schema: its ``rw`` role is the signer's too (schedule leases); the relay reads its
``routing``."""
_SCHEMA: Final = re.compile(r"[a-z][a-z0-9_]{1,30}", re.ASCII)
"""2 to 31 characters, so that ``<schema>_owner`` stays a short identifier."""
RESERVED_WORDS: Final = frozenset(
    [
        "all",
        "analyse",
        "analyze",
        "and",
        "any",
        "array",
        "as",
        "asc",
        "asymmetric",
        "authorization",
        "binary",
        "both",
        "case",
        "cast",
        "check",
        "collate",
        "collation",
        "column",
        "concurrently",
        "constraint",
        "create",
        "cross",
        "current_catalog",
        "current_date",
        "current_role",
        "current_schema",
        "current_time",
        "current_timestamp",
        "current_user",
        "default",
        "deferrable",
        "desc",
        "distinct",
        "do",
        "else",
        "end",
        "except",
        "false",
        "fetch",
        "for",
        "foreign",
        "freeze",
        "from",
        "full",
        "grant",
        "group",
        "having",
        "ilike",
        "in",
        "initially",
        "inner",
        "intersect",
        "into",
        "is",
        "isnull",
        "join",
        "lateral",
        "leading",
        "left",
        "like",
        "limit",
        "localtime",
        "localtimestamp",
        "natural",
        "not",
        "notnull",
        "null",
        "offset",
        "on",
        "only",
        "or",
        "order",
        "outer",
        "overlaps",
        "placing",
        "primary",
        "references",
        "returning",
        "right",
        "select",
        "session_user",
        "similar",
        "some",
        "symmetric",
        "system_user",
        "table",
        "tablesample",
        "then",
        "to",
        "trailing",
        "true",
        "union",
        "unique",
        "user",
        "using",
        "variadic",
        "verbose",
        "when",
        "where",
        "window",
        "with",
    ]
)
"""PostgreSQL's reserved key words (``pg_get_keywords()``, categories R and T; a test keeps them equal)."""
_REFUSED: Final = RESERVED_WORDS | {"pg", "public", "information_schema"}
_GROUP: Final = "NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION"
_LOGIN: Final = "LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION"
_MEMBER: Final = "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
"""A login becomes the role with ``SET ROLE``; it never inherits it, and can grant it to no one."""
_DATABASE: Final = "DO $$ BEGIN EXECUTE format('{} ON DATABASE %I {}', current_database()); END $$"
"""A statement on the current database, whose name only the server knows (no input reaches the block)."""


class InvalidSchemaNameError(LedgerError):
    """A schema name that cannot be a role prefix, or roles that cannot be made: a defect of the caller.

    A schema name is not ``pg``, ``pg_*``, ``public``, ``information_schema`` or a reserved word.
    """

    code: ClassVar[str] = "invalid_schema_name"

    def __init__(self, schema: str, *, signer_without_password: bool = False) -> None:
        shown = excerpt(schema)
        super().__init__(
            f"schema {shown} belongs to the signer login, and the signer login was given no password"
            if signer_without_password
            else f"the schema name {shown} is not 2 to 31 lower-case letters, digits and underscores"
        )


def identifier(name: str) -> str:
    """``name`` as a quoted SQL identifier (names are checked first; quoting makes any keyword safe)."""
    return '"' + name.replace('"', '""') + '"'


def _literal(text: str) -> str:
    """``text`` as an escape-string literal, whatever ``standard_conforming_strings`` says."""
    return "E'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaRoles:
    """A schema and its roles.

    ``signer``: its ``rw`` and ``purge`` roles belong to ``ledger_signer``, not to ``ledger_app``.
    """

    schema: str
    signer: bool = False

    def __post_init__(self) -> None:
        if _SCHEMA.fullmatch(self.schema) is None or self.schema in _REFUSED or self.schema.startswith("pg_"):
            raise InvalidSchemaNameError(self.schema)

    @property
    def owner(self) -> str:
        """Owns the schema and its objects; ``ledger_migrator`` becomes it to migrate."""
        return f"{self.schema}_owner"

    @property
    def rw(self) -> str:
        """Reads and writes the schema's tables, no DDL; a unit becomes it with ``SET LOCAL ROLE``."""
        return f"{self.schema}_rw"

    @property
    def purge(self) -> str:
        """Reads and deletes expired idempotency keys of every organization, and nothing else (Decision 6)."""
        return f"{self.schema}_purge"

    @property
    def login(self) -> str:
        """The login that enters ``rw`` and ``purge``."""
        return SIGNER if self.signer else APP


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Logins:
    """The passwords of the login roles (local and test values only; production takes them from secrets)."""

    app: str
    migrator: str
    signer: str | None = None


def _role(name: str, options: str) -> tuple[str, str]:
    quoted = identifier(name)
    return (
        f"DO $$ BEGIN CREATE ROLE {quoted}; EXCEPTION WHEN duplicate_object THEN NULL; END $$",
        f"ALTER ROLE {quoted} WITH {options}",
    )


def _member(role: str, login: str) -> str:
    return f"GRANT {identifier(role)} TO {identifier(login)} {_MEMBER}"


def cluster_statements(schemas: Sequence[SchemaRoles], logins: Logins) -> tuple[str, ...]:
    """The roles of every schema and the logins, with their memberships (run once per server)."""
    statements = [
        *_role(RELAY, _GROUP),
        *_role(APP, f"{_LOGIN} PASSWORD {_literal(logins.app)}"),
        *_role(MIGRATOR, f"{_LOGIN} PASSWORD {_literal(logins.migrator)}"),
    ]
    signed = [schema.schema for schema in schemas if schema.signer]
    if signed:
        if logins.signer is None:
            raise InvalidSchemaNameError(signed[0], signer_without_password=True)
        statements += _role(SIGNER, f"{_LOGIN} PASSWORD {_literal(logins.signer)}")
    for schema in schemas:
        statements += [
            *_role(schema.owner, _GROUP),
            *_role(schema.rw, _GROUP),
            *_role(schema.purge, _GROUP),
            _member(schema.owner, MIGRATOR),
            _member(schema.rw, schema.login),
            _member(schema.purge, schema.login),
        ]
    statements.append(_member(RELAY, APP))
    if signed:
        statements.append(_member(f"{PLATFORM}_rw", SIGNER))
    return tuple(statements)


def database_statements(schemas: Sequence[SchemaRoles]) -> tuple[str, ...]:
    """The database closed to ``PUBLIC``, and each schema owned by its owner (run in the database).

    Only the logins connect; nobody makes temporary tables or uses ``public``; functions an owner makes are
    executed by no one it did not grant them to. Each schema is used by its ``rw`` and ``purge`` roles and the
    relay; its tables, as the owner makes them, are its ``rw`` role's to read and write.
    """
    logins = [APP, MIGRATOR, *((SIGNER,) if any(schema.signer for schema in schemas) else ())]
    statements = [
        _DATABASE.format("REVOKE TEMPORARY, CONNECT", "FROM PUBLIC"),
        _DATABASE.format("GRANT CONNECT", "TO " + ", ".join(identifier(login) for login in logins)),
        "REVOKE ALL ON SCHEMA public FROM PUBLIC",
    ]
    for schema in schemas:
        name, owner = identifier(schema.schema), identifier(schema.owner)
        rw, purge = identifier(schema.rw), identifier(schema.purge)
        default = f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner}"
        statements += [
            f"CREATE SCHEMA IF NOT EXISTS {name} AUTHORIZATION {owner}",
            f"GRANT USAGE ON SCHEMA {name} TO {rw}, {purge}, {identifier(RELAY)}",
            f"{default} IN SCHEMA {name} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {rw}",
            f"{default} IN SCHEMA {name} GRANT USAGE, SELECT ON SEQUENCES TO {rw}",
            f"{default} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
        ]
    return tuple(statements)


def relay_statements(schema: str) -> tuple[str, ...]:
    """What ``ledger_relay`` may do in ``schema`` once its tables exist (run as the owner, after migrations).

    The fan-out (§3.5) reads and deletes outbox rows and marks them (``attempts``, ``unrouted_since``),
    inserts inbox rows and reads subscriptions; in ``platform`` it also reads the routing version.
    """
    roles = SchemaRoles(schema=schema)
    name, relay = identifier(roles.schema), identifier(RELAY)
    statements = [
        f"REVOKE ALL ON {name}.outbox FROM {relay}",
        f"GRANT SELECT, DELETE ON {name}.outbox TO {relay}",
        f"GRANT UPDATE (attempts, unrouted_since) ON {name}.outbox TO {relay}",
        f"GRANT INSERT ON {name}.inbox TO {relay}",
        f"GRANT SELECT ON {name}.subscriptions TO {relay}",
    ]
    if roles.schema == PLATFORM:
        statements.append(f"GRANT SELECT ON {name}.routing TO {relay}")
    return tuple(statements)


def migrated_statements(schema: str) -> tuple[str, ...]:
    """What ``ledger-migrate`` runs as the owner after a schema's revisions.

    The relay's grants, and Alembic's version table made read-only for the ``rw`` role (readiness reads it;
    nothing at run time writes it).
    """
    roles = SchemaRoles(schema=schema)
    version = f"{identifier(roles.schema)}.alembic_version"
    return (
        *relay_statements(schema),
        f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {version} FROM {identifier(roles.rw)}",
    )
