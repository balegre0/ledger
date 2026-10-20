"""Hypothesis profiles for every package's tests (pytest loads this root conftest for each of them).

``ci`` is what the gate runs (``poe test`` sets ``HYPOTHESIS_PROFILE=ci`` unless it is set already): examples
derived from each test's name rather than drawn at random, so a red gate is red again on the same examples and
a busy machine never fails one on time (no deadline, no ``too_slow`` health check). Being the same every run,
it looks at twice the default number of examples. A developer's own ``pytest`` run keeps Hypothesis's default
profile: random examples, which explore more over many runs, and a failure printed with its seed. A test's own
``@settings`` still win over either.
"""

import os
from typing import Final

from hypothesis import HealthCheck, settings

CI: Final = "ci"

settings.register_profile(
    CI,
    derandomize=True,
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
