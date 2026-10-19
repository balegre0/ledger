"""The roles, schemas and grants of §6.2, as SQL statements.

One builder for ``init.sql`` (devtools), the test template (``ledger_testing``) and ``ledger-migrate``'s
grants. Roles are the cluster's, shared by every database on it: each is created if missing, then altered to
exactly the options it must have, so a role that existed with others (``INHERIT``, ``BYPASSRLS``, a stale
password) is normalized; memberships are granted with their options spelled out for the same reason. No role
is a superuser or ``BYPASSRLS``, none has a connection limit or an expiry, no group role a password.
Logins are ``NOINHERIT``: ``ledger_app`` reaches a module's tables only after ``SET LOCAL ROLE <schema>_rw``
(one unit at a time), ``ledger_migrator`` a schema only after ``SET ROLE <schema>_owner``. Memberships
nobody grants here are revoked last (``_revoke_strays``).

Every identifier is quoted (``identifier``) after its name was checked. A password never reaches the
server: the statements carry its SCRAM-SHA-256 verifier (``scram_verifier``), so neither ``log_statement``
nor an error report can show it. The verifier is an escape-string literal outside any dollar-quoted block.
"""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Sequence

from psycopg import sql

from ledger_kernel.errors import LedgerError, excerpt
from ledger_platform.config.names import PLATFORM, is_schema_name

APP: Final = "ledger_app"
MIGRATOR: Final = "ledger_migrator"
SIGNER: Final = "ledger_signer"
RELAY: Final = "ledger_relay"
_UNLIMITED: Final = "CONNECTION LIMIT -1 VALID UNTIL 'infinity'"
"""A limit or an expiry someone set on a role is taken off: the apps' pools size the connections."""
_GROUP: Final = (
    f"NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION {_UNLIMITED} PASSWORD NULL"
)
_LOGIN: Final = f"LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION {_UNLIMITED}"
SCRAM_ITERATIONS: Final = 4096
"""PostgreSQL's own default (``scram_iterations``)."""
SALT_BYTES: Final = 16
"""PostgreSQL's own salt length, and the shortest a verifier here is made with."""
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


class UnsupportedPasswordError(LedgerError):
    """A login password whose SCRAM verifier this module cannot compute as PostgreSQL would.

    PostgreSQL prepares a password with SASLprep before hashing it, which leaves printable ASCII alone and may
    rewrite anything else; such a verifier would not match. The password itself is never shown.
    """

    code: ClassVar[str] = "unsupported_password"

    def __init__(self) -> None:
        super().__init__("a login password is 1 or more printable ASCII characters")


class ShortSaltError(LedgerError):
    """A SCRAM salt shorter than PostgreSQL's own (16 bytes): it would weaken every verifier made with it."""

    code: ClassVar[str] = "scram_salt_too_short"

    def __init__(self, length: int) -> None:
        super().__init__(f"a SCRAM salt has at least {SALT_BYTES} bytes, not {length}")


type Salting = Callable[[str], bytes]
"""The salt of a login's verifier, by the login's name."""


def random_salt(login: str) -> bytes:
    """A new random salt (the default): every run makes another verifier of the same password."""
    del login
    return secrets.token_bytes(SALT_BYTES)


def scram_verifier(password: str, *, salt: bytes) -> str:
    """``password`` as PostgreSQL stores it for SCRAM-SHA-256 (RFC 5802, RFC 7677).

    ``SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>``, base64: what ``ALTER ROLE ... PASSWORD``
    keeps as it is, without ever seeing the password.
    """
    if not password or not (password.isascii() and password.isprintable()):
        raise UnsupportedPasswordError
    if len(salt) < SALT_BYTES:
        raise ShortSaltError(len(salt))
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, SCRAM_ITERATIONS)
    stored = hashlib.sha256(hmac.digest(salted, b"Client Key", "sha256")).digest()
    server = hmac.digest(salted, b"Server Key", "sha256")
    encoded = [base64.b64encode(part).decode("ascii") for part in (salt, stored, server)]
    return f"SCRAM-SHA-256${SCRAM_ITERATIONS}:{encoded[0]}${encoded[1]}:{encoded[2]}"


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

    ``signer``: its ``rw`` and ``purge`` roles belong to ``ledger_signer``, not to ``ledger_app``. Callers set
    it from the owner's manifest (``PackageManifest.isolated_in == "signer"``), never from the schema's name.
    """

    schema: str
    signer: bool = False

    def __post_init__(self) -> None:
        if not is_schema_name(self.schema):
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
    """The passwords of the login roles (local and test values only; production takes them from secrets).

    They are kept out of the ``repr``, so that no log line or traceback shows one.
    """

    app: str = field(repr=False)
    migrator: str = field(repr=False)
    signer: str | None = field(default=None, repr=False)


def _role(name: str, options: str) -> tuple[str, str]:
    quoted = identifier(name)
    return (
        f"DO $$ BEGIN CREATE ROLE {quoted}; EXCEPTION WHEN duplicate_object THEN NULL; END $$",
        f"ALTER ROLE {quoted} WITH {options}",
    )


def _login(name: str, password: str, salt: Salting) -> tuple[str, str]:
    return _role(name, f"{_LOGIN} PASSWORD {_literal(scram_verifier(password, salt=salt(name)))}")


def _member(role: str, login: str) -> str:
    return f"GRANT {identifier(role)} TO {identifier(login)} {_MEMBER}"


_KEPT_BY_LOGIN: Final = {APP: "_(rw|purge)$", MIGRATOR: "_owner$", SIGNER: "_(rw|purge)$"}
"""The roles a login may stay a member of although this run names them not: another database's schemas.

Accepted risk: the families go by name, so ``ledger_app`` and ``ledger_signer`` keep a membership in any role
whose name ends ``_rw`` or ``_purge`` (``ledger_migrator`` in any ending ``_owner``), whoever made it. Making
such a role and granting it takes ``CREATEROLE`` or a superuser, who could grant the logins anything anyway;
the roles of the schemas a run names are checked exactly.
"""


_STRAYS: Final = sql.SQL("""DO $$ DECLARE stray record; BEGIN
FOR stray IN
    SELECT granted.rolname AS role, member.rolname AS member, grantor.rolname AS grantor
    FROM pg_auth_members AS membership
    JOIN pg_roles AS granted ON granted.oid = membership.roleid
    JOIN pg_roles AS member ON member.oid = membership.member
    JOIN pg_roles AS grantor ON grantor.oid = membership.grantor
    WHERE NOT ((granted.rolname, member.rolname) IN ({expected})
               AND NOT membership.admin_option AND NOT membership.inherit_option AND membership.set_option)
      AND member.rolname <> current_user
      AND (granted.rolname IN ({granted}) OR member.rolname IN ({groups}) OR {families}){kept}
LOOP
    EXECUTE format('REVOKE %I FROM %I GRANTED BY %I CASCADE', stray.role, stray.member, stray.grantor);
END LOOP;
END $$""")


def _names(names: Sequence[str]) -> sql.Composed:
    return sql.SQL(", ").join(sql.Literal(name) for name in names)


def _revoke_strays(
    groups: Sequence[str],
    logins: Sequence[str],
    memberships: Sequence[tuple[str, str]],
    kept: Sequence[tuple[str, str]],
) -> str:
    """Revoke, whoever granted it, every membership no statement here granted, but those ``kept``.

    That is any membership in this run's roles (nobody is a member of a login either), any of this run's group
    roles in another role, and any of a login outside its family of roles (``_KEPT_BY_LOGIN``). A membership
    of an expected pair is kept only with this module's options: PostgreSQL 16 keeps one row per grantor, and
    the ``GRANT`` here normalizes the current user's row only. The role running the statements keeps its own
    (PostgreSQL makes a ``CREATEROLE`` creator a member of what it creates). ``kept`` pairs are left as they
    are, neither granted nor revoked. Every name is a constant or a checked schema's role, and goes in as a
    literal.
    """
    pair = sql.SQL("({}, {})")
    family = sql.SQL("(member.rolname = {} AND granted.rolname !~ {})")
    pairs = sql.SQL(", ").join(pair.format(sql.Literal(r), sql.Literal(m)) for r, m in kept)
    return _STRAYS.format(
        kept=sql.SQL("\n      AND (granted.rolname, member.rolname) NOT IN ({})").format(pairs)
        if kept
        else sql.SQL(""),
        expected=sql.SQL(", ").join(pair.format(sql.Literal(r), sql.Literal(m)) for r, m in memberships),
        granted=_names([*groups, *logins]),
        groups=_names(groups),
        families=sql.SQL(" OR ").join(
            family.format(sql.Literal(login), sql.Literal(kept)) for login, kept in _KEPT_BY_LOGIN.items()
        ),
    ).as_string()


def cluster_statements(
    schemas: Sequence[SchemaRoles], logins: Logins, *, salt: Salting = random_salt
) -> tuple[str, ...]:
    """The roles of every schema and the logins, with their memberships (run once per server).

    ``salt`` gives each login's verifier its salt; a generated file that must come out the same at every run
    passes one of its own. ``ledger_signer`` is a member of the platform's ``rw`` role (schedule leases) when
    a schema of the run is the signer login's; a run with none leaves that membership as it finds it, so that
    the roles one package's tests make never take it from another's on the same server.
    """
    statements = [
        *_role(RELAY, _GROUP),
        *_login(APP, logins.app, salt),
        *_login(MIGRATOR, logins.migrator, salt),
    ]
    names = [APP, MIGRATOR]
    signed = [schema.schema for schema in schemas if schema.signer]
    if signed:
        if logins.signer is None:
            raise InvalidSchemaNameError(signed[0], signer_without_password=True)
        statements += _login(SIGNER, logins.signer, salt)
        names.append(SIGNER)
    groups = [RELAY]
    memberships: list[tuple[str, str]] = []
    for schema in schemas:
        groups += [schema.owner, schema.rw, schema.purge]
        statements += [*_role(schema.owner, _GROUP), *_role(schema.rw, _GROUP), *_role(schema.purge, _GROUP)]
        memberships += [(schema.owner, MIGRATOR), (schema.rw, schema.login), (schema.purge, schema.login)]
    memberships.append((RELAY, APP))
    leases = (f"{PLATFORM}_rw", SIGNER)
    if signed:
        memberships.append(leases)
    statements += [_member(role, login) for role, login in memberships]
    statements.append(_revoke_strays(groups, names, memberships, () if signed else (leases,)))
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
