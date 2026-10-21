"""``PgDocuments``: a document kept and read back as it was, its draft through the platform codec.

Every signature signs what the request checked: the draft read back from ``draft`` (``jsonb``) must equal the
draft numbered, whatever it holds (references, country fields, no counterparty).
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Final
lazy from uuid import UUID

import pytest
from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError

from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.application.documents import (
    DocumentExistsError,
    EcfDocument,
    Signature,
)
from ledger_country_do.channels.ecf_v1.application.ports import Archived
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange
from ledger_country_do.channels.ecf_v1.domain.ecf import DeliveryState, EcfDocumentState, EmissionMode
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import RangeState, RangeStatus
from ledger_country_do.channels.ecf_v1.domain.references import AcceptedEcf
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.channels.ecf_v1.domain.status_map import NumberFate
from ledger_country_do_testing.fiscal import fiscal_draft
from ledger_fiscal_kit.drafts import DocumentRef, FiscalReference
from ledger_fiscal_kit.status import AuthorityMessage
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.regimes import DocumentKind
from ledger_kernel.tenancy import Mode, Tenant
lazy from ledger_fiscal_kit.drafts import FiscalDraft
lazy from ledger_platform.data.bound import BoundDatabase

NOW: Final = datetime(2026, 10, 20, 15, tzinfo=UTC)
ISSUED_ON: Final = date(2026, 10, 13)


def a_tenant() -> Tenant:
    return Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)


def owned(draft: FiscalDraft, tenant: Tenant) -> FiscalDraft:
    return replace(
        draft, organization=tenant.organization, company=tenant.company, document=DocumentRef(new_id())
    )


async def a_range(module_database: BoundDatabase, tenant: Tenant, ecf_type: EcfType) -> UUID:
    async with module_database.unit(tenant) as unit:
        ranges = bind(unit).ranges
        await ranges.series(Environment.TESTECF, ecf_type)
        state = RangeState(
            id=new_id(),
            ecf_type=ecf_type,
            first=1,
            last=10,
            next=2,
            expires_on=None,
            status=RangeStatus.ACTIVE,
        )
        await ranges.add(
            NumberingRange(environment=Environment.TESTECF, range=state, created_at=NOW, version=1)
        )
        return state.id


def document_of(draft: FiscalDraft, ecf_type: EcfType, range_id: UUID, *, route: Route) -> EcfDocument:
    return EcfDocument(
        id=new_id(),
        invoice=draft.document,
        environment=Environment.TESTECF,
        encf=Encf(ecf_type, 1),
        range_id=range_id,
        range_expires_on=None,
        state=EcfDocumentState.NUMBERED,
        route=route,
        delivery=DeliveryState.NOT_STARTED,
        issued_on=ISSUED_ON,
        signature=None,
        draft=draft,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def credit_note(tenant: Tenant) -> FiscalDraft:
    corrected = FiscalReference(document=DocumentRef(new_id()), issue_date=date(2026, 10, 1))
    draft = fiscal_draft(
        kind=DocumentKind.CREDIT_NOTE,
        references=(corrected,),
        fields={"modification_code": "3", "income_type": "01", "withholdings": {"0": {"itbis": "1.00"}}},
    )
    return owned(draft, tenant)


@pytest.mark.parametrize(
    ("ecf_type", "route"),
    [(EcfType.CREDIT_NOTE, Route.RECEPTION), (EcfType.CONSUMER_INVOICE, Route.RFCE)],
    ids=["a-34-with-references-and-fields", "a-32-without-counterparty"],
)
async def test_a_document_reads_back_as_kept_its_draft_included(
    module_database: BoundDatabase, ecf_type: EcfType, route: Route
) -> None:
    tenant = a_tenant()
    range_id = await a_range(module_database, tenant, ecf_type)
    draft = (
        credit_note(tenant)
        if ecf_type is EcfType.CREDIT_NOTE
        else owned(fiscal_draft(counterparty=None), tenant)
    )
    kept = document_of(draft, ecf_type, range_id, route=route)
    async with module_database.unit(tenant) as unit:
        await bind(unit).documents.add(kept)
    async with module_database.unit(tenant) as unit:
        documents = bind(unit).documents
        assert await documents.of_invoice(draft.document) == kept
        assert await documents.get(kept.id, lock=True) == kept
        assert await documents.get(new_id(), lock=False) is None
    assert kept.draft.fields == draft.fields


async def test_a_signed_document_reads_back_with_its_signature_and_counts_once_accepted(
    module_database: BoundDatabase,
) -> None:
    tenant = a_tenant()
    range_id = await a_range(module_database, tenant, EcfType.CONSUMER_INVOICE)
    draft = owned(fiscal_draft(counterparty=None), tenant)
    numbered = document_of(draft, EcfType.CONSUMER_INVOICE, range_id, route=Route.RFCE)
    signature = Signature(
        signed_at=NOW,
        security_code="AbC123",
        qr_url="https://fc.dgii.gov.do/testecf/consultatimbrefc?x",
        emission_mode=EmissionMode.NORMAL,
        ecf=Archived(key="do-ecf-v1/test/o/c/ecf/a.xml", sha256=bytes(32)),
        rfce=Archived(key="do-ecf-v1/test/o/c/rfce/b.xml", sha256=bytes(range(32))),
    )
    accepted = replace(
        numbered,
        state=EcfDocumentState.ACCEPTED,
        signature=signature,
        version=3,
        delivery=DeliveryState.NOT_APPLICABLE,
        submission_started_at=NOW,
        track_id_since=NOW,
        polls=2,
        outcome_code=1,
        sequence_used=True,
        number_fate=NumberFate.USED,
        messages=(AuthorityMessage(code="613", message="Fuera de tolerancia", lang="es"),),
    )
    async with module_database.unit(tenant) as unit:
        documents = bind(unit).documents
        await documents.add(numbered)
        await documents.changed(accepted)
    async with module_database.unit(tenant) as unit:
        documents = bind(unit).documents
        assert await documents.get(numbered.id, lock=False) == accepted
        assert await documents.accepted([draft.document, DocumentRef(new_id())]) == {
            draft.document: AcceptedEcf(encf=Encf(EcfType.CONSUMER_INVOICE, 1), issued_on=ISSUED_ON)
        }
        assert await documents.accepted([]) == {}


async def test_a_second_document_of_an_invoice_is_its_own_refusal_any_other_the_table_s(
    module_database: BoundDatabase,
) -> None:
    """An invoice numbered meanwhile is ``DocumentExistsError``; a number held twice stays a defect."""
    tenant = a_tenant()
    range_id = await a_range(module_database, tenant, EcfType.CONSUMER_INVOICE)
    draft = owned(fiscal_draft(counterparty=None), tenant)
    first = document_of(draft, EcfType.CONSUMER_INVOICE, range_id, route=Route.RFCE)
    async with module_database.unit(tenant) as unit:
        await bind(unit).documents.add(first)
    with pytest.raises(DocumentExistsError):
        async with module_database.unit(tenant) as unit:
            await bind(unit).documents.add(replace(first, id=new_id()))
    other = owned(fiscal_draft(counterparty=None), tenant)
    with pytest.raises(IntegrityError) as caught:
        async with module_database.unit(tenant) as unit:
            await bind(unit).documents.add(
                document_of(other, EcfType.CONSUMER_INVOICE, range_id, route=Route.RFCE)
            )
    assert isinstance(caught.value.orig, UniqueViolation)
