from pathlib import Path
lazy from collections.abc import Iterator

import pytest

from ledger_testing.images import ImagesFileNotFoundError, image, images


@pytest.fixture(autouse=True)
def fresh_images() -> Iterator[None]:
    """Each test reads the file again, from wherever it moved the current directory."""
    images.cache_clear()
    yield
    images.cache_clear()


def test_images_are_read_from_the_repository_file() -> None:
    assert image("postgres") == "postgres:18.6"


def test_the_file_is_found_from_a_subdirectory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(Path("packages/testing"))
    assert image("postgres") == "postgres:18.6"


def test_outside_the_repository_there_is_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(
        ImagesFileNotFoundError, match=r"^no deploy/local/images\.toml in .+ or any directory above it$"
    ):
        image("postgres")
