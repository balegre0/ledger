"""Where a company issues documents from: its establishments (§7.1).

An establishment is minimal: a code unique in the company, a name and the time zone that fixes the tax point
date of the documents it issues. Every company has a ``MAIN`` one from the day it is registered. What a
country adds (branches, emission points, issuing series) is the channel's to model, not identity's.
"""

from dataclasses import dataclass
from typing import Final, final
lazy from datetime import datetime

from ledger_identity.domain.ids import EstablishmentId
from ledger_identity.domain.text import parse_code, parse_name
from ledger_kernel.ids import new_id
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.text import Code, Name, TimeZone
lazy from ledger_kernel.tenancy import Tenant

MAIN_CODE: Final = parse_code("MAIN")
"""The code of the establishment a company is registered with."""
MAIN_NAME: Final = parse_name("Main")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Establishment:
    """An establishment of a company, known by a code unique in the company."""

    id: EstablishmentId
    tenant: Tenant
    code: Code
    name: Name
    time_zone: TimeZone
    created_at: datetime

    @classmethod
    def open(
        cls, company: Company, *, code: Code, name: Name, time_zone: TimeZone, now: datetime
    ) -> Establishment:
        """A new establishment of ``company``, which must be active."""
        company.require_active()
        return cls(
            id=EstablishmentId(new_id()),
            tenant=company.tenant,
            code=code,
            name=name,
            time_zone=time_zone,
            created_at=now,
        )

    @classmethod
    def main(cls, company: Company, *, time_zone: TimeZone, now: datetime) -> Establishment:
        """The company's ``MAIN`` establishment, opened with it."""
        return cls.open(company, code=MAIN_CODE, name=MAIN_NAME, time_zone=time_zone, now=now)
