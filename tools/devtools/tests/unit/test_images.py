from typing import Final
lazy from pathlib import Path

from ledger_devtools.checks import Problem
from ledger_devtools.images import image_problems, pin_problems, pinned_images

UV: Final = f"ghcr.io/astral-sh/uv:0.13.0-trixie-slim@sha256:{'1' * 64}"
DISTROLESS: Final = f"gcr.io/distroless/cc-debian13:nonroot@sha256:{'e' * 64}"
POSTGRES: Final = f"postgres:18.6@sha256:{'7' * 64}"
PINS: Final = f"""
# The pins.
[images]
postgres = "{POSTGRES}"
uv = "{UV}"
distroless = "{DISTROLESS}"
"""
COMPOSE: Final = f"""
x-app: &app
  image: ledger:local
services:
  postgres:
    image: {POSTGRES}
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
    assert pinned_images(deploy(tmp_path)) == frozenset({POSTGRES, UV, DISTROLESS})


def test_a_tree_without_images_toml_pins_nothing(tmp_path: Path) -> None:
    assert pinned_images(tmp_path) == frozenset()


def test_images_named_by_compose_and_dockerfiles_are_pinned(tmp_path: Path) -> None:
    root = deploy(tmp_path)
    assert image_problems(root, pinned_images(root)) == ()


def test_an_image_that_is_not_a_pin_is_named_where_it_is(tmp_path: Path) -> None:
    root = deploy(
        tmp_path,
        compose=COMPOSE.replace(POSTGRES, "postgres:18.7"),
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


def test_every_pin_carries_a_digest(tmp_path: Path) -> None:
    assert pin_problems(deploy(tmp_path)) == ()


def test_a_pin_without_a_full_digest_is_named(tmp_path: Path) -> None:
    root = deploy(tmp_path)
    pins = root / "deploy/local/images.toml"
    pins.write_text(
        pins.read_text(encoding="utf-8")
        .replace(POSTGRES, "postgres:18.6")
        .replace(UV, UV[:-1])
        .replace(DISTROLESS, DISTROLESS.replace("sha256", "sha512")),
        encoding="utf-8",
    )
    assert pin_problems(root) == tuple(
        Problem(
            "deploy/local/images.toml",
            f"image '{name}' ({image}) is not pinned by digest: append @sha256:<the tag's index digest>",
        )
        for name, image in (
            ("distroless", DISTROLESS.replace("sha256", "sha512")),
            ("postgres", "postgres:18.6"),
            ("uv", UV[:-1]),
        )
    )


def test_a_tree_without_images_toml_has_no_pin_problem(tmp_path: Path) -> None:
    assert pin_problems(tmp_path) == ()
