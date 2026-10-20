"""``ledger_testing.localstack``: the pinned LocalStack, S3 only, and buckets made as production's are."""

import re

from ledger_platform.config.settings import is_bucket_name
from ledger_testing.images import image
lazy from ledger_testing.localstack import LocalStack


def test_the_run_s_localstack_is_the_pinned_image_on_loopback(localstack: LocalStack) -> None:
    assert image("localstack").startswith("localstack/localstack:4.14.0@sha256:")
    assert re.fullmatch(r"http://(127\.0\.0\.1|localhost):\d+", localstack.endpoint_url)
    assert localstack.region == "us-east-1"


def test_a_bucket_is_new_each_time_with_object_lock_unless_told(localstack: LocalStack) -> None:
    client = localstack.client()
    locked, other, unlocked = localstack.bucket(), localstack.bucket(), localstack.bucket(object_lock=False)
    assert len({locked, other, unlocked}) == 3
    assert all(is_bucket_name(name) for name in (locked, other, unlocked))
    configuration = client.get_object_lock_configuration(Bucket=locked)["ObjectLockConfiguration"]
    assert configuration.get("ObjectLockEnabled") == "Enabled"
    assert client.get_bucket_versioning(Bucket=locked).get("Status") == "Enabled"
    assert client.get_bucket_versioning(Bucket=unlocked).get("Status") is None
