"""identity's schema brought to this version's: the platform's runner on identity's revisions.

``MIGRATIONS`` runs them, from the tests and from the command line (``ledger-identity-migrate``, which the
one-shot container runs before the service starts); its ``readiness()`` is the service's ``migrations`` check.
identity keeps only its revisions (``migrations/versions``) and its tables; how they run is
``ledger_platform.migrations``'s.
"""

from typing import Final

from ledger_identity.settings import IdentitySettings
from ledger_platform.migrations import Migrations

SCRIPTS: Final = "ledger_identity.adapters.postgres:migrations"
"""Where the migration scripts are, as a package resource: they ship inside the wheel and the image."""
MIGRATIONS: Final = Migrations(SCRIPTS, owner="identity")


def main() -> int:
    """``ledger-identity-migrate``: bring the database of ``LEDGER_DATABASE_URL`` to this version's schema."""
    MIGRATIONS.upgrade(IdentitySettings().database_url.get_secret_value())
    return 0
