"""The roles, schemas and grants of §6.2, as SQL statements.

One builder for ``init.sql`` (devtools), the test template (``ledger_testing``) and ``ledger-migrate``'s relay
grants. Roles are the cluster's, shared by every database on it, so each ``CREATE ROLE`` tolerates one that
exists. No role is a superuser or ``BYPASSRLS``. Logins are ``NOINHERIT``: ``ledger_app`` reaches a module's
tables only after ``SET LOCAL ROLE <schema>_rw`` (one unit at a time), and ``ledger_migrator`` a schema only
after ``SET ROLE <schema>_owner``. Schema names are checked before they reach SQL; passwords are written as
SQL literals.
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
_SCHEMA: Final = re.compile(r"(?!pg_)[a-z][a-z0-9_]{1,30}", re.ASCII)
"""2 to 31 characters, so that ``<schema>_owner`` stays a short identifier; ``pg_`` is PostgreSQL's prefix."""
_PLAIN: Final = "NOSUPERUSER NOBYPASSRLS"
_LOGIN: Final = f"LOGIN NOINHERIT {_PLAIN} NOCREATEDB NOCREATEROLE"


class InvalidSchemaNameError(LedgerError):
    """A schema name that cannot be a role prefix, or roles that cannot be made: a defect of the caller.

    A schema name starts with a letter and not with ``pg_`` (PostgreSQL's own prefix).
    """

    code: ClassVar[str] = "invalid_schema_name"

    def __init__(self, schema: str, *, signer_without_password: bool = False) -> None:
        shown = excerpt(schema)
        super().__init__(
            f"schema {shown} belongs to the signer login, and the signer login was given no password"
            if signer_without_password
            else f"the schema name {shown} is not 2 to 31 lower-case letters, digits and underscores"
        )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaRoles:
    """A schema and its two roles.

    ``signer``: its ``rw`` role belongs to ``ledger_signer``, not to ``ledger_app``.
    """

    schema: str
    signer: bool = False

    def __post_init__(self) -> None:
        if _SCHEMA.fullmatch(self.schema) is None:
            raise InvalidSchemaNameError(self.schema)

    @property
    def owner(self) -> str:
        """Owns the schema and its objects; ``ledger_migrator`` becomes it to migrate."""
        return f"{self.schema}_owner"

    @property
    def rw(self) -> str:
        """Reads and writes the schema's tables, no DDL; a unit becomes it with ``SET LOCAL ROLE``."""
        return f"{self.schema}_rw"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Logins:
    """The passwords of the login roles (local and test values only; production takes them from secrets)."""

    app: str
    migrator: str
    signer: str | None = None


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _role(name: str, options: str) -> str:
    return f"DO $$ BEGIN CREATE ROLE {name} {options}; EXCEPTION WHEN duplicate_object THEN NULL; END $$"


def cluster_statements(schemas: Sequence[SchemaRoles], logins: Logins) -> tuple[str, ...]:
    """The roles of every schema and the logins, with their memberships (run once per server)."""
    statements = [
        _role(RELAY, f"NOLOGIN {_PLAIN}"),
        _role(APP, f"{_LOGIN} PASSWORD {_literal(logins.app)}"),
        _role(MIGRATOR, f"{_LOGIN} PASSWORD {_literal(logins.migrator)}"),
    ]
    signed = [schema.schema for schema in schemas if schema.signer]
    if signed:
        if logins.signer is None:
            raise InvalidSchemaNameError(signed[0], signer_without_password=True)
        statements.append(_role(SIGNER, f"{_LOGIN} PASSWORD {_literal(logins.signer)}"))
    for schema in schemas:
        statements += [
            _role(schema.owner, f"NOLOGIN {_PLAIN}"),
            _role(schema.rw, f"NOLOGIN {_PLAIN}"),
            f"GRANT {schema.owner} TO {MIGRATOR}",
            f"GRANT {schema.rw} TO {SIGNER if schema.signer else APP}",
        ]
    statements.append(f"GRANT {RELAY} TO {APP}")
    if signed:
        statements.append(f"GRANT {PLATFORM}_rw TO {SIGNER}")
    return tuple(statements)


def database_statements(schemas: Sequence[SchemaRoles]) -> tuple[str, ...]:
    """Each schema, owned by its owner role, used by its ``rw`` role and the relay (run in the database)."""
    statements: list[str] = []
    for schema in schemas:
        name, owner, rw = schema.schema, schema.owner, schema.rw
        default = f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {name} GRANT"
        statements += [
            f"CREATE SCHEMA IF NOT EXISTS {name} AUTHORIZATION {owner}",
            f"GRANT USAGE ON SCHEMA {name} TO {rw}, {RELAY}",
            f"{default} SELECT, INSERT, UPDATE, DELETE ON TABLES TO {rw}",
            f"{default} USAGE, SELECT ON SEQUENCES TO {rw}",
        ]
    return tuple(statements)


def relay_statements(schema: str) -> tuple[str, ...]:
    """What ``ledger_relay`` may do in ``schema`` once its tables exist (``ledger-migrate`` runs it)."""
    name = SchemaRoles(schema=schema).schema
    statements = [
        f"GRANT SELECT, UPDATE, DELETE ON {name}.outbox TO {RELAY}",
        f"GRANT INSERT ON {name}.inbox TO {RELAY}",
        f"GRANT SELECT ON {name}.subscriptions TO {RELAY}",
    ]
    if name == PLATFORM:
        statements.append(f"GRANT SELECT ON {PLATFORM}.routing TO {RELAY}")
    return tuple(statements)
