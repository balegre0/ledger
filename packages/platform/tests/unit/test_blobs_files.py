"""``FileImmutableStore``: write-once files kept until a date, read back against their digest.

The store contract every store keeps is ``ledger_testing.blobs.ImmutableStoreContract``'s; what is here is the
file store's own: read-only files, sidecars, directory flushes, a damaged directory.
"""

import hashlib
import os
import stat
from datetime import date
from typing import TYPE_CHECKING, Final, final, override
lazy from pathlib import Path

import pytest

from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_platform.blobs.files import FileImmutableStore, sync_directory
from ledger_platform.blobs.store import (
    MAX_KEY,
    BlobNotFoundError,
    BlobRecordCorruptError,
    BlobStoreUnavailableError,
    StoredObject,
    require_key,
)
from ledger_testing.blobs import ImmutableStoreContract

if TYPE_CHECKING:
    from ledger_platform.blobs.store import (
        ImmutableStore,
    )

KEY: Final = "do-ecf-v1/test/org/company/ecf/0a1b.xml"
CONTENT: Final = b"<ECF>\r\n<eNCF>E310000000001</eNCF>\n</ECF>"
"""Both line endings: a store that wrote in text mode would change them."""
DIGEST: Final = hashlib.sha256(CONTENT).digest()
RETAIN: Final = date(2036, 10, 19)
MEDIA: Final = "application/xml"
KEY_RULE: Final = "blob.key must be"


@final
class TestFileStoreContract(ImmutableStoreContract):
    """The store contract, kept by the file store."""

    @override
    def make(self, root: Path) -> ImmutableStore:
        return FileImmutableStore(root)


def entries(directory: Path) -> list[str]:
    return sorted(child.name for child in directory.iterdir())


def file_of(root: Path, key: str = KEY) -> Path:
    return root.joinpath(*key.split("/"))


def writable(path: Path) -> bool:
    return stat.S_IMODE(path.stat().st_mode) & 0o222 != 0


async def test_an_object_is_a_read_only_file_beside_its_retention(tmp_path: Path) -> None:
    store = FileImmutableStore(tmp_path)
    assert store.root == tmp_path
    stored = await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    assert stored == StoredObject(key=KEY, sha256=DIGEST, size=len(CONTENT))
    path = file_of(tmp_path)
    assert path.read_bytes() == CONTENT  # byte for byte: no line ending translated
    assert not writable(path)
    assert entries(path.parent) == [".0a1b.xml.retain", "0a1b.xml"]  # no staged file left


async def test_the_same_bytes_again_make_the_file_read_only_again(tmp_path: Path) -> None:
    """A write interrupted between its link and its chmod is completed by the next ``put_once``."""
    store = FileImmutableStore(tmp_path)
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    file_of(tmp_path).chmod(0o644)
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    assert not writable(file_of(tmp_path))


async def test_every_name_made_is_flushed_with_its_directory(tmp_path: Path) -> None:
    synced: list[Path] = []
    store = FileImmutableStore(tmp_path / "root", sync=synced.append)
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    folder = file_of(tmp_path / "root").parent
    created = [folder, *folder.parents][: len(KEY.split("/"))]  # root/do-ecf-v1/.../ecf, root included
    assert synced == [*(directory.parent for directory in reversed(created)), folder, folder]
    synced.clear()
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    assert synced == []  # nothing new was named


def test_a_directory_is_flushed_where_the_system_opens_one(tmp_path: Path) -> None:
    probe = tmp_path / "probe"
    probe.write_bytes(b"x")
    opened: list[int] = []

    def open_file(path: Path, flags: int) -> int:
        del path
        descriptor = os.open(probe, flags)
        opened.append(descriptor)
        return descriptor

    def refuse(path: Path, flags: int) -> int:
        del path, flags
        raise PermissionError

    sync_directory(tmp_path, opener=open_file)
    [descriptor] = opened
    with pytest.raises(OSError, match=r"Bad file descriptor|handle is invalid"):  # POSIX, Windows
        os.fstat(descriptor)  # closed
    sync_directory(tmp_path, opener=refuse)  # Windows opens no directory: nothing to flush
    sync_directory(tmp_path)


async def test_retention_is_read_only_for_an_object_stored_and_whole(tmp_path: Path) -> None:
    store = FileImmutableStore(tmp_path)
    folder = file_of(tmp_path).parent
    folder.mkdir(parents=True)
    (folder / ".0a1b.xml.retain").write_bytes(b"2036-10-19")  # a write that stopped before its object
    with pytest.raises(BlobNotFoundError):
        await store.retained_until(KEY)
    await store.put_once(KEY, CONTENT, retain_until=date(2040, 1, 1), media_type=MEDIA)
    assert await store.retained_until(KEY) == RETAIN  # the first record stays
    other = "do-ecf-v1/test/org/company/ecf/ffff.xml"
    await store.put_once(other, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    sidecar = folder / ".ffff.xml.retain"
    sidecar.chmod(0o644)
    sidecar.write_bytes(b"\xffnot a date")
    with pytest.raises(BlobRecordCorruptError) as raised:
        await store.retained_until(other)
    assert (raised.value.code, raised.value.kind) == ("blob_record_corrupt", ErrorKind.INTERNAL)
    sidecar.write_bytes(b"2036-13-45")
    with pytest.raises(BlobRecordCorruptError):
        await store.retained_until(other)


async def test_a_store_that_cannot_write_or_read_is_unavailable(tmp_path: Path) -> None:
    blocker = tmp_path / "root"
    blocker.write_bytes(b"a file where the root's directory should be")
    with pytest.raises(BlobStoreUnavailableError) as raised:
        await FileImmutableStore(blocker).put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    assert (raised.value.code, raised.value.kind) == ("blob_store_unavailable", ErrorKind.UNAVAILABLE)
    store = FileImmutableStore(tmp_path / "store")
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(BlobStoreUnavailableError):  # a directory where an object should be: damaged
        await store.get("do-ecf-v1/test", sha256=DIGEST)
    (tmp_path / "store" / "do-ecf-v1" / "k").write_bytes(b"x")  # an object under the name
    (tmp_path / "store" / "do-ecf-v1" / ".k.retain").mkdir()  # a directory where its record should be
    with pytest.raises(BlobStoreUnavailableError):
        await store.retained_until("do-ecf-v1/k")


@pytest.mark.parametrize(
    "key", ["a/b.", "a/b-", "a/b_", "nul", "con", "a/com1.xml", "a/lpt9", "prn.txt", "aux"]
)
def test_a_key_no_file_system_could_hold_as_named_is_refused(key: str) -> None:
    with pytest.raises(InvalidValueError, match=KEY_RULE):
        require_key(key)


def test_a_key_is_at_most_its_length_and_made_of_plain_segments() -> None:
    longest = "/".join(["a" * 128, "b" * 127, "c" * 127, "d" * 127])
    assert len(longest) == MAX_KEY
    for key in (longest, "a/b", "a", "a/console.xml", "a/null", "com10", "lpt.xml", "do-ecf-v1/x_1.xml"):
        require_key(key)
    with pytest.raises(InvalidValueError, match=KEY_RULE):
        require_key(longest + "e")


def test_a_stored_object_names_a_sha256_and_a_size() -> None:
    with pytest.raises(
        InvalidValueError, match=r"stored_object.sha256 must be a SHA-256 digest \(32 bytes\)"
    ):
        StoredObject(key=KEY, sha256=b"short", size=0)
    with pytest.raises(InvalidValueError, match=r"stored_object.size must be 0 or more"):
        StoredObject(key=KEY, sha256=DIGEST, size=-1)
