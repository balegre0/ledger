import uuid
from typing import Final

import pytest

from ledger_kernel.access import Principal, PrincipalKind, SubjectId
from ledger_kernel.ids import OrganizationId
from ledger_kernel.tenancy import OrganizationScope
from ledger_platform.data.audit import Actor, MissingActorError, current_actor
from ledger_platform.runtime.calls import Call, SystemCall, acting, actor_of

ORGANIZATION: Final = OrganizationId(uuid.uuid7())
SCOPE: Final = OrganizationScope(ORGANIZATION, None)
PRINCIPAL: Final = Principal(
    subject=SubjectId("user-1"),
    organization=ORGANIZATION,
    provider_organization="zitadel-org",
    kind=PrincipalKind.HUMAN,
)


def test_a_call_acts_as_its_principal_under_its_request_id() -> None:
    call = Call(principal=PRINCIPAL, scope=SCOPE, request_id="r-1")
    assert actor_of(call) == Actor("human:user-1", "r-1")  # the actor says it is a person
    assert call.traceparent is None


def test_a_system_call_acts_as_the_system_for_its_reason() -> None:
    assert actor_of(SystemCall(reason="consumer:x", scope=None, call_id="c")) == Actor(
        "system:consumer:x", "c"
    )


def test_a_system_call_without_an_id_gets_a_fresh_uuid7() -> None:
    first, second = SystemCall(reason="job:y", scope=SCOPE), SystemCall(reason="job:y", scope=SCOPE)
    assert uuid.UUID(first.call_id).version == 7
    assert first.call_id != second.call_id


def test_acting_binds_the_calls_actor_for_the_block() -> None:
    with acting(SystemCall(reason="job:y", scope=None, call_id="c")):
        assert current_actor() == Actor("system:job:y", "c")
    with pytest.raises(MissingActorError):
        current_actor()
