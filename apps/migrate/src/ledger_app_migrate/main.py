"""``ledger-migrate``: a one-shot job before each rollout (§3.3)."""

from ledger_platform.runtime import App, Settings


def main() -> int:
    """Every schema to head, platform first, as ``ledger_migrator`` over ``LEDGER_DATABASE_DIRECT_URL``.

    A failure raises: the process exits non-zero and the rollout stops.
    """
    App("migrate", settings=Settings()).migrate()
    return 0
