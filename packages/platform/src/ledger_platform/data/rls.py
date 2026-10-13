"""Row-level security policies from one builder (§6.3, audit D13): a policy per command, forced on the owner.

Settings are read through ``NULLIF(..., '')`` because ``current_setting(name, true)`` answers ``''``, not
``NULL``, once a pooled connection has seen the setting, and ``''::uuid`` would raise; wrapped in
``(SELECT ...)`` they are evaluated once per statement. A missing setting matches nothing: a unit without a
scope sees no protected row and can write none, and a unit without a mode sees no moded row (Decision 2).

The text is versioned like ``chassis_schema_v1``: migrations call ``policy`` and their meaning must not move,
so the tests pin every statement, and a different policy is a new function. Every policy is permissive
(PostgreSQL's default) and the only one per command on its table: permissive policies are OR-ed, so a second
one would widen the first.
"""

import re
from enum import StrEnum
from typing import ClassVar, Final, Literal, assert_type

from ledger_kernel.errors import LedgerError, excerpt

_TABLE: Final = re.compile(r"((?!pg_)[a-z][a-z0-9_]{1,30})\.([a-z][a-z0-9_]{0,40})", re.ASCII)
"""``<schema>.<table>``; the table part is short enough for ``<table>_select`` to stay an identifier."""
_COLUMN: Final = re.compile(r"[a-z][a-z0-9_]{0,62}", re.ASCII)
_ORGANIZATION: Final = "(SELECT NULLIF(current_setting('ledger.organization_id', true), '')::uuid)"
_COMPANY: Final = "(SELECT NULLIF(current_setting('ledger.company_id', true), '')::uuid)"
_MODE: Final = "(SELECT NULLIF(current_setting('ledger.mode', true), ''))"
_PURGING: Final = "((SELECT current_setting('ledger.purge', true)) = 'on' AND expires_at < now())"


class RlsScope(StrEnum):
    """Whose rows a table holds (§6.3; ``company`` is Decision 3)."""

    TENANT = "tenant"
    """A company's rows in one mode."""
    COMPANY = "company"
    """A company's rows shared by both modes (establishments, branches)."""
    ORGANIZATION = "organization"
    """An organization's rows, visible to it and to each of its companies."""
    MODED_ORGANIZATION = "moded_organization"
    """An organization's rows in one mode (API clients, idempotency keys)."""


class InvalidPolicyNameError(LedgerError):
    """A table or column name a policy cannot be written for: a defect of the migration that asked."""

    code: ClassVar[str] = "invalid_policy_name"

    def __init__(self, name: str, *, column: bool = False) -> None:
        super().__init__(
            f"the column name {excerpt(name)} is not a plain lower-case identifier"
            if column
            else f"the table {excerpt(name)} is not a schema-qualified table: <schema>.<table>, lower case"
        )


def predicate(scope: RlsScope, *, organization_column: str = "organization_id") -> str:
    """The condition a row of ``scope`` must meet in the current unit."""
    if _COLUMN.fullmatch(organization_column) is None:
        raise InvalidPolicyNameError(organization_column, column=True)
    match scope:
        case RlsScope.TENANT:
            return f"organization_id = {_ORGANIZATION} AND company_id = {_COMPANY} AND mode = {_MODE}"
        case RlsScope.COMPANY:
            return f"organization_id = {_ORGANIZATION} AND company_id = {_COMPANY}"
        case RlsScope.ORGANIZATION:
            return f"{organization_column} = {_ORGANIZATION}"
        case _:
            assert_type(scope, Literal[RlsScope.MODED_ORGANIZATION])
            return f"organization_id = {_ORGANIZATION} AND mode = {_MODE}"


def policy(
    table: str, scope: RlsScope, *, organization_column: str = "organization_id", purge_expired: bool = False
) -> tuple[str, ...]:
    """DDL that puts ``table`` (``schema.table``) under ``scope``: one permissive policy per command.

    ``purge_expired`` (idempotency keys, Decision 6): a unit that set ``ledger.purge = 'on'`` may also read
    and delete rows whose ``expires_at`` has passed, and nothing else: it can neither insert nor update
    outside its scope.
    """
    found = _TABLE.fullmatch(table)
    if found is None:
        raise InvalidPolicyNameError(table)
    name = found[2]
    rule = predicate(scope, organization_column=organization_column)
    reachable = f"({rule}) OR {_PURGING}" if purge_expired else rule
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {name}_select ON {table} FOR SELECT USING ({reachable})",
        f"CREATE POLICY {name}_insert ON {table} FOR INSERT WITH CHECK ({rule})",
        f"CREATE POLICY {name}_update ON {table} FOR UPDATE USING ({rule}) WITH CHECK ({rule})",
        f"CREATE POLICY {name}_delete ON {table} FOR DELETE USING ({reachable})",
    )
