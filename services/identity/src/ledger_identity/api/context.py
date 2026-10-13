"""What identity's routes are built with: the runtime, the caller, its access, and what ``main`` wires."""

from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Final, final
lazy from collections.abc import Callable, Generator

from fastapi import Depends

from ledger_identity.api.models import Problem
from ledger_identity.domain.errors import StaleVersionError
from ledger_platform.routes import authenticated, company_in_path, precondition_failed
from ledger_platform.web import PROBLEM_JSON
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.authentication import TokenVerifier
lazy from ledger_platform.authorization import PermissionSource
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.idempotency import Idempotency
lazy from ledger_platform.outbox import Outbox
lazy from ledger_platform.routes import Caller, InCompany
lazy from ledger_platform.service import Runtime, RuntimeDependency

type BindUnit = Callable[[UnitOfWork, Outbox], IdentityUnit]
"""identity's unit of work over a chassis transaction (``adapters.postgres.unit.bind``)."""
type PermissionsOf = Callable[[Database], PermissionSource]
"""identity's permission source over its database (``adapters.permissions.MembershipPermissions``)."""
_PROBLEM_SCHEMA: Final = Problem.model_json_schema()
"""What every error answer holds, written into each answer: the document then needs no shared component."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """The dependencies every router shares, built once with the app.

    The routes see the adapters through ``bind`` and ``permissions`` only: ``main`` hands them in, so the API
    never imports an adapter.
    """

    runtime: RuntimeDependency[IdentitySettings]
    caller: Caller
    in_company: InCompany
    bind: BindUnit
    idempotency: Idempotency

    @classmethod
    def build(
        cls,
        *,
        runtime: RuntimeDependency[IdentitySettings],
        verifier: TokenVerifier,
        bind: BindUnit,
        permissions: PermissionsOf,
        idempotency: Idempotency,
    ) -> ApiContext:
        """The context of identity's routes: the caller from ``verifier``, its access from ``permissions``."""
        caller = authenticated(verifier)

        async def permission_source(
            current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
        ) -> PermissionSource:
            return permissions(current.database)

        return cls(
            runtime=runtime,
            caller=caller,
            in_company=company_in_path(caller, permission_source),
            bind=bind,
            idempotency=idempotency,
        )


def problems(*statuses: int) -> dict[int | str, dict[str, object]]:
    """The error answers an operation documents: RFC 9457 problem documents, ``application/problem+json``.

    Written out: FastAPI's ``model`` would document them as ``application/json``, which no error is sent as.
    """
    answers: dict[int | str, dict[str, object]] = {
        status: {
            "description": HTTPStatus(status).phrase,
            "content": {PROBLEM_JSON: {"schema": _PROBLEM_SCHEMA}},
        }
        for status in statuses
    }
    return answers


@contextmanager
def precondition_checked() -> Generator[None]:
    """An update whose ``If-Match`` names no current version answers 412, as RFC 9110 says, not 409."""
    try:
        yield
    except StaleVersionError as error:
        raise precondition_failed() from error
