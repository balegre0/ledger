"""xmlsec1, the reference implementation of XML Signature, as the independent verifier of our signatures.

The pack signs with lxml and cryptography; a test proves a signature right with an implementation sharing none
of that code: xmlsec1 1.2.41 of Debian trixie, in a container built from ``xmlsec1/Dockerfile`` (its base
pinned by digest, its package by version; Docker keeps the image, built once per machine). The container runs
the image by the ID the build returned, under a name of its own, without a network, on a read-only root with
``/verify`` in memory; it is removed when the block ends, and only it. The document and certificates travel in
commands, base64-encoded and cut in pieces each under Linux's limit on one argument, so a document of any size
gets through: no host file is shared with the container, so a test runs the same on Windows and Linux.
"""

import base64
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Final, final
lazy from collections.abc import Generator
lazy from datetime import datetime

from testcontainers.core.container import DockerContainer
from testcontainers.core.image import DockerImage

from ledger_country_do.channels.ecf_v1.domain.notation import NaiveMomentError

XMLSEC_IMAGE: Final = "ledger-do-xmlsec1:1.2.41"
CONTEXT: Final = Path(__file__).with_name("xmlsec1")
"""The image's build context: its Dockerfile, shipped as package data."""
_NAME: Final = "ledger-do-xmlsec1-"
_WORK: Final = "/verify"
"""Where the files land: a directory in memory, the only writable one."""
_CHUNK: Final = 64 * 1024
"""Base64 characters per command argument: Linux takes at most 128 KiB in one (``MAX_ARG_STRLEN``)."""
_APPEND: Final = 'printf %s "$1" >> "/verify/$2"'
_VERIFY: Final = (
    'moment="$1"'
    " && cd /verify && base64 -d signed.b64 > signed.xml && base64 -d trusted.b64 > trusted.pem"
    " && if [ -s untrusted.b64 ]; then base64 -d untrusted.b64 > untrusted.pem"
    " && set -- --untrusted-pem untrusted.pem; else set --; fi"
    ' && exec xmlsec1 --verify --trusted-pem trusted.pem "$@" --verification-time "$moment" signed.xml'
)
"""Decode the three files, then verify; ``--untrusted-pem`` only when intermediates were given."""
_CLEAN: Final = "rm -f /verify/*"
_TIME: Final = "%Y-%m-%d %H:%M:%S"


class XmlsecCommandError(RuntimeError):
    """A command that prepares a verification failed in the container: its exit code and xmlsec's output."""

    def __init__(self, exit_code: int | None, output: str) -> None:
        super().__init__(f"a command in the xmlsec1 container exited with {exit_code}: {output}")
        self.exit_code: int | None = exit_code
        self.output: str = output


@final
@dataclass(frozen=True, slots=True)
class Verdict:
    """What xmlsec1 said of a signature: whether it holds, and its own words (for a failing test's report)."""

    verified: bool
    output: str


def _encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@final
class Xmlsec:
    """A running xmlsec1 container."""

    __slots__ = ("_container",)

    def __init__(self, container: DockerContainer) -> None:
        self._container = container

    @classmethod
    @contextmanager
    def started(cls) -> Generator[Xmlsec]:
        """xmlsec1, built if this machine lacks the image, for the length of the block."""
        with DockerImage(path=CONTEXT, tag=XMLSEC_IMAGE, clean_up=False) as image:
            container = (
                DockerContainer(image.short_id)
                .with_name(f"{_NAME}{uuid.uuid4().hex}")
                .with_tmpfs_mount(_WORK)
                .with_kwargs(network_mode="none", read_only=True)
            )
            with container:
                yield cls(container)

    def run(self, *command: str) -> str:
        """``command``'s output in the container; ``XmlsecCommandError`` unless it exits with 0."""
        result = self._container.exec(list(command))
        output = result.output.decode("utf-8", "replace")
        if result.exit_code != 0:
            raise XmlsecCommandError(result.exit_code, output)
        return output

    def verify(self, signed: bytes, *, trusted: bytes, untrusted: bytes, at: datetime) -> Verdict:
        """Whether ``signed`` holds an enveloped signature by a certificate chaining to ``trusted`` (PEM).

        ``untrusted`` holds the intermediates (PEM, possibly none); ``at`` is the moment certificates are
        checked at, timezone-aware.
        """
        if at.utcoffset() is None:
            raise NaiveMomentError
        self.run("sh", "-c", _CLEAN)
        for name, data in (("signed.b64", signed), ("trusted.b64", trusted), ("untrusted.b64", untrusted)):
            encoded = _encoded(data)
            for start in range(0, len(encoded), _CHUNK):
                self.run("sh", "-c", _APPEND, "append", encoded[start : start + _CHUNK], name)
        moment = at.astimezone(UTC).strftime(_TIME)
        result = self._container.exec(["sh", "-c", _VERIFY, "verify", moment])
        return Verdict(result.exit_code == 0, result.output.decode("utf-8", "replace"))
