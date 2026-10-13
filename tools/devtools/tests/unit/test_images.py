from typing import Final
lazy from pathlib import Path

from ledger_devtools.checks import Problem
from ledger_devtools.images import image_problems, pinned_images

UV: Final = "ghcr.io/astral-sh/uv:0.13.0-trixie-slim@sha256:11bee95ce279"
DISTROLESS: Final = "gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a"
PINS: Final = f"""
# The pins.
[images]
postgres = "postgres:18.6"
uv = "{UV}"
distroless = "{DISTROLESS}"
"""
COMPOSE: Final = """
x-app: &app
  image: ledger:local
services:
  postgres:
    image: postgres:18.6
  api:
    <<: *app
    image: "ledger:dev"
"""
DOCKERFILE: Final = f"""
# syntax=docker/dockerfile:1
FROM {UV} AS build
RUN echo FROM nowhere
FROM build AS check
FROM {DISTROLESS}
COPY --from=build /app /app
"""


def deploy(root: Path, *, compose: str = COMPOSE, dockerfile: str = DOCKERFILE) -> Path:
    (root / "deploy/local").mkdir(parents=True)
    (root / "deploy/docker").mkdir(parents=True)
    (root / "deploy/local/images.toml").write_text(PINS, encoding="utf-8")
    (root / "deploy/local/compose.yaml").write_text(compose, encoding="utf-8")
    (root / "deploy/docker/ledger.Dockerfile").write_text(dockerfile, encoding="utf-8")
    return root


def test_images_toml_is_read_as_the_set_of_pins(tmp_path: Path) -> None:
    assert pinned_images(deploy(tmp_path)) == frozenset({"postgres:18.6", UV, DISTROLESS})


def test_a_tree_without_images_toml_pins_nothing(tmp_path: Path) -> None:
    assert pinned_images(tmp_path) == frozenset()


def test_images_named_by_compose_and_dockerfiles_are_pinned(tmp_path: Path) -> None:
    root = deploy(tmp_path)
    assert image_problems(root, pinned_images(root)) == ()


def test_an_image_that_is_not_a_pin_is_named_where_it_is(tmp_path: Path) -> None:
    root = deploy(
        tmp_path,
        compose=COMPOSE.replace("postgres:18.6", "postgres:18.7"),
        dockerfile=DOCKERFILE.replace(f"FROM {DISTROLESS}", "FROM gcr.io/distroless/cc-debian13:latest"),
    )
    assert image_problems(root, pinned_images(root)) == (
        Problem(
            "deploy/docker/ledger.Dockerfile",
            ("image 'gcr.io/distroless/cc-debian13:latest' is not pinned in deploy/local/images.toml"),
        ),
        Problem(
            "deploy/local/compose.yaml", "image 'postgres:18.7' is not pinned in deploy/local/images.toml"
        ),
    )


def test_a_tree_without_deploy_files_has_no_image_problems(tmp_path: Path) -> None:
    assert image_problems(tmp_path, frozenset()) == ()


def test_every_dockerfile_of_the_repository_is_checked_wherever_it_is(tmp_path: Path) -> None:
    root = deploy(tmp_path)
    nested = root / "countries/do/testing/src/ledger_country_do_testing/xmlsec1"
    nested.mkdir(parents=True)
    (nested / "Dockerfile").write_text("FROM debian:trixie-slim\n", encoding="utf-8")
    hidden = root / ".venv/lib/somewhere"
    hidden.mkdir(parents=True)
    (hidden / "Dockerfile").write_text("FROM anything:latest\n", encoding="utf-8")
    assert image_problems(root, pinned_images(root)) == (
        Problem(
            "countries/do/testing/src/ledger_country_do_testing/xmlsec1/Dockerfile",
            "image 'debian:trixie-slim' is not pinned in deploy/local/images.toml",
        ),
    )
