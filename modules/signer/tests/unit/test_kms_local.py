"""The local-file KMS: a master key in a file, for a laptop and the tests; refused where it would be real.

It wraps data keys under the context it is given and unwraps them under that context only; its key id names
its master key; it creates its key file (readable by its owner alone) only in ``local``.
"""

import os
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Final
lazy from collections.abc import Callable, Mapping
lazy from pathlib import Path

import pytest

from ledger_kernel.errors import ErrorKind
from ledger_signer.adapters.custody.envelope import DATA_KEY_BYTES
from ledger_signer.adapters.custody.kms_local import (
    KEY_ID_PREFIX,
    MASTER_KEY_BYTES,
    KmsKeyFileError,
    KmsLocalRefusedError,
    LocalFileKms,
)
from ledger_signer.application.ports import KmsRefusedError
lazy from ledger_platform.config.settings import Environment
lazy from ledger_signer.application.ports import DataKey

CONTEXT: Final = {"purpose": "signer.custody", "organization": "o", "company": "c", "certificate": "1"}
OTHER: Final = {**CONTEXT, "certificate": "2"}
OTHERS_MAY_NOT: Final = 0o077
STARTERS: Final = 8

type Tamper = Callable[[DataKey], tuple[bytes, str, Mapping[str, str]]]
"""What an unwrap is given instead of what the data key was wrapped with: bytes, key id, context."""


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    """A master key file, as a test or a deployment provides it."""
    path = tmp_path / "master.key"
    path.write_bytes(os.urandom(MASTER_KEY_BYTES))
    return path


async def test_a_data_key_wraps_and_unwraps_under_its_context(key_file: Path) -> None:
    kms = LocalFileKms(key_file, environment="test")
    data_key = await kms.new_data_key(CONTEXT)
    assert len(data_key.plaintext) == DATA_KEY_BYTES
    assert data_key.plaintext not in data_key.wrapped
    assert data_key.key_id == kms.key_id
    assert await kms.unwrap(data_key.wrapped, key_id=data_key.key_id, context=CONTEXT) == data_key.plaintext
    await kms.ready()


async def test_each_data_key_is_new(key_file: Path) -> None:
    kms = LocalFileKms(key_file, environment="test")
    first, second = await kms.new_data_key(CONTEXT), await kms.new_data_key(CONTEXT)
    assert first.plaintext != second.plaintext


async def test_the_key_id_names_the_master_key(key_file: Path, tmp_path: Path) -> None:
    kms = LocalFileKms(key_file, environment="test")
    assert kms.key_id.startswith(KEY_ID_PREFIX)
    assert len(kms.key_id) == len(KEY_ID_PREFIX) + 16
    assert LocalFileKms(key_file, environment="test").key_id == kms.key_id
    other = tmp_path / "other.key"
    other.write_bytes(os.urandom(MASTER_KEY_BYTES))
    assert LocalFileKms(other, environment="test").key_id != kms.key_id


def another_context(key: DataKey) -> tuple[bytes, str, Mapping[str, str]]:
    return key.wrapped, key.key_id, OTHER


def another_master_key(key: DataKey) -> tuple[bytes, str, Mapping[str, str]]:
    return key.wrapped, "local:0000000000000000", CONTEXT


def no_wrapped_key(key: DataKey) -> tuple[bytes, str, Mapping[str, str]]:
    return b"short", key.key_id, CONTEXT


@pytest.mark.parametrize("tamper", [another_context, another_master_key, no_wrapped_key])
async def test_an_unwrap_it_cannot_vouch_for_is_refused(key_file: Path, tamper: Tamper) -> None:
    kms = LocalFileKms(key_file, environment="test")
    wrapped, key_id, context = tamper(await kms.new_data_key(CONTEXT))
    with pytest.raises(KmsRefusedError) as caught:
        await kms.unwrap(wrapped, key_id=key_id, context=context)
    error = caught.value
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__  # the library's words stay behind


def test_the_key_file_is_created_only_in_local_and_kept_to_its_owner(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "master.key"
    path.parent.mkdir()
    kms = LocalFileKms(path, environment="local")
    assert path.stat().st_size == MASTER_KEY_BYTES
    if sys.platform == "win32":  # Windows keeps no POSIX modes: the file exists, in the user's own directory
        assert path.is_file()
    else:
        assert stat.S_IMODE(path.stat().st_mode) & OTHERS_MAY_NOT == 0
    assert LocalFileKms(path, environment="local").key_id == kms.key_id  # made once, then kept


def test_a_missing_key_file_is_refused_outside_local(tmp_path: Path) -> None:
    path = tmp_path / "master.key"
    with pytest.raises(KmsKeyFileError) as caught:
        LocalFileKms(path, environment="test")
    assert not path.exists()
    assert str(path) not in str(caught.value)
    assert caught.value.code == "kms_key_file_unusable"
    assert caught.value.kind is ErrorKind.INTERNAL


@pytest.mark.parametrize("size", [0, 16, 31, 33])
def test_a_key_file_that_is_no_256_bit_key_is_refused(tmp_path: Path, size: int) -> None:
    path = tmp_path / "master.key"
    content = os.urandom(size)
    path.write_bytes(content)
    with pytest.raises(KmsKeyFileError) as caught:
        LocalFileKms(path, environment="local")
    assert path.read_bytes() == content  # an existing file is never replaced
    assert caught.value.code == "kms_key_file_unusable"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_it_is_refused_where_it_would_hold_real_keys(key_file: Path, environment: Environment) -> None:
    with pytest.raises(KmsLocalRefusedError) as caught:
        LocalFileKms(key_file, environment=environment)
    assert caught.value.code == "kms_local_refused"
    assert caught.value.kind is ErrorKind.INTERNAL


def test_it_shows_no_key(key_file: Path) -> None:
    kms = LocalFileKms(key_file, environment="test")
    assert key_file.read_bytes().hex() not in repr(kms)
    assert str(key_file) not in repr(kms)


@pytest.mark.parametrize("environment", ["local", "test"])
@pytest.mark.parametrize("where", ["missing directory", "a directory"])
def test_a_key_file_that_cannot_be_made_or_read_is_refused_without_its_path(
    tmp_path: Path, environment: Environment, where: str
) -> None:
    path = tmp_path / "absent" / "master.key" if where == "missing directory" else tmp_path
    with pytest.raises(KmsKeyFileError) as caught:
        LocalFileKms(path, environment=environment)
    error = caught.value
    assert str(tmp_path) not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__


def started(path: Path) -> str:
    """The key id a starter in ``local`` reads, making the key file if it is first."""
    return LocalFileKms(path, environment="local").key_id


def test_starters_racing_to_make_the_key_file_all_use_the_one_kept(tmp_path: Path) -> None:
    path = tmp_path / "master.key"
    with ThreadPoolExecutor(STARTERS) as pool:
        key_ids = set(pool.map(started, [path] * STARTERS))
    assert key_ids == {LocalFileKms(path, environment="test").key_id}
    assert [entry.name for entry in tmp_path.iterdir()] == ["master.key"]  # no temporary file left behind
