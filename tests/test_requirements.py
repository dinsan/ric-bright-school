"""requirements*.txt must stay in step with pyproject.toml (regenerate: make requirements)."""

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def pins(filename: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (ROOT / filename).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            requirement = Requirement(line)
            (spec,) = requirement.specifier  # exactly one "==" pin per line
            assert spec.operator == "==", line
            result[canonicalize_name(requirement.name)] = spec.version
    return result


@pytest.mark.parametrize(
    ("filename", "declared"),
    [
        ("requirements.txt", PYPROJECT["dependencies"]),
        (
            "requirements-dev.txt",
            PYPROJECT["dependencies"] + PYPROJECT["optional-dependencies"]["dev"],
        ),
    ],
)
def test_every_declared_dependency_is_pinned_within_its_range(
    filename: str, declared: list[str]
) -> None:
    pinned = pins(filename)
    for entry in declared:
        requirement = Requirement(entry)
        name = canonicalize_name(requirement.name)
        assert name in pinned, f"{name} is missing from {filename}: run `make requirements`"
        assert requirement.specifier.contains(pinned[name], prereleases=True), (
            f"{name}=={pinned[name]} in {filename} does not satisfy {requirement.specifier}: "
            "run `make requirements`"
        )


def test_runtime_pins_do_not_include_dev_tools() -> None:
    runtime = pins("requirements.txt")
    for tool in ("pytest", "ruff", "mypy", "uv"):
        assert tool not in runtime
