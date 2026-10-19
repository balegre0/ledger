"""invoicing's assembly (§3.10): what the apps mount of it.

The ``api`` app mounts its router, ``migrate`` runs its revisions, and every app checks its schema is current
(readiness). identity, which its manifest depends on, is asked through ``IdentityApi``: for the company, its
establishment and what a caller may do there. The regime that prices a document is the installed one that
governs the company's jurisdiction. Every app binds its API (``InvoicingApi``) for the modules that depend on
it; it publishes ``invoicing.invoice.issued`` and ``voided`` (``events.py``), consumes none, and runs no job.
"""

from typing import Final

from ledger_identity.api import IDENTITY_API
from ledger_invoicing._manifest import MANIFEST
from ledger_invoicing.adapters.http import invoices
from ledger_invoicing.adapters.http.context import ApiContext
from ledger_invoicing.adapters.identity import IdentityApiLookup
from ledger_invoicing.adapters.postgres.schema import MIGRATIONS, migrations
from ledger_invoicing.adapters.postgres.unit import PgUnits
from ledger_invoicing.adapters.regimes import regime_of
from ledger_invoicing.api import INVOICING_API
from ledger_invoicing.application.commands import InvoicingCommands
from ledger_invoicing.application.facade import InvoicingFacade
from ledger_kernel.ids import new_id
from ledger_platform.runtime import Module, ModuleParts
lazy from ledger_platform.runtime import ModuleContext


def assemble(context: ModuleContext) -> ModuleParts:
    """What invoicing hands the app: its router, its API for the modules that depend on it, its migrations."""
    identity = context.apis.get(IDENTITY_API)
    units = PgUnits(context.database)
    commands = InvoicingCommands(
        units=units,
        lookup=IdentityApiLookup(identity),
        regime_of=regime_of(context.regimes),
        clock=context.clock,
        new_id=new_id,
    )
    api = ApiContext.build(
        database=context.database,
        authentication=context.auth,
        identity=identity,
        commands=commands,
        tax_ids=context.regimes.tax_id_schemes,
    )
    return ModuleParts(
        api=INVOICING_API.bind(InvoicingFacade(api.queries)),
        routers=(invoices.router(api),),
        migrations=migrations(),
    )


MODULE: Final = Module(manifest=MANIFEST, assemble=assemble, migrations=MIGRATIONS)
"""What the entry point ``ledger.modules: invoicing`` names (Decision 13)."""
