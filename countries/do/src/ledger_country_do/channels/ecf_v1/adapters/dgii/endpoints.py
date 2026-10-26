"""Where DGII answers each service, per environment (DT-DGII, 02-01-2026).

A URL is a host, the environment's segment, the service and its resource:
``https://ecf.dgii.gov.do/testecf/recepcion/api/facturaselectronicas``. The RFCE and its query
(``consultarfce``) have a host of their own, ``fc.dgii.gov.do``. CerteCF publishes neither
``consultatrackids``, ``anularrango`` nor ``consultadirectorio``: asked there, the client refuses before it
sends anything (``ServiceNotInEnvironmentError``). The live
environment is reached on DGII's own hosts only (``LiveHostRefusedError``), compared exactly: a fake can stand
in for TesteCF and CerteCF, never for production. Every host is an origin and nothing more
(``HostRefusedError``): no path, user, query or fragment, and ``https``, except a fake on this machine
(``LOOPBACK``: 127.0.0.1, ::1, localhost) or on the local stack's network (a single-label name led by a
letter, a compose service such as ``fake-dgii``: no public name is one label; the assembly allows it in the
environments ``local`` and ``test`` alone), which may be plain ``http`` and, not being DGII's, never serves
the live environment. The ``ecf`` host is also the audience of DGII's seeds
(``DgiiHosts.audience``), so it must be one the signer can audit (fiscal-kit's ``is_audience``). DGII's paths
ignore case; DT-DGII's lower-case spelling is used.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final
from urllib.parse import urlsplit
lazy from collections.abc import Mapping

from ledger_country_do.channels.ecf_v1.domain.environments import (
    OFFICIAL_ECF_HOST,
    OFFICIAL_FC_HOST,
    Environment,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfError
from ledger_country_do.channels.ecf_v1.domain.printed import QrHosts
from ledger_fiscal_kit.ports import is_audience


class Service(StrEnum):
    """DGII's services an emitter calls, as errors name them."""

    SEED = "seed"
    VALIDATE_SEED = "validate_seed"
    RECEPTION = "reception"
    RESULT = "result"
    TRACK_IDS = "track_ids"
    RFCE = "rfce"
    RFCE_QUERY = "rfce_query"
    VOID_RANGES = "void_ranges"
    APPROVAL = "approval"
    DIRECTORY = "directory"


PATHS: Final[Mapping[Service, str]] = MappingProxyType(
    {
        Service.SEED: "autenticacion/api/autenticacion/semilla",
        Service.VALIDATE_SEED: "autenticacion/api/autenticacion/validarsemilla",
        Service.RECEPTION: "recepcion/api/facturaselectronicas",
        Service.RESULT: "consultaresultado/api/consultas/estado",
        Service.TRACK_IDS: "consultatrackids/api/trackids/consulta",
        Service.RFCE: "recepcionfc/api/recepcion/ecf",
        Service.RFCE_QUERY: "consultarfce/api/consultas/consulta",
        Service.VOID_RANGES: "anulacionrangos/api/operaciones/anularrango",
        Service.APPROVAL: "aprobacioncomercial/api/aprobacioncomercial",
        Service.DIRECTORY: "consultadirectorio/api/consultas/obtenerdirectorioporrnc",
    }
)
NOT_IN_CERTECF: Final = frozenset({Service.TRACK_IDS, Service.VOID_RANGES, Service.DIRECTORY})
ON_FC: Final = frozenset({Service.RFCE, Service.RFCE_QUERY})
"""The services DGII answers on its ``fc`` host."""


class ServiceNotInEnvironmentError(EcfConflictError):
    """DGII publishes no such service in the environment."""

    code: ClassVar[str] = "do_ecf_dgii_service_not_in_environment"

    def __init__(self, service: Service, environment: Environment) -> None:
        super().__init__(f"DGII has no {service.value} service in {environment.value}")


class LiveHostRefusedError(EcfError):
    """The live environment was configured on a host that is not DGII's: a defect of configuration."""

    code: ClassVar[str] = "do_ecf_live_host_refused"

    def __init__(self) -> None:
        super().__init__("the live environment is reached on DGII's own hosts only")


LOOPBACK: Final = frozenset({"127.0.0.1", "::1", "localhost"})
"""The hosts a fake on this machine answers on: plain ``http`` may reach them."""
LOCAL_SERVICE: Final = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
"""A single-label host name led by a letter (``134744072`` and ``0x7f000001`` are addresses some resolvers
read): a service of the local stack's network, which plain ``http`` may reach too, in the environments
``local`` and ``test`` alone (the assembly refuses it elsewhere: ``DgiiHosts.beyond_loopback``)."""


class HostRefusedError(EcfError):
    """A host that is not an ``https`` origin (or ``http`` on a fake's host): a configuration defect."""

    code: ClassVar[str] = "do_ecf_dgii_host_refused"

    def __init__(self) -> None:
        super().__init__(
            "DGII's hosts are each an https origin (http on a fake's only): no path, user, query, fragment"
        )


def _origin(host: str) -> bool:
    """Whether ``host`` is an origin without user information: ``https``, or ``http`` on a fake's host."""
    try:
        parts = urlsplit(host)
        hostname = parts.hostname
    except ValueError:
        return False
    return (
        (parts.scheme == "https" or (parts.scheme == "http" and _is_local(hostname)))
        and bool(hostname)
        and "@" not in parts.netloc
        and (parts.path, parts.query, parts.fragment) == ("", "", "")
        and not host.endswith(("?", "#"))
    )


def _is_local(hostname: str | None) -> bool:
    """Whether ``hostname`` is a fake's: loopback, or a single-label service name of the local stack."""
    return hostname is not None and (hostname in LOOPBACK or LOCAL_SERVICE.fullmatch(hostname) is not None)


def offered(service: Service, environment: Environment) -> bool:
    """Whether DGII answers ``service`` in ``environment``."""
    return environment is not Environment.CERTECF or service not in NOT_IN_CERTECF


@final
@dataclass(frozen=True, slots=True)
class DgiiHosts:
    """DGII's two hosts (scheme and authority, no trailing slash), or a fake's (see the module)."""

    ecf: str = OFFICIAL_ECF_HOST
    fc: str = OFFICIAL_FC_HOST

    def __post_init__(self) -> None:
        if not (_origin(self.ecf) and _origin(self.fc) and is_audience(self.audience)):
            raise HostRefusedError

    @property
    def audience(self) -> str:
        """DGII's seeds' audience: the ``ecf`` host, with its port if it names one (checked when built)."""
        return urlsplit(self.ecf).netloc

    @property
    def beyond_loopback(self) -> bool:
        """Whether either host is plain ``http`` to a local service's name, not to this machine."""
        return any(
            urlsplit(host).scheme == "http" and urlsplit(host).hostname not in LOOPBACK
            for host in (self.ecf, self.fc)
        )

    @property
    def official(self) -> bool:
        """Whether these are DGII's own hosts, exactly."""
        return (self.ecf, self.fc) == (OFFICIAL_ECF_HOST, OFFICIAL_FC_HOST)

    @property
    def qr(self) -> QrHosts:
        """The QR's hosts: these, checked (a QR names where its document went)."""
        return QrHosts(ecf=self.ecf, fc=self.fc)

    def url(self, service: Service, environment: Environment) -> str:
        """Where ``service`` answers in ``environment``."""
        if environment.live and not self.official:
            raise LiveHostRefusedError
        if not offered(service, environment):
            raise ServiceNotInEnvironmentError(service, environment)
        host = self.fc if service in ON_FC else self.ecf
        return f"{host}/{environment.value}/{PATHS[service]}"


OFFICIAL_HOSTS: Final = DgiiHosts()
