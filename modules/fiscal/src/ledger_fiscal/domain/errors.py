"""What fiscal refuses, each with a stable code; a message never repeats what a client sent."""

from typing import ClassVar, Final
lazy from collections.abc import Mapping
lazy from uuid import UUID

from ledger_kernel.errors import ConflictError, InvalidInputError, NotFoundError
from ledger_kernel.ids import format_id

ENROLLMENT_PREFIX: Final = "enr"
"""The public id prefix of an enrollment (the manifest's ``[[ids]]``)."""


class EnrollmentNotFoundError(NotFoundError):
    """No enrollment of the request's company and mode by that id (another's is no different)."""

    code: ClassVar[str] = "fiscal_enrollment_not_found"

    def __init__(self) -> None:
        super().__init__("the company has no such enrollment in this mode")


class EnrollmentExistsError(ConflictError):
    """The company already has an enrollment on the channel in this mode that is not disabled (§7.6).

    The problem names it (``existing_id``): changing environment means disabling it first.
    """

    code: ClassVar[str] = "fiscal_enrollment_exists"

    def __init__(self, existing: UUID) -> None:
        super().__init__(
            "the company is enrolled on this channel in this mode: disable that enrollment first"
        )
        self.existing_id: Final = existing

    def problem_members(self) -> Mapping[str, str]:
        """``existing_id``: the public id of the enrollment that holds the channel (§9.6)."""
        return {"existing_id": format_id(ENROLLMENT_PREFIX, self.existing_id)}


class ChannelNotInstalledError(InvalidInputError):
    """An enrollment on a channel this installation does not have."""

    code: ClassVar[str] = "fiscal_channel_unknown"

    def __init__(self) -> None:
        super().__init__("no such fiscal channel is installed")


class EnvironmentUnknownError(InvalidInputError):
    """An environment the channel does not submit to."""

    code: ClassVar[str] = "fiscal_environment_unknown"

    def __init__(self) -> None:
        super().__init__("the channel has no such environment")


class EnvironmentNotAllowedError(InvalidInputError):
    """An environment of the other mode: live enrollments go to live environments only, test ones to none."""

    code: ClassVar[str] = "fiscal_environment_not_allowed"

    def __init__(self) -> None:
        super().__init__("the environment does not serve documents of this mode")


class InvalidDisableReasonError(InvalidInputError):
    """A reason to disable an enrollment that is empty, too long or not printable."""

    code: ClassVar[str] = "fiscal_disable_reason_invalid"

    def __init__(self, limit: int) -> None:
        super().__init__(f"reason must be 1 to {limit} printable characters once white space is collapsed")
