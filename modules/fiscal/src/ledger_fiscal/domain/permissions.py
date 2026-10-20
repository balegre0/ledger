"""fiscal's permissions, as its manifest declares them (identity's generated table grants them to roles)."""

from typing import Final

from ledger_kernel.access import parse_permission

ENROLLMENTS_READ: Final = parse_permission("fiscal.enrollments.read")
"""See the company's enrollments in fiscal channels and what each still requires."""
ENROLLMENTS_MANAGE: Final = parse_permission("fiscal.enrollments.manage")
"""Enroll the company in a fiscal channel, or disable an enrollment."""
