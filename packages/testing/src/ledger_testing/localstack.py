"""LocalStack, the S3 the S3 store's tests run against: the pinned image, S3 alone, one container a process.

The image is ``deploy/local/images.toml``'s ``localstack`` (4.14.0: it starts without an auth token and its
S3 keeps object lock in compliance mode, conditional writes and SHA-256 checksums as AWS does, except that it
stores a locked write to a bucket without object lock, which AWS refuses). The container listens on a port
Docker picks on loopback; it is ready when LocalStack's health endpoint says S3 is. LocalStack takes any
credentials: the tests sign with ``LocalStack.credential``, which is no secret.

``LocalStack.bucket`` makes a new bucket for each caller, with object lock enabled (and so versioning) as
production's is made, or without when a test needs a bucket that cannot hold the archive.
"""

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Generator

import botocore.session
from pydantic import TypeAdapter
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy
lazy from types_boto3_s3.client import S3Client

from ledger_platform.blobs.s3 import as_s3_client
from ledger_testing.images import image

_PORT: Final = 4566
_HEALTH: Final = "/_localstack/health"
_READY: Final = ('"s3": "available"', '"s3": "running"')
_STARTUP_SECONDS: Final = 120
_NUMBER: Final = TypeAdapter(int)
LOCALSTACK_REGION: Final = "us-east-1"
LOCALSTACK_CREDENTIAL: Final = "test"
"""What LocalStack takes as an access key and a secret key alike: it checks neither."""


def _s3_ready(health: str) -> bool:
    return any(state in health for state in _READY)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class LocalStack:
    """A running LocalStack: where its S3 answers, and in which region."""

    endpoint_url: str
    region: str = LOCALSTACK_REGION
    credential: str = LOCALSTACK_CREDENTIAL

    def client(self) -> S3Client:
        """A client of LocalStack's S3 (botocore's defaults, a session of its own: one per thread)."""
        client = botocore.session.Session().create_client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.credential,
            aws_secret_access_key=self.credential,
        )
        return as_s3_client(client)

    def bucket(self, *, object_lock: bool = True) -> str:
        """A new bucket's name: with object lock enabled (and versioning), unless told otherwise."""
        name = f"ledger-test-{uuid.uuid7().hex}"
        self.client().create_bucket(Bucket=name, ObjectLockEnabledForBucket=object_lock)
        return name


@contextmanager
def started_localstack() -> Generator[LocalStack]:
    """LocalStack (the pinned image, S3 alone) for the block, in a container named for the run."""
    container = (
        DockerContainer(image("localstack"))
        .with_name(f"ledger-tests-localstack-{uuid.uuid7().hex}")
        .with_env("SERVICES", "s3")
        .with_exposed_ports(_PORT)
        .waiting_for(
            HttpWaitStrategy(_PORT, _HEALTH)
            .for_response_predicate(_s3_ready)
            .with_startup_timeout(_STARTUP_SECONDS)
        )
    )
    with container:
        host = container.get_container_host_ip()
        port = _NUMBER.validate_python(container.get_exposed_port(_PORT))
        yield LocalStack(endpoint_url=f"http://{host}:{port}")
