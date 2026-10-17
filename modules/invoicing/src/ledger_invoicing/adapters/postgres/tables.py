"""invoicing's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

A document belongs to a company in a mode, and so does every row of it: they are under the tenant's row-level
security (``organization_id``, ``company_id``, ``mode``), and a foreign key between rows carries the tenant.
Every table is in schema ``invoicing`` (§6.2): invoicing's role reaches no other.
"""

from typing import Final

from ledger_platform.data import ModuleTable

SCHEMA: Final = "invoicing"
"""invoicing's schema: its tables and the chassis's (``chassis_schema_v1`` and ``_v2``), under its roles."""


class InvoicingTable(ModuleTable, schema=SCHEMA):
    """invoicing's own tables, in its schema; the chassis's are ``chassis_tables("invoicing")``."""
