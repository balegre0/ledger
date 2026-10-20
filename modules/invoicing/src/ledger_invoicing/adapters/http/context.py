"""What invoicing's routes are built with: its use cases, the caller, its access and the keys of POSTs."""

from dataclasses import dataclass
from typing import Final, final

from ledger_invoicing.adapters.permissions import IdentityPermissions
from ledger_invoicing.application.queries import InvoicingQueries
from ledger_kernel.ids import new_id
from ledger_platform.http import Idempotency, company_in_header, ledger_mode_of
from ledger_platform.http.problems import current_request_id
from ledger_platform.runtime.calls import Call
lazy from ledger_identity.api import IdentityApi
lazy from ledger_invoicing.application.commands import InvoicingCommands
lazy from ledger_invoicing.application.issuing import IssuePolicies
lazy from ledger_kernel.regimes import TaxIdSchemes
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.auth import CompanyAccess, PermissionSource
lazy from ledger_platform.data import BoundDatabase
lazy from ledger_platform.http import Authentication, InCompany
lazy from ledger_platform.http.mode import ModeDependency

MAX_STORED_ANSWER: Final = 4 * 1024 * 1024
"""The answer an ``Idempotency-Key`` keeps at most: 4 MiB. A document's 1 MiB request can come back larger,
priced line by line (1000 lines, their taxes and amounts), and its key must keep that answer whole."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """What every route of invoicing shares, built once by the assembly."""

    database: BoundDatabase
    """invoicing's units, in its schema and role: where a POST claims its key with its change."""
    commands: InvoicingCommands
    queries: InvoicingQueries
    mode: ModeDependency
    """The request's mode, settled once its caller is proven (``ledger_mode_of``)."""
    in_company: InCompany
    """The company of ``Ledger-Company`` and what the caller may do there, as identity says."""
    idempotency: Idempotency
    tax_ids: TaxIdSchemes
    """Every installed regime's schemes: they read a buyer's ``{scheme, value}``."""

    @property
    def policies(self) -> IssuePolicies:
        """The ``IssuePolicy`` chain: what runs once a document is issued (and checked it before)."""
        return self.commands.policies

    @classmethod
    def build(
        cls,
        *,
        database: BoundDatabase,
        authentication: Authentication,
        identity: IdentityApi,
        commands: InvoicingCommands,
        tax_ids: TaxIdSchemes,
    ) -> ApiContext:
        """The context of the routes: the chassis's caller, its access in the company as identity says."""
        caller = authentication.caller
        permissions = IdentityPermissions(identity)

        async def permission_source() -> PermissionSource:
            return permissions

        return cls(
            database=database,
            commands=commands,
            queries=InvoicingQueries(units=commands.units),
            mode=ledger_mode_of(caller),
            in_company=company_in_header(caller, permission_source),
            idempotency=Idempotency(max_stored_body=MAX_STORED_ANSWER),
            tax_ids=tax_ids,
        )


def call_of(access: CompanyAccess, tenant: Tenant) -> Call:
    """The call a request makes: its caller, in ``tenant`` (its company in the request's mode)."""
    return Call(principal=access.principal, scope=tenant, request_id=current_request_id() or str(new_id()))
