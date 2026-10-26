"""The shapes of the channel's public API: what clients send (unknown fields refused) and what they receive.

Ids are TypeIDs (``rng_…``; a delivery is named by its invoice's, ``inv_…``; §9.4), days ISO 8601, times
with their offset, enumerations their words (an e-CF type is DGII's two digits). A list answers a ``Page`` of
its views (§9.7). Public ids are formatted and read only here.
"""

from datetime import date, datetime
from typing import Annotated, ClassVar, Final, Literal, Self
lazy from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ledger_country_do.channels.ecf_v1.application.delivering import DeliveryNotFoundError
from ledger_country_do.channels.ecf_v1.application.ranges import RangeNotFoundError, RangeRequest
from ledger_country_do.channels.ecf_v1.domain.ecf import DeliveryState
from ledger_country_do.channels.ecf_v1.domain.encf import MAX_SEQUENCE, EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import Authorization, RangeStatus
from ledger_kernel.ids import InvalidPublicIdError, format_id, parse_id
lazy from ledger_country_do.channels.ecf_v1.application.documents import DeliveryOptions, EcfDocument
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange

RANGE_PREFIX: Final = "rng"
"""The numbering range's id prefix, as the manifest mints it."""
_SUFFIX: Final = "[0-7][0-9a-hjkmnp-tv-z]{25}"
"""A TypeID's 26 base32 characters (Crockford's alphabet, lower case): 128 bits, so the first is at most 7."""
RANGE_REF_PATTERN: Final = rf"^{RANGE_PREFIX}_{_SUFFIX}$"
"""What a range's public id looks like, as a schema states it."""

type RangeRef = Annotated[str, StringConstraints(pattern=RANGE_REF_PATTERN)]
"""A range, by its public id (``rng_01h455vb4pex5vsknk084sn02q``)."""
type Sequence10 = Annotated[int, Field(ge=1, le=MAX_SEQUENCE)]
"""An e-NCF's sequence: 1 to 9 999 999 999 (its ten digits)."""


def range_ref(range_id: UUID) -> str:
    """The public id of a range."""
    return format_id(RANGE_PREFIX, range_id)


def parse_range_ref(text: str) -> UUID:
    """The range ``text`` names; ``RangeNotFoundError`` for anything but a public id of one."""
    try:
        return parse_id(RANGE_PREFIX, text)
    except InvalidPublicIdError:
        raise RangeNotFoundError from None


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class NewRange(_Shape):
    """A range DGII authorized, registered in the request's mode for one of the channel's environments."""

    environment: Environment = Field(
        description=(
            "Where the range numbers documents: testecf or certecf in test mode, ecf in live mode. "
            "The company must be enrolled on do-ecf-v1 there."
        )
    )
    ecf_type: EcfType = Field(description="The e-CF type DGII authorized the range for (31 to 47).")
    first: Sequence10 = Field(description="The range's first sequence number.")
    last: Sequence10 = Field(description="Its last, no lower than first.")
    expires_on: date | None = Field(
        default=None,
        description=(
            "Its last day, as DGII authorized it (Santo Domingo's date). Required for every type but 32 and "
            "34, which DGII may authorize undated."
        ),
    )

    def request(self) -> RangeRequest:
        """What the use case registers."""
        authorization = Authorization(
            ecf_type=self.ecf_type, first=self.first, last=self.last, expires_on=self.expires_on
        )
        return RangeRequest(environment=self.environment, authorization=authorization)


class NumberingRangeView(_Shape):
    """A DGII-authorized range of the company, and how far it has numbered."""

    object: Literal["numbering_range"] = "numbering_range"
    id: RangeRef
    livemode: bool
    environment: Environment
    ecf_type: EcfType
    first: int
    last: int
    next: int = Field(description="The sequence it gives next; last + 1 once spent.")
    remaining: int = Field(description="How many numbers it has not given yet.")
    expires_on: date | None
    status: RangeStatus
    version: int
    created_at: datetime

    @classmethod
    def of(cls, registered: NumberingRange) -> Self:
        """The view of ``registered``."""
        state = registered.range
        return cls(
            id=range_ref(state.id),
            livemode=registered.environment.live,
            environment=registered.environment,
            ecf_type=state.ecf_type,
            first=state.first,
            last=state.last,
            next=state.next,
            remaining=state.remaining,
            expires_on=state.expires_on,
            status=state.status,
            version=registered.version,
            created_at=registered.created_at,
        )


INVOICE_PREFIX: Final = "inv"
"""The invoice's id prefix, as invoicing's manifest mints it: a delivery is named by its invoice."""


def invoice_ref(invoice: UUID) -> str:
    """The public id of an invoice."""
    return format_id(INVOICE_PREFIX, invoice)


def parse_invoice_ref(text: str) -> UUID:
    """The invoice ``text`` names; ``DeliveryNotFoundError`` for anything but a public id of one."""
    try:
        return parse_id(INVOICE_PREFIX, text)
    except InvalidPublicIdError:
        raise DeliveryNotFoundError from None


class DeliveryView(_Shape):
    """Where an invoice's e-CF stands in its delivery to the buyer (spec §8.6.5)."""

    object: Literal["delivery"] = "delivery"
    invoice: str = Field(description="The invoice (inv_…) whose e-CF is delivered.")
    livemode: bool
    encf: str
    delivery: DeliveryState
    reason: str | None = Field(description="Why it failed, failed last, or does not apply: a code.")
    version: int = Field(description="The e-CF's version, as the invoice's fiscal status carries it.")

    @classmethod
    def of(cls, document: EcfDocument) -> Self:
        """The view of ``document``'s delivery."""
        return cls(
            invoice=invoice_ref(document.invoice),
            livemode=document.environment.live,
            encf=str(document.encf),
            delivery=document.delivery,
            reason=document.delivery_reason,
            version=document.version,
        )


class DeliveryOptionsBody(_Shape):
    """How the company delivers to its buyers in the request's mode."""

    buyer_authentication: bool = Field(
        description=(
            "Whether a buyer's authentication seed is signed with the company's certificate for a token, "
            "when the buyer publishes one (off by default: a buyer that then requires a token fails its "
            "delivery with do_ecf_buyer_requires_authentication)."
        )
    )


class DeliveryOptionsView(_Shape):
    """The company's delivery options in a mode."""

    object: Literal["delivery_options"] = "delivery_options"
    livemode: bool
    buyer_authentication: bool

    @classmethod
    def of(cls, options: DeliveryOptions, *, livemode: bool) -> Self:
        """The view of ``options``."""
        return cls(livemode=livemode, buyer_authentication=options.buyer_authentication)
