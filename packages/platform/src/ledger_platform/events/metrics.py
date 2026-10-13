"""The eventing metrics (§3.5): one instrument per name and registry, shared by every runner on it.

A registry refuses a second instrument of one name, and a worker runs a dispatcher and one inbox runner per
consumer on one registry, so each instrument is made once per registry. Without a registry (tests) an
instrument is made and exported nowhere. Labels never name a tenant.
"""

import weakref
from typing import Final
lazy from collections.abc import Sequence

from prometheus_client import Counter, Gauge
lazy from prometheus_client import CollectorRegistry

_COUNTERS: Final[weakref.WeakKeyDictionary[CollectorRegistry, dict[str, Counter]]] = (
    weakref.WeakKeyDictionary()
)
_GAUGES: Final[weakref.WeakKeyDictionary[CollectorRegistry, dict[str, Gauge]]] = weakref.WeakKeyDictionary()


def counter(
    registry: CollectorRegistry | None, name: str, documentation: str, labels: Sequence[str]
) -> Counter:
    """The counter ``name`` of ``registry`` (``<name>_total`` once exported), made on first use."""
    if registry is None:
        return Counter(name, documentation, labels, registry=None)
    made = _COUNTERS.setdefault(registry, {})
    found = made.get(name)
    if found is None:
        found = made[name] = Counter(name, documentation, labels, registry=registry)
    return found


def gauge(registry: CollectorRegistry | None, name: str, documentation: str, labels: Sequence[str]) -> Gauge:
    """The gauge ``name`` of ``registry``, made on first use."""
    if registry is None:
        return Gauge(name, documentation, labels, registry=None)
    made = _GAUGES.setdefault(registry, {})
    found = made.get(name)
    if found is None:
        found = made[name] = Gauge(name, documentation, labels, registry=registry)
    return found
