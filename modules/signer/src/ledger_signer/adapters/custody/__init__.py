"""Custody: the only code that touches a private key or a credential; the signer's app alone runs it.

ADR 0010: only the signer module and the apps of its role import it (import-linter's ``signer-custody``).
"""
