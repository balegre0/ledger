"""What signer announces: the types and versions its manifest declares, scoped to a company in no mode.

Payloads travel through the platform's codec and read back.
"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from pydantic import TypeAdapter

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.data.codec import canonical_json, payload_of
from ledger_signer._manifest import MANIFEST
from ledger_signer.events import CertificateExpiring, CertificateInstalled, CertificateRetired
lazy from ledger_kernel.events import Event

NOT_AFTER: Final = datetime(2027, 10, 19, 12, tzinfo=UTC)
COMPANY: Final = Tenant(organization=OrganizationId(new_id()), company=CompanyId(new_id()), mode=None)
CERTIFICATE: Final = new_id()
REPLACED: Final = new_id()
PAYLOAD: Final = TypeAdapter(dict[str, object])
TEXTS: Final = TypeAdapter(list[str])
TEXT: Final = TypeAdapter(str)
NUMBER: Final = TypeAdapter(int)
MOMENT: Final = TypeAdapter(datetime)


def installed() -> CertificateInstalled:
    return CertificateInstalled(
        scope=COMPANY,
        subject=CERTIFICATE,
        aggregate_version=1,
        certificate=CERTIFICATE,
        modes=(Mode.TEST, Mode.LIVE),
        backend="local",
        not_after=NOT_AFTER,
        replaces=(REPLACED,),
    )


def retired() -> CertificateRetired:
    return CertificateRetired(
        scope=COMPANY, subject=CERTIFICATE, aggregate_version=2, certificate=CERTIFICATE, reason="replaced"
    )


def expiring() -> CertificateExpiring:
    return CertificateExpiring(
        scope=COMPANY,
        subject=CERTIFICATE,
        aggregate_version=1,
        certificate=CERTIFICATE,
        not_after=NOT_AFTER,
        days_left=7,
    )


def through_json(event: Event) -> dict[str, object]:
    """``event``'s payload as a consumer reads it: the codec's bytes, parsed back."""
    return PAYLOAD.validate_json(canonical_json(payload_of(event)))


def test_the_types_and_versions_are_the_manifest_s() -> None:
    declared = {(event.type, event.version) for event in MANIFEST.events.publishes}
    assert {
        (CertificateInstalled.TYPE, CertificateInstalled.VERSION),
        (CertificateRetired.TYPE, CertificateRetired.VERSION),
        (CertificateExpiring.TYPE, CertificateExpiring.VERSION),
    } == declared
    assert {event.type: event.public for event in MANIFEST.events.publishes} == {
        "signer.certificate.installed": None,
        "signer.certificate.retired": None,
        "signer.certificate.expiring": "certificate.expiring",
    }


def test_every_event_is_its_company_s_in_no_mode_about_its_certificate() -> None:
    for event in (installed(), retired(), expiring()):
        assert (event.scope, event.subject) == (COMPANY, event.certificate)
    assert (installed().modes, installed().replaces) == ((Mode.TEST, Mode.LIVE), (REPLACED,))
    assert (expiring().not_after, expiring().days_left) == (NOT_AFTER, 7)


@pytest.mark.parametrize(
    "scope",
    [
        Tenant(organization=COMPANY.organization, company=COMPANY.company, mode=Mode.TEST),
        OrganizationScope(COMPANY.organization, None),
        None,
    ],
)
def test_an_event_outside_a_company_or_in_a_mode_is_a_defect(
    scope: Tenant | OrganizationScope | None,
) -> None:
    with pytest.raises(InvalidValueError, match="a company's tenant without a mode"):
        CertificateRetired(
            scope=scope, subject=CERTIFICATE, aggregate_version=2, certificate=CERTIFICATE, reason="retired"
        )


def test_an_event_about_another_certificate_than_its_subject_is_a_defect() -> None:
    with pytest.raises(InvalidValueError, match="the certificate"):
        CertificateRetired(
            scope=COMPANY, subject=new_id(), aggregate_version=2, certificate=CERTIFICATE, reason="retired"
        )


def test_an_installation_travels_and_reads_back() -> None:
    event = installed()
    payload = through_json(event)
    assert payload == {
        "certificate": str(CERTIFICATE),
        "modes": ["test", "live"],
        "backend": "local",
        "not_after": "2027-10-19T12:00:00+00:00",
        "replaces": [str(REPLACED)],
    }
    read = CertificateInstalled(
        scope=COMPANY,
        subject=event.subject,
        aggregate_version=event.aggregate_version,
        certificate=UUID(TEXT.validate_python(payload["certificate"])),
        modes=tuple(Mode(mode) for mode in TEXTS.validate_python(payload["modes"])),
        backend=TEXT.validate_python(payload["backend"]),
        not_after=MOMENT.validate_python(payload["not_after"]),
        replaces=tuple(UUID(replaced) for replaced in TEXTS.validate_python(payload["replaces"])),
    )
    assert read == event


def test_a_retirement_and_an_expiry_notice_travel_and_read_back() -> None:
    assert through_json(retired()) == {"certificate": str(CERTIFICATE), "reason": "replaced"}
    notice = through_json(expiring())
    assert notice == {
        "certificate": str(CERTIFICATE),
        "not_after": "2027-10-19T12:00:00+00:00",
        "days_left": 7,
    }
    read = CertificateExpiring(
        scope=COMPANY,
        subject=CERTIFICATE,
        aggregate_version=1,
        certificate=UUID(TEXT.validate_python(notice["certificate"])),
        not_after=MOMENT.validate_python(notice["not_after"]),
        days_left=NUMBER.validate_python(notice["days_left"]),
    )
    assert read == expiring()
