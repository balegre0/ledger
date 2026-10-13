"""Channel ``do-ecf-v1``: DGII's electronic fiscal vouchers (e-CF), format v1.0 (spec §8.6).

Layered as every v2 package (spec §4.5): ``domain`` (the standard library and the regime) <- ``application``
(ports and use cases) <- ``adapters`` (DGII's XSDs and XML, the signature profile, DGII's and buyers' web
services). The runtime integration (``CHANNEL``, ``assembly``, jobs, PostgreSQL) is v2-M3's.
"""
