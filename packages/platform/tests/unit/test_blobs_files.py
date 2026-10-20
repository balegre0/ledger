"""``FileImmutableStore``: write-once files kept until a date, read back against their digest."""

import asyncio
import hashlib
import stat
from datetime import date
from typing import Final
lazy from pathlib import Path

import pytest

from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_platform.blobs.files import FileImmutableStore
from ledger_platform.blobs.store import (
    MAX_KEY,
    BlobDigestMismatchError,
    BlobNotFoundError,
    BlobStoreUnavailableError,
    ImmutableStore,
    ObjectConflictError,
    StoredObject,
    require_key,
)

KEY: Final = "do-ecf-v1/test/org/company/ecf/0a1b.xml"
CONTENT: Final = b"<ECF>\r\n<eNCF>E310000000001</eNCF>\n</ECF>"
"""Both line endings: a store that wrote in text mode would change them."""
DIGEST: Final = hashlib.sha256(CONTENT).digest()
RETAIN: Final = date(2036, 10, 19)
MEDIA: Final = "application/xml"
KEY_RULE: Final = "blob.key must be"


def entries(directory: Path) -> list[str]:
    return sorted(child.name for child in directory.iterdir())


def file_of(root: Path, key: str = KEY) -> Path:
    return root.joinpath(*key.split("/"))


async def test_put_once_then_get_returns_the_bytes_from_a_read_only_file(tmp_path: Path) -> None:
    store = FileImmutableStore(tmp_path)
    stored = await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    assert stored == StoredObject(key=KEY, sha256=DIGEST, size=len(CONTENT))
    assert await store.get(KEY, sha256=DIGEST) == CONTENT
    path = file_of(tmp_path)
    assert path.read_bytes() == CONTENT
    assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0  # nobody may write it
    assert await store.retained_until(KEY) == RETAIN
    assert entries(path.parent) == [".0a1b.xml.retain", "0a1b.xml"]


async def test_the_store_is_an_immutable_store_and_names_its_root(tmp_path: Path) -> None:
    store = FileImmutableStore(tmp_path)
    assert isinstance(store, ImmutableStore)
    assert store.root == tmp_path


async def test_the_same_bytes_again_answer_the_stored_object_and_keep_its_retention(tmp_path: Path) -> None:
    store = FileImmutableStore(tmp_path)
    first = await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    written = file_of(tmp_path).stat().st_mtime_ns
    for later in (date(2030, 1, 1), date(2050, 1, 1)):  # neither shortened nor extended
        assert await store.put_once(KEY, CONTENT, retain_until=later, media_type=MEDIA) == first
    assert await store.retained_until(KEY) == RETAIN
    assert file_of(tmp_path).stat().st_mtime_ns == written


async def test_other_bytes_under_a_key_are_a_conflict_and_change_nothing(tmp_path: Path) -> None:
    store = FileImmutableStore(tmp_path)
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(ObjectConflictError) as raised:
        await store.put_once(KEY, b"<ECF/>", retain_until=RETAIN, media_type=MEDIA)
    assert (raised.value.code, raised.value.kind) == ("blob_conflict", ErrorKind.INTERNAL)
    assert file_of(tmp_path).read_bytes() == CONTENT
    assert entries(file_of(tmp_path).parent) == [".0a1b.xml.retain", "0a1b.xml"]  # no staged file left


async def test_writers_racing_for_a_key_store_it_once(tmp_path: Path) -> None:
    store = FileImmutableStore(tmp_path)
    stored = await asyncio.gather(
        *(store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA) for _ in range(8))
    )
    assert set(stored) == {StoredObject(key=KEY, sha256=DIGEST, size=len(CONTENT))}
    assert entries(file_of(tmp_path).parent) == [".0a1b.xml.retain", "0a1b.xml"]


async def test_get_checks_the_digest_and_says_what_is_missing(tmp_path: Path) -> None:
    store = FileImmutableStore(tmp_path)
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(BlobDigestMismatchError) as mismatched:
        await store.get(KEY, sha256=hashlib.sha256(b"other").digest())
    assert mismatched.value.code == "blob_digest_mismatch"
    with pytest.raises(BlobNotFoundError) as missing:
        await store.get("do-ecf-v1/test/none.xml", sha256=DIGEST)
    assert missing.value.code == "blob_not_found"
    with pytest.raises(BlobNotFoundError):
        await store.retained_until("do-ecf-v1/test/none.xml")
    with pytest.raises(InvalidValueError, match=r"blob.sha256 must be a SHA-256 digest \(32 bytes\)"):
        await store.get(KEY, sha256=DIGEST[:31])


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/do-ecf-v1/x.xml",
        "do-ecf-v1/../x.xml",
        "do-ecf-v1/..x.xml",
        "do-ecf-v1/./x.xml",
        "do-ecf-v1//x.xml",
        "do-ecf-v1/.x.xml",
        "do-ecf-v1/x.xml/",
        "Do-ecf-v1/x.xml",
        "do-ecf-v1\\x.xml",
        "do-ecf-v1/x y.xml",
        "a" * (MAX_KEY + 1),
    ],
)
async def test_a_key_that_could_leave_its_place_is_refused(tmp_path: Path, key: str) -> None:
    store = FileImmutableStore(tmp_path)
    with pytest.raises(InvalidValueError, match=KEY_RULE):
        await store.put_once(key, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(InvalidValueError, match=KEY_RULE):
        await store.get(key, sha256=DIGEST)
    with pytest.raises(InvalidValueError, match=KEY_RULE):
        await store.retained_until(key)
    assert entries(tmp_path) == []


def test_a_key_is_at_most_its_length_and_made_of_plain_segments() -> None:
    longest = "/".join(["a" * 128, "b" * 127, "c" * 127, "d" * 127])
    assert len(longest) == MAX_KEY
    require_key(longest)
    require_key("do-ecf-v1/test/0199e3a0-0000-7000-8000-000000000001/ecf/ab12.xml")
    with pytest.raises(InvalidValueError, match=KEY_RULE):
        require_key(longest + "e")


async def test_a_store_that_cannot_write_or_read_is_unavailable(tmp_path: Path) -> None:
    blocker = tmp_path / "root"
    blocker.write_bytes(b"a file where the root's directory should be")
    with pytest.raises(BlobStoreUnavailableError) as raised:
        await FileImmutableStore(blocker).put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    assert (raised.value.code, raised.value.kind) == ("blob_store_unavailable", ErrorKind.UNAVAILABLE)
    store = FileImmutableStore(tmp_path / "store")
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(BlobStoreUnavailableError):  # a directory where an object should be
        await store.get("do-ecf-v1/test", sha256=DIGEST)
    (tmp_path / "store" / "do-ecf-v1" / ".test.retain").mkdir()
    with pytest.raises(BlobStoreUnavailableError):
        await store.retained_until("do-ecf-v1/test")


def test_a_stored_object_names_a_sha256_and_a_size() -> None:
    with pytest.raises(
        InvalidValueError, match=r"stored_object.sha256 must be a SHA-256 digest \(32 bytes\)"
    ):
        StoredObject(key=KEY, sha256=b"short", size=0)
    with pytest.raises(InvalidValueError, match=r"stored_object.size must be 0 or more"):
        StoredObject(key=KEY, sha256=DIGEST, size=-1)
