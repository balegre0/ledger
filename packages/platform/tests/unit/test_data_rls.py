import pytest

from ledger_platform.data.rls import InvalidPolicyNameError, RlsScope, policy, predicate

ORG = "(SELECT NULLIF(current_setting('ledger.organization_id', true), '')::uuid)"
COMPANY = "(SELECT NULLIF(current_setting('ledger.company_id', true), '')::uuid)"
MODE = "(SELECT NULLIF(current_setting('ledger.mode', true), ''))"
PURGE = "((SELECT current_setting('ledger.purge', true)) = 'on' AND expires_at < now())"


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (RlsScope.TENANT, f"organization_id = {ORG} AND company_id = {COMPANY} AND mode = {MODE}"),
        (RlsScope.COMPANY, f"organization_id = {ORG} AND company_id = {COMPANY}"),
        (RlsScope.ORGANIZATION, f"organization_id = {ORG}"),
        (RlsScope.MODED_ORGANIZATION, f"organization_id = {ORG} AND mode = {MODE}"),
    ],
)
def test_each_scope_reads_the_settings_through_nullif_once_per_statement(
    scope: RlsScope, expected: str
) -> None:
    assert predicate(scope) == expected


def test_the_organization_column_can_be_named() -> None:
    assert predicate(RlsScope.ORGANIZATION, organization_column="id") == f"id = {ORG}"


@pytest.mark.parametrize("column", ["Id", "id; DROP", "", "x" * 64])
def test_the_organization_column_is_a_plain_name(column: str) -> None:
    with pytest.raises(InvalidPolicyNameError, match="column"):
        predicate(RlsScope.ORGANIZATION, organization_column=column)


def test_a_policy_per_command_forced_on_the_owner() -> None:
    rule = predicate(RlsScope.COMPANY)
    assert policy("identity.branches", RlsScope.COMPANY) == (
        "ALTER TABLE identity.branches ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE identity.branches FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY branches_select ON identity.branches FOR SELECT USING ({rule})",
        f"CREATE POLICY branches_insert ON identity.branches FOR INSERT WITH CHECK ({rule})",
        f"CREATE POLICY branches_update ON identity.branches FOR UPDATE USING ({rule}) WITH CHECK ({rule})",
        f"CREATE POLICY branches_delete ON identity.branches FOR DELETE USING ({rule})",
    )


def test_a_purgeable_table_lets_a_purging_unit_read_and_delete_expired_rows_only() -> None:
    rule = predicate(RlsScope.MODED_ORGANIZATION)
    statements = policy("probe.idempotency_keys", RlsScope.MODED_ORGANIZATION, purge_expired=True)
    assert statements[2] == (
        "CREATE POLICY idempotency_keys_select ON probe.idempotency_keys"
        f" FOR SELECT USING (({rule}) OR {PURGE})"
    )
    assert statements[3] == (
        f"CREATE POLICY idempotency_keys_insert ON probe.idempotency_keys FOR INSERT WITH CHECK ({rule})"
    )
    assert statements[4] == (
        "CREATE POLICY idempotency_keys_update ON probe.idempotency_keys"
        f" FOR UPDATE USING ({rule}) WITH CHECK ({rule})"
    )
    assert statements[5] == (
        "CREATE POLICY idempotency_keys_delete ON probe.idempotency_keys"
        f" FOR DELETE USING (({rule}) OR {PURGE})"
    )


@pytest.mark.parametrize("table", ["branches", "Identity.branches", "identity.branches; DROP", "a.b.c"])
def test_a_table_must_be_schema_qualified_and_plain(table: str) -> None:
    with pytest.raises(InvalidPolicyNameError, match="schema-qualified") as raised:
        policy(table, RlsScope.TENANT)
    assert raised.value.code == "invalid_policy_name"
