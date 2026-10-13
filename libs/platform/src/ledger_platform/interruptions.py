"""Interruptions of the connection to NATS, told to whoever keeps state that core NATS messages keep fresh.

A core NATS subscription is handed what is published while its connection is up, and nothing of what was
published while it was down: nats-py subscribes again when it reconnects, but what it missed is gone. Code
that keeps state up to date from such messages (a cache told what changed, ``identity_client``) listens here
and drops that state when the connection is lost, and again when it is back: what it kept may have changed
meanwhile, unannounced.

A service has one ``Interruptions`` (``Runtime.interruptions``), told from nats-py's ``disconnected_cb`` and
``reconnected_cb``; closing the connection on purpose is no interruption. Listeners are plain functions, told
on the event loop, one after the other: one that fails is logged, and the others are still told.
"""

from typing import Final, final
lazy from collections.abc import Callable

import structlog

from ledger_kernel.errors import error_type

_logger = structlog.stdlib.get_logger(__name__)

type Listener = Callable[[], None]
"""Told that the connection was lost, or is back: whatever was announced meanwhile was missed."""


@final
class _Listening:
    """One ``listen``: its own identity, so that the same function listening twice stops once at a time."""

    __slots__ = ("listener",)

    def __init__(self, listener: Listener) -> None:
        self.listener: Final = listener


@final
class Interruptions:
    """The listeners told of every interruption of a connection to NATS."""

    __slots__ = ("_listening",)

    def __init__(self) -> None:
        self._listening: list[_Listening] = []

    def listen(self, listener: Listener) -> Callable[[], None]:
        """Tell ``listener`` of every interruption from now on; calling what is returned stops it."""
        listening = _Listening(listener)
        self._listening.append(listening)

        def stop() -> None:
            if listening in self._listening:
                self._listening.remove(listening)

        return stop

    def interrupted(self) -> None:
        """Tell every listener that the connection was lost, or is back."""
        for listening in tuple(self._listening):  # a listener may stop listening while it is told
            try:
                listening.listener()
            except Exception as error:  # a listener may fail with anything; the others must still be told
                _logger.exception("nats.interruption_listener_failed", error=error_type(error))
