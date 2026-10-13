"""The identity service's settings: the chassis's and the bearer-token policy, from ``LEDGER_*`` variables."""

from pydantic import Field

from ledger_platform.config.settings import ApiSettings


class IdentitySettings(ApiSettings):
    """identity configures nothing of its own yet; its name defaults to ``identity``."""

    service_name: str = Field(default="identity", pattern=r"^[a-z][a-z0-9-]{1,30}$")
