"""The testing package's own tests: its hook prepares nothing, they make the databases they need."""

lazy from ledger_testing.postgres import Template


def pytest_ledger_template(template: Template) -> None:
    """The testing package's own tests need nothing in the template."""
    del template
