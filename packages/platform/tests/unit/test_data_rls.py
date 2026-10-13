import pytest

from ledger_platform.data.rls import InvalidPolicyNameError, RlsScope, policy, predicate
from ledger_platform.data.roles import InvalidSchemaNameError

ORG = "(SELECT NULLIF(current_setting('ledger.organization_id', true), '')::uuid)"
COMPANY = "(SELECT NULLIF(current_setting('ledger.company_id', true), '')::uuid)"
MODE = "(SELECT NULLIF(current_setting('ledger.mode', true), ''))"
EXPIRED = "expires_at < now()"


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (RlsScope.TENANT, f'"organization_id" = {ORG} AND "company_id" = {COMPANY} AND "mode" = {MODE}'),
        (RlsScope.COMPANY, f'"organization_id" = {ORG} AND "company_id" = {COMPANY}'),
        (RlsScope.ORGANIZATION, f'"organization_id" = {ORG}'),
        (RlsScope.MODED_ORGANIZATION, f'"organization_id" = {ORG} AND "mode" = {MODE}'),
    ],
)
def test_each_scope_reads_the_settings_through_nullif_once_per_statement(
    scope: RlsScope, expected: str
) -> None:
    assert predicate(scope) == expected


def test_the_organization_column_can_be_named() -> None:
    assert predicate(RlsScope.ORGANIZATION, organization_column="id") == f'"id" = {ORG}'


@pytest.mark.parametrize("column", ["Id", "id; DROP", "", "x" * 64])
def test_the_organization_column_is_a_plain_name(column: str) -> None:
    with pytest.raises(InvalidPolicyNameError, match="column"):
        predicate(RlsScope.ORGANIZATION, organization_column=column)


def test_a_policy_per_command_forced_on_the_owner() -> None:
    rule = predicate(RlsScope.COMPANY)
    on = '"identity"."branches"'
    to = 'TO "identity_rw", "identity_owner"'
    assert policy("identity.branches", RlsScope.COMPANY) == (
        f"ALTER TABLE {on} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {on} FORCE ROW LEVEL SECURITY",
        f'CREATE POLICY "branches_select" ON {on} FOR SELECT {to} USING ({rule})',
        f'CREATE POLICY "branches_insert" ON {on} FOR INSERT {to} WITH CHECK ({rule})',
        f'CREATE POLICY "branches_update" ON {on} FOR UPDATE {to} USING ({rule}) WITH CHECK ({rule})',
        f'CREATE POLICY "branches_delete" ON {on} FOR DELETE {to} USING ({rule})',
    )


def test_an_append_only_table_has_no_update_or_delete_policy() -> None:
    statements = policy("probe.audit_events", RlsScope.ORGANIZATION, append_only=True)
    assert len(statements) == 4
    assert [statement.split()[2] for statement in statements[2:]] == [
        '"audit_events_select"',
        '"audit_events_insert"',
    ]


def test_a_purgeable_table_lets_the_purge_role_alone_read_and_delete_expired_rows() -> None:
    rule = predicate(RlsScope.MODED_ORGANIZATION)
    on = '"probe"."idempotency_keys"'
    statements = policy("probe.idempotency_keys", RlsScope.MODED_ORGANIZATION, purge_expired=True)
    to = 'TO "probe_rw", "probe_owner"'
    assert statements[2] == f'CREATE POLICY "idempotency_keys_select" ON {on} FOR SELECT {to} USING ({rule})'
    assert statements[5] == f'CREATE POLICY "idempotency_keys_delete" ON {on} FOR DELETE {to} USING ({rule})'
    purge = f'TO "probe_purge" USING ({EXPIRED})'
    assert statements[6:] == (
        f'CREATE POLICY "idempotency_keys_purge_select" ON {on} FOR SELECT {purge}',
        f'CREATE POLICY "idempotency_keys_purge_delete" ON {on} FOR DELETE {purge}',
    )
    assert not any("ledger.purge" in statement for statement in statements)


@pytest.mark.parametrize("table", ["branches", "Identity.branches", "identity.branches; DROP", "a.b.c"])
def test_a_table_must_be_schema_qualified_and_plain(table: str) -> None:
    with pytest.raises(InvalidPolicyNameError, match="schema-qualified") as raised:
        policy(table, RlsScope.TENANT)
    assert raised.value.code == "invalid_policy_name"


@pytest.mark.parametrize("table", ["public.things", "pg_catalog.things", "user.things"])
def test_a_table_in_a_schema_no_module_may_have_is_refused(table: str) -> None:
    with pytest.raises(InvalidSchemaNameError):
        policy(table, RlsScope.TENANT)
