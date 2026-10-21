"""Channel ``do-ecf-v1``: DGII's electronic fiscal vouchers (e-CF), format v1.0 (spec §8.6).

Layered as every v2 package (spec §4.5): ``domain`` (the standard library and the regime) <- ``application``
(ports and use cases) <- ``adapters`` (DGII's XSDs and XML, the signature profile, DGII's and buyers' web
services, PostgreSQL, the public routes). Above them, ``settings`` (``LEDGER_DO_ECF_*``) and ``assembly``,
whose ``CHANNEL`` the entry point names: nothing here imports either, so a domain module loads alone.
"""
