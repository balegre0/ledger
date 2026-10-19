"""invoicing's routes require the permissions its manifest declares, and no other (Decision 2)."""

from ledger_invoicing._manifest import MANIFEST
from ledger_invoicing.domain.permissions import INVOICES_CREATE, INVOICES_ISSUE, INVOICES_READ, INVOICES_VOID


def test_the_permissions_the_routes_require_are_the_ones_the_manifest_declares() -> None:
    declared = {permission.code: frozenset(permission.roles) for permission in MANIFEST.permissions}
    assert declared == {
        INVOICES_READ: frozenset({"owner", "admin", "accountant", "issuer", "viewer"}),
        INVOICES_CREATE: frozenset({"owner", "admin", "accountant", "issuer"}),
        INVOICES_ISSUE: frozenset({"owner", "admin", "accountant", "issuer"}),
        INVOICES_VOID: frozenset({"owner", "admin", "accountant"}),
    }
