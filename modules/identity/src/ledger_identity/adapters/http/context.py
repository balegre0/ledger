"""What identity's routes are built with: its units, the caller, its access and the idempotency of POSTs."""

from dataclasses import dataclass
from typing import final

from ledger_identity.adapters.permissions import MembershipPermissions
from ledger_platform.http import Idempotency, company_in_path
lazy from ledger_identity.domain.text import TimeZone
lazy from ledger_kernel.jurisdiction import Jurisdiction
lazy from ledger_kernel.regimes import TaxIdSchemes
lazy from ledger_platform.auth import PermissionSource
lazy from ledger_platform.config.manifest import ChannelEntry
lazy from ledger_platform.data import BoundDatabase
lazy from ledger_platform.http import Authentication, Caller, InCompany
lazy from ledger_platform.runtime.registries import Regimes


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """What every router of identity shares, built once by the assembly."""

    database: BoundDatabase
    """identity's units, in its schema and role."""
    caller: Caller
    in_company: InCompany
    idempotency: Idempotency
    regimes: Regimes
    """The installed regimes: their schemes read a client's ``{scheme, value}``, their zones default."""
    channels: tuple[ChannelEntry, ...]
    """The manifest entries of the installed channels, by key: what ``/v1/countries`` says of them."""

    @property
    def tax_ids(self) -> TaxIdSchemes:
        """Every installed regime's tax id schemes."""
        return self.regimes.tax_id_schemes

    def default_time_zone(self, jurisdiction: Jurisdiction) -> TimeZone | None:
        """The zone the installed regime of ``jurisdiction`` runs its days in; none when no regime is of it.

        A subdivision with its own regime is judged by it; any other falls back to its country's.
        """
        for key in (jurisdiction.subdivision, jurisdiction.country):
            if key is not None and key.lower() in self.regimes:
                return TimeZone(self.regimes.get(key.lower()).time_zone)
        return None

    @classmethod
    def build(
        cls,
        *,
        database: BoundDatabase,
        authentication: Authentication,
        regimes: Regimes,
        channels: tuple[ChannelEntry, ...],
    ) -> ApiContext:
        """The context of the routes: the caller from the chassis, its access from identity's memberships."""
        caller = authentication.caller
        permissions = MembershipPermissions(database)

        async def permission_source() -> PermissionSource:
            return permissions

        return cls(
            database=database,
            caller=caller,
            in_company=company_in_path(caller, permission_source),
            idempotency=Idempotency(),
            regimes=regimes,
            channels=channels,
        )
