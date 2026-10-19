"""signer's adapters: PostgreSQL (its schema), its settings, its gRPC contract and its REST API.

Nothing here imports the custody (``adapters.custody``): Python runs this package with any of its submodules,
and only the signer's app may hold key material (ADR 0010; ``ledger-devtools check`` keeps it so).
"""
