"""invoicing's permissions (Decision 2), as its manifest declares them.

Which built-in role holds which is identity's generated table (``ROLE_GRANTS``), asked through
``IdentityApi.permissions``.
"""

from typing import Final

from ledger_kernel.access import parse_permission

INVOICES_READ: Final = parse_permission("invoicing.invoices.read")
"""Read documents: owner, admin, accountant, issuer and viewer."""
INVOICES_CREATE: Final = parse_permission("invoicing.invoices.create")
"""Create invoices and notes, as drafts or issued: owner, admin, accountant and issuer."""
INVOICES_ISSUE: Final = parse_permission("invoicing.invoices.issue")
"""Issue a draft: owner, admin, accountant and issuer."""
INVOICES_VOID: Final = parse_permission("invoicing.invoices.void")
"""Void an issued document: owner, admin and accountant."""
