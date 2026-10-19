"""A module's contract, planted in a copy of the repository: its gRPC code is ``sync``'s, and buf guards it.

The code lands in the module's generated package only, ``sync`` twice writes nothing the second time, and
``check`` finds it stale once the contract changes. The gate's ``proto-breaking`` task, run by poe as the
gate runs it, refuses a contract that breaks the one on the base ref, in the pinned buf image (Docker).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from ledger_devtools.cli import main
from ledger_devtools.generate import targets
from ledger_devtools.tree import visible_files
from ledger_devtools.workspace import Workspace

ROOT: Final = Path(__file__).resolve().parents[4]
VAULT: Final = """[package]
name = "vault"
kind = "module"
summary = "A module planted by a test: a contract of its own."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]
python = ["grpcio>=1.84.0", "protobuf>=7.36.2"]
"""
CONTRACT: Final = """syntax = "proto3";

package ledger.vault.v1;

import "google/protobuf/timestamp.proto";

// Opens what the vault holds.
service VaultService {
  rpc Open(OpenRequest) returns (OpenResponse);
}

message OpenRequest {
  string name = 1;
  google.protobuf.Timestamp at = 2;
}

message OpenResponse {
  string id = 1;
}
"""
PROTO: Final = "contracts/proto/ledger/vault/v1/vault.proto"
CODE: Final = Path("modules/vault/src/ledger_vault/adapters/grpc/_generated")


def write(root: Path, relative: str, text: str) -> None:
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    (root / relative).write_text(text, encoding="utf-8", newline="\n")


def texts(root: Path) -> dict[Path, str]:
    return {path: (root / path).read_text(encoding="utf-8") for path in visible_files(root)}


def test_a_planted_contract_generates_into_its_module_only(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repository, "modules/vault/manifest.toml", VAULT)
    for package in ("", "/adapters"):
        write(repository, f"modules/vault/src/ledger_vault{package}/__init__.py", '"""Planted."""\n')
    write(repository, PROTO, CONTRACT)
    before = texts(repository)
    assert main(["sync", "--root", str(repository)]) == 0
    capsys.readouterr()
    after = texts(repository)
    assert main(["sync", "--root", str(repository)]) == 0
    assert capsys.readouterr().out == ""
    assert main(["check", "--root", str(repository)]) == 0, capsys.readouterr().out
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    assert changed <= set(targets(Workspace.load(repository)))
    names = ("__init__.py", "vault_pb2.py", "vault_pb2.pyi", "vault_pb2_grpc.py", "vault_pb2_grpc.pyi")
    assert {path for path in changed if path.is_relative_to(CODE)} == {
        CODE / "__init__.py",
        *(CODE / "v1" / name for name in names),
    }
    omitted = 'omit = ["*/modules/*/src/*/adapters/grpc/_generated/*"]\n'
    assert omitted in after[Path("modules/vault/pyproject.toml")]
    write(repository, PROTO, CONTRACT.replace("  string id = 1;\n", "  string id = 1;\n  string seal = 2;\n"))
    assert main(["check", "--root", str(repository)]) == 1
    stale = "stale, run `uv run ledger-devtools sync`"
    assert capsys.readouterr().out.splitlines() == [
        f"{(CODE / 'v1' / name).as_posix()}: {stale}" for name in ("vault_pb2.py", "vault_pb2.pyi")
    ]


# ── buf breaking, as the gate runs it ──────────────────────────────────────────

IDENTITY: Final = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


GIT: Final = shutil.which("git") or "git"


def git(repository: Path, *arguments: str, stdin: str = "") -> str:
    return subprocess.run(
        [GIT, "-C", str(repository), *arguments],
        env={**os.environ, **IDENTITY},
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def proto_breaking(repository: Path, *arguments: str) -> tuple[int, str]:
    """The root's ``proto-breaking`` task, run by poe in ``repository``: its exit status and what it said.

    In this interpreter (poe's ``simple`` executor): ``uv`` would build the copy's workspace, which it lacks.
    """
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "poethepoet",
            "-C",
            str(repository),
            "-e",
            "simple",
            "proto-breaking",
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return process.returncode, process.stdout + process.stderr


@pytest.fixture
def based(tmp_path: Path) -> Path:
    """A git repository of the root's tasks and pins, its ``main`` holding the contract (plumbing commit)."""
    for relative in ("pyproject.toml", "deploy/local/images.toml", "contracts/buf.yaml"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    write(tmp_path, PROTO, CONTRACT)
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "add", "--all")
    git(
        tmp_path,
        "update-ref",
        "refs/heads/main",
        git(tmp_path, "commit-tree", "-m", "base", git(tmp_path, "write-tree")),
    )
    return tmp_path


def test_proto_breaking_passes_a_compatible_change_and_refuses_a_breaking_one(based: Path) -> None:
    write(based, PROTO, CONTRACT.replace("  string id = 1;\n", "  string id = 1;\n  string seal = 2;\n"))
    status, said = proto_breaking(based)
    assert status == 0, said
    write(based, PROTO, CONTRACT.replace("  google.protobuf.Timestamp at = 2;\n", ""))
    status, said = proto_breaking(based)
    assert status != 0
    assert 'Previously present field "2" with name "at" on message "OpenRequest" was deleted.' in said, said


def test_an_explicit_base_must_name_a_commit(based: Path) -> None:
    status, said = proto_breaking(based, "--base", "release/v9")
    assert status != 0
    assert "--base release/v9 names no commit here" in said, said


def test_a_base_without_a_contract_has_nothing_to_break(based: Path) -> None:
    empty = git(based, "commit-tree", "-m", "empty", git(based, "mktree"))
    git(based, "update-ref", "refs/heads/before", empty)
    status, said = proto_breaking(based, "--base", "before")
    assert status == 0, said
    assert "before holds no contract (contracts/buf.yaml and a .proto): nothing can break" in said


def test_the_default_base_may_be_absent(based: Path) -> None:
    """A pull request's checkout has no local main: `poe check` passes, CI's own step compares."""
    git(based, "update-ref", "-d", "refs/heads/main")
    status, said = proto_breaking(based)
    assert status == 0, said
    assert "main is not a ref here: nothing to compare (--base names the ref to compare with)" in said
