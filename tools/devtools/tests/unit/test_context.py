from typing import Final
lazy from pathlib import Path

from ledger_devtools.checks import Problem
from ledger_devtools.context import DOCKERIGNORE, SECRET_EXCLUSIONS, context_problems

SAFE: Final = "\n".join(
    ("# Secrets never enter the context.", ".git", *SECRET_EXCLUSIONS, "!tools/*/pyproject.toml")
)


def dockerfile(root: Path) -> Path:
    (root / "deploy/docker").mkdir(parents=True)
    (root / "deploy/docker/ledger.Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    return root


def test_the_secret_patterns_cover_env_files_dgii_and_key_stores() -> None:
    assert {
        ".env",
        "**/.env",
        "**/.env.*",
        ".dgii",
        "**/.dgii",
        "*.p12",
        "**/*.p12",
        "*.pfx",
        "**/*.pfx",
    } == set(SECRET_EXCLUSIONS)


def test_a_dockerignore_that_excludes_every_secret_pattern_has_no_problem(tmp_path: Path) -> None:
    (dockerfile(tmp_path) / DOCKERIGNORE).write_text(SAFE + "\n", encoding="utf-8")
    assert context_problems(tmp_path) == ()


def test_a_tree_without_dockerfiles_needs_no_dockerignore(tmp_path: Path) -> None:
    assert context_problems(tmp_path) == ()


def test_a_missing_dockerignore_names_every_pattern(tmp_path: Path) -> None:
    assert context_problems(dockerfile(tmp_path)) == tuple(
        Problem(DOCKERIGNORE, f"does not exclude '{pattern}': a secret could enter an image's build context")
        for pattern in SECRET_EXCLUSIONS
    )


def test_a_missing_pattern_and_a_re_inclusion_are_named(tmp_path: Path) -> None:
    text = SAFE.replace("\n**/*.pfx", "") + "\n  !deploy/local/ledger.env  \n!keys/signer.p12\n"
    (dockerfile(tmp_path) / DOCKERIGNORE).write_text(text, encoding="utf-8")
    assert context_problems(tmp_path) == (
        Problem(DOCKERIGNORE, "does not exclude '**/*.pfx': a secret could enter an image's build context"),
        Problem(DOCKERIGNORE, "re-includes '!keys/signer.p12', which may be a secret"),
    )
