"""Where DGII answers each service, per environment (DT-DGII, 02-01-2026).

A URL is a host, the environment's segment, the service and its resource:
``https://ecf.dgii.gov.do/testecf/recepcion/api/facturaselectronicas``. The RFCE has a host of its own,
``fc.dgii.gov.do``. CerteCF publishes neither ``consultatrackids`` nor ``consultadirectorio``. The live
environment is reached on DGII's own hosts only (``LiveHostRefusedError``), compared exactly: a fake can stand
in for TesteCF and CerteCF, never for production. DGII's paths ignore case; DT-DGII's lower-case spelling is
used.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping

from ledger_country_do.channels.ecf_v1.domain.environments import (
    OFFICIAL_ECF_HOST,
    OFFICIAL_FC_HOST,
    Environment,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfError


class Service(StrEnum):
    """DGII's services an emitter calls, as errors name them."""

    SEED = "seed"
    VALIDATE_SEED = "validate_seed"
    RECEPTION = "reception"
    RESULT = "result"
    TRACK_IDS = "track_ids"
    RFCE = "rfce"
    DIRECTORY = "directory"


PATHS: Final[Mapping[Service, str]] = MappingProxyType(
    {
        Service.SEED: "autenticacion/api/autenticacion/semilla",
        Service.VALIDATE_SEED: "autenticacion/api/autenticacion/validarsemilla",
        Service.RECEPTION: "recepcion/api/facturaselectronicas",
        Service.RESULT: "consultaresultado/api/consultas/estado",
        Service.TRACK_IDS: "consultatrackids/api/trackids/consulta",
        Service.RFCE: "recepcionfc/api/recepcion/ecf",
        Service.DIRECTORY: "consultadirectorio/api/consultas/obtenerdirectorioporrnc",
    }
)
NOT_IN_CERTECF: Final = frozenset({Service.TRACK_IDS, Service.DIRECTORY})


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


def offered(service: Service, environment: Environment) -> bool:
    """Whether DGII answers ``service`` in ``environment``."""
    return environment is not Environment.CERTECF or service not in NOT_IN_CERTECF


@final
@dataclass(frozen=True, slots=True)
class DgiiHosts:
    """DGII's two hosts (scheme and authority, no trailing slash), or a fake's."""

    ecf: str = OFFICIAL_ECF_HOST
    fc: str = OFFICIAL_FC_HOST

    @property
    def official(self) -> bool:
        """Whether these are DGII's own hosts, exactly."""
        return (self.ecf, self.fc) == (OFFICIAL_ECF_HOST, OFFICIAL_FC_HOST)

    def url(self, service: Service, environment: Environment) -> str:
        """Where ``service`` answers in ``environment``."""
        if environment.live and not self.official:
            raise LiveHostRefusedError
        if not offered(service, environment):
            raise ServiceNotInEnvironmentError(service, environment)
        host = self.fc if service is Service.RFCE else self.ecf
        return f"{host}/{environment.value}/{PATHS[service]}"


OFFICIAL_HOSTS: Final = DgiiHosts()
