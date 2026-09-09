from __future__ import annotations

import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]

DOMAIN_PACKAGES = {
    "profile",
    "ranking",
    "generation",
}

PROJECT_PACKAGES = DOMAIN_PACKAGES | {
    "api",
    "core",
    "data",
    "gateway",
    "graph_service",
    "llm",
    "main",
}

ALLOWED_IMPORTS: dict[str, set[str]] = {
    "api": {"api", "core", "data", "gateway", "graph_service", "llm"},
    "data": {"core", "data", "graph_service"},
    "profile": {"core", "data", "llm", "profile"},
    "llm": {"core", "data", "llm"},
    "ranking": {"core", "data", "llm", "ranking"},
    "generation": {"core", "data", "generation", "llm"},
    "gateway": {"core", "data", "gateway"},
    "graph_service": {"core", "data", "graph_service"},
}

LEGACY_IMPORT_EXCEPTIONS: dict[str, set[str]] = {}


def _project_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in PROJECT_PACKAGES:
                    imports.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                root = node.module.split(".", 1)[0]
                if root in PROJECT_PACKAGES:
                    imports.add(root)
    return imports


def test_modular_package_import_boundaries_are_explicit():
    violations: list[str] = []
    for package, allowed in ALLOWED_IMPORTS.items():
        for path in (BACKEND_ROOT / package).rglob("*.py"):
            rel = path.relative_to(BACKEND_ROOT).as_posix()
            allowed_for_file = allowed | LEGACY_IMPORT_EXCEPTIONS.get(rel, set())
            imports = _project_imports(path)
            forbidden = imports - allowed_for_file
            if forbidden:
                violations.append(f"{rel}: {', '.join(sorted(forbidden))}")

    assert not violations, "Unexpected cross-boundary imports:\n" + "\n".join(violations)


def test_core_remains_dependency_free_inside_the_project():
    violations = []
    for path in (BACKEND_ROOT / "core").rglob("*.py"):
        imports = _project_imports(path)
        if imports:
            rel = path.relative_to(BACKEND_ROOT).as_posix()
            violations.append(f"{rel}: {', '.join(sorted(imports))}")

    assert not violations, "core/ must not import project packages:\n" + "\n".join(violations)

