"""The channel's manifest entry and its domain agree: its key, its environments, which of them are live."""

from ledger_country_do.channels.ecf_v1.assembly import CHANNEL
from ledger_country_do.channels.ecf_v1.domain.channel import KEY
from ledger_country_do.channels.ecf_v1.domain.environments import Environment


def test_the_domain_s_environments_and_their_modes_are_the_manifest_s() -> None:
    """``Environment.live`` decides a range's mode in the use cases; the manifest's ``live_environments``
    decides an enrollment's (``ChannelDef.mode_of``): one answer for every environment."""
    entry = CHANNEL.entry
    assert (entry.key, CHANNEL.key) == (KEY, KEY)
    assert [environment.value for environment in Environment] == list(entry.environments)
    assert [environment.value for environment in Environment if environment.live] == list(
        entry.live_environments
    )
