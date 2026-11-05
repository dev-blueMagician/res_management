"""What an installed copy needs: every data file in the package, and the promised README."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "restaurant"
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _packaged_data_files() -> set[Path]:
    patterns = PYPROJECT["tool"]["setuptools"]["package-data"]["restaurant"]
    return {path for pattern in patterns for path in PACKAGE.glob(pattern)}


def test_every_non_python_file_in_the_package_gets_installed():
    data_files = {
        path
        for path in PACKAGE.rglob("*")
        if path.is_file() and path.suffix not in (".py", ".pyc") and "__pycache__" not in path.parts
    }
    assert data_files, "the package should have data files"
    assert data_files - _packaged_data_files() == set()


def test_the_qml_module_file_is_among_them():
    assert PACKAGE / "qml" / "components" / "qmldir" in _packaged_data_files()


def test_the_readme_named_by_pyproject_exists():
    assert (ROOT / PYPROJECT["project"]["readme"]).is_file()


def test_the_requirements_and_architecture_document_exists_and_covers_every_requirement():
    document = (ROOT / "docs" / "REQUIREMENTS_AND_ARCHITECTURE.md").read_text(encoding="utf-8")
    requirements = (
        [f"FR-S{n}" for n in range(1, 8)]
        + [f"FR-M{n}" for n in range(1, 5)]
        + [f"FR-I{n}" for n in range(1, 6)]
        + [f"FR-P{n}" for n in range(1, 7)]
        + [f"FR-R{n}" for n in range(1, 4)]
        + [f"NFR-{n}" for n in range(1, 9)]
    )
    assert [name for name in requirements if name not in document] == []
