"""Test configuration shared by every package."""

import asyncio
import sys
lazy from collections.abc import Callable, Mapping

lazy import pytest


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """Give every test a selector event loop.

    psycopg's async driver cannot run on Windows' default proactor loop.
    """
    del config, item
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}
