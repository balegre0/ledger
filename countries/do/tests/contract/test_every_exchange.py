"""Every ANECF, ACECF and ARECF written from random inputs is valid for both validators; an ACECF reads back.

libxml2 checks each document as it is written (the builders return only what their schema accepts);
``xmlschema``, which shares no code with it, checks it again here in the shape it takes once signed.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Final

import xmlschema
from hypothesis import given, settings
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.adapters.xml.acecf import render_acecf
from ledger_country_do.channels.ecf_v1.adapters.xml.anecf import render_anecf
from ledger_country_do.channels.ecf_v1.adapters.xml.arecf import render_arecf
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import read_inbound_acecf, read_inbound_arecf
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import PUBLISHED, Format
from ledger_country_do.channels.ecf_v1.domain.drafts import writable
from ledger_country_do.channels.ecf_v1.domain.encf import MAX_SEQUENCE, EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.exchanges import (
    ANNULMENT_RANGES,
    REJECTION_REASON,
    CommercialApproval,
    NotReceivedReason,
    Receipt,
    VoidedRange,
)
from ledger_fiscal_kit.xml.hardened import texts

SIGNATURE: Final = '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/>'
_TAX_IDS = st.from_regex(r"[0-9]{9}|[0-9]{11}", fullmatch=True)
_ENCF = st.builds(Encf, st.sampled_from(list(EcfType)), st.integers(1, MAX_SEQUENCE))
_MOMENTS = st.datetimes(
    min_value=datetime(2000, 1, 2, tzinfo=UTC).replace(tzinfo=None),  # hypothesis' bounds are naive
    max_value=datetime(2099, 12, 30, tzinfo=UTC).replace(tzinfo=None),
    timezones=st.sampled_from([UTC, timezone(timedelta(hours=-4)), timezone(timedelta(hours=9, minutes=30))]),
).map(lambda moment: moment.replace(microsecond=0))
_REASONS = st.text(st.characters(codec="utf-8"), min_size=1, max_size=REJECTION_REASON).filter(
    lambda text: writable(text) and bool(text.strip())
)


def _valid_for_xmlschema(format_: Format, document: bytes) -> None:
    root = f"</{PUBLISHED[format_].root}>"
    signed_shape = document.decode("utf-8").replace(root, SIGNATURE + root)
    xmlschema.XMLSchema10(DO_SCHEMAS.corrected(format_).decode("utf-8")).validate(signed_shape)


@st.composite
def _voided(draw: st.DrawFn) -> list[VoidedRange]:
    ranges: list[VoidedRange] = []
    for ecf_type in draw(st.sets(st.sampled_from(list(EcfType)), min_size=1)):
        # at most a tenth of the sequences per type: ten types never void more than ANECF counts
        bounds = sorted(draw(st.sets(st.integers(1, MAX_SEQUENCE // 10), min_size=1, max_size=40)))
        pairs = list(zip(bounds[::2], bounds[1::2], strict=False))
        if len(bounds) % 2:
            pairs.append((bounds[-1], bounds[-1]))
        ranges.extend(VoidedRange(ecf_type, first, last) for first, last in pairs)
    return draw(st.permutations(ranges))


@settings(max_examples=150, deadline=None)
@given(issuer=_TAX_IDS, ranges=_voided(), at=_MOMENTS)
def test_every_anecf_is_valid_and_counts_what_it_voids(
    issuer: str, ranges: list[VoidedRange], at: datetime
) -> None:
    document = render_anecf(issuer_rnc=issuer, ranges=ranges, at=at)
    _valid_for_xmlschema(Format.ANECF, document)
    (counted,) = texts(document, "Encabezado/CantidadeNCFAnulados")
    assert counted == str(sum(each.count for each in ranges))
    assert len(ranges) <= len(EcfType) * ANNULMENT_RANGES


@st.composite
def _approvals(draw: st.DrawFn) -> CommercialApproval:
    reason = draw(st.none() | _REASONS)
    return CommercialApproval(
        issuer_rnc=draw(_TAX_IDS),
        buyer_rnc=draw(_TAX_IDS),
        encf=draw(_ENCF),
        issued_on=draw(st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31))),
        total=draw(st.decimals(min_value=Decimal(0), max_value=Decimal("9999999999999999.99"), places=2)),
        approved=reason is None,
        reason=reason,
        at=draw(_MOMENTS),
    )


@settings(max_examples=200, deadline=None)
@given(approval=_approvals())
def test_every_acecf_is_valid_and_reads_back(approval: CommercialApproval) -> None:
    document = render_acecf(approval)
    _valid_for_xmlschema(Format.ACECF, document)
    assert read_inbound_acecf(document) == approval


@settings(max_examples=150, deadline=None)
@given(
    issuer=_TAX_IDS,
    buyer=_TAX_IDS,
    encf=_ENCF,
    reason=st.none() | st.sampled_from(list(NotReceivedReason)),
    at=_MOMENTS,
)
def test_every_arecf_is_valid_and_reads_back(
    issuer: str, buyer: str, encf: Encf, reason: NotReceivedReason | None, at: datetime
) -> None:
    receipt = Receipt(
        issuer_rnc=issuer, buyer_rnc=buyer, encf=encf, received=reason is None, reason=reason, at=at
    )
    document = render_arecf(receipt)
    _valid_for_xmlschema(Format.ARECF, document)
    assert texts(document, "DetalleAcusedeRecibo/Estado") == ("0" if reason is None else "1",)
    assert read_inbound_arecf(document) == receipt
