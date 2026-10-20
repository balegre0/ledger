"""The shapes of fiscal's public API: what clients send (unknown fields refused) and what they receive.

Ids are TypeIDs (``enr_…``, §9.4), times ISO 8601 with their offset, enumerations their lowercase words. A
list answers a ``Page`` of its views (§9.7). Public ids are formatted and read only here.
"""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ledger_fiscal.domain.enrollments import MAX_REASON, EnrollmentId
from ledger_fiscal.domain.errors import ENROLLMENT_PREFIX, EnrollmentNotFoundError
from ledger_kernel.ids import InvalidPublicIdError, format_id, parse_id
from ledger_kernel.tenancy import Mode
lazy from ledger_fiscal.domain.enrollments import Enrollment

_SUFFIX: Final = "[0-7][0-9a-hjkmnp-tv-z]{25}"
"""A TypeID's 26 base32 characters (Crockford's alphabet, lower case): 128 bits, so the first is at most 7."""
ENROLLMENT_REF_PATTERN: Final = rf"^{ENROLLMENT_PREFIX}_{_SUFFIX}$"
"""What an enrollment's public id looks like, as a schema states it."""
MAX_KEY: Final = 64
"""The longest channel key or environment name a request may name: longer ones name none."""
_REASON_TEXT: Final = 4 * MAX_REASON
"""The longest reason text read: room for white space ``parse_reason`` collapses, and no more."""

type EnrollmentRef = Annotated[str, StringConstraints(pattern=ENROLLMENT_REF_PATTERN)]
"""An enrollment, by its public id (``enr_01h455vb4pex5vsknk084sn02q``)."""


def enrollment_ref(enrollment: EnrollmentId) -> str:
    """The public id of ``enrollment``."""
    return format_id(ENROLLMENT_PREFIX, enrollment)


def parse_enrollment_ref(text: str) -> EnrollmentId:
    """The enrollment ``text`` names; ``EnrollmentNotFoundError`` for anything but a public id of one."""
    try:
        return EnrollmentId(parse_id(ENROLLMENT_PREFIX, text))
    except InvalidPublicIdError:
        raise EnrollmentNotFoundError from None


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class NewEnrollment(_Shape):
    """An enrollment of the request's company, in the request's mode."""

    channel: str = Field(
        min_length=1, max_length=MAX_KEY, description="An installed channel's key.", examples=["do-ecf-v1"]
    )
    environment: str = Field(
        min_length=1,
        max_length=MAX_KEY,
        description=(
            "One of the channel's environments, of the request's mode: a live enrollment submits to a live "
            "environment only (ecf), a test one never does (testecf, certecf)."
        ),
        examples=["testecf"],
    )


class DisableBody(_Shape):
    """Why an enrollment is disabled."""

    reason: str = Field(
        min_length=1,
        max_length=_REASON_TEXT,
        description=f"Shown on the enrollment: at most {MAX_REASON} characters, white space collapsed.",
    )


class RequirementsView(_Shape):
    """What the channel required of the company at the enrollment's last review."""

    currently_due: list[str] = Field(
        description="What the company must still provide, in the channel's words."
    )
    disabled_reason: str | None = Field(
        description="Why the channel could not serve the company, in its words; null when nothing stopped it."
    )


class DisabledView(_Shape):
    """Why and when the enrollment was disabled."""

    reason: str
    at: datetime


class EnrollmentView(_Shape):
    """An enrollment of the company in a fiscal channel and one of its environments."""

    object: Literal["enrollment"] = "enrollment"
    id: EnrollmentRef
    livemode: bool
    channel: str
    environment: str
    status: Literal["pending", "active", "disabled"]
    requirements: RequirementsView
    disabled: DisabledView | None = Field(description="Set once the enrollment is disabled, for good.")
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, enrollment: Enrollment) -> Self:
        """The view of ``enrollment``."""
        disablement = enrollment.disablement
        return cls(
            id=enrollment_ref(enrollment.id),
            livemode=enrollment.mode is Mode.LIVE,
            channel=enrollment.channel,
            environment=enrollment.environment,
            status=enrollment.status.value,
            requirements=RequirementsView(
                currently_due=list(enrollment.currently_due), disabled_reason=enrollment.requirements_reason
            ),
            disabled=None
            if disablement is None
            else DisabledView(reason=disablement.reason, at=disablement.at),
            version=enrollment.version,
            created_at=enrollment.created_at,
            updated_at=enrollment.updated_at,
        )
