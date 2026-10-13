from uuid import NAMESPACE_URL, uuid5

from ledger_platform.auth.providers.zitadel import organization_of, project_roles_claim


def test_the_roles_of_a_project_are_in_a_claim_named_after_it() -> None:
    assert project_roles_claim("394892279531175940") == "urn:zitadel:iam:org:project:394892279531175940:roles"


def test_an_organization_id_is_the_same_wherever_it_is_derived() -> None:
    namespace = uuid5(NAMESPACE_URL, "urn:ledger:organization")
    assert organization_of("1001") == uuid5(namespace, "1001") == organization_of("1001")
    assert organization_of("1001") != organization_of("1002")
    assert organization_of("1001").version == 5
