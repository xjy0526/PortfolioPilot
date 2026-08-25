"""Static dependency guards for the incremental legacy migration.

These tests intentionally protect only boundaries that are already true.  They
do not pretend that every root-level package is legacy: several governed
services still live in ``rag``, ``routes``, ``services``, and ``workflows``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

SQLITE_RAG_MODULES = {
    "rag.repository",
    "rag.retriever",
    "rag.service",
}

LEGACY_ROOT_MODULES = {
    "database",
    "engine",
    "fetchers",
    "state",
    *SQLITE_RAG_MODULES,
    "services.data_loader",
    "services.portfolio_builder",
    "services.portfolio_history_fallback",
    "services.refresh",
    "services.shadow_agent",
    "services.telegram",
    "services.telegram_bot",
    "services.trade_advisor",
}


def _python_files(path: Path) -> list[Path]:
    return sorted(file for file in path.rglob("*.py") if "__pycache__" not in file.parts)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
            modules.update(
                f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            modules.add(node.args[0].value)

    return modules


def _matches(module: str, forbidden: Iterable[str]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden)


def _violations(files: Iterable[Path], forbidden: set[str]) -> list[str]:
    violations: list[str] = []
    for path in files:
        relative_path = path.relative_to(ROOT)
        for module in sorted(_imported_modules(path)):
            if _matches(module, forbidden):
                violations.append(f"{relative_path}: imports {module}")
    return violations


def test_app_core_does_not_depend_on_legacy_root_modules() -> None:
    files = _python_files(ROOT / "app")
    adapter = ROOT / "app" / "services" / "legacy_portfolio_adapter.py"
    violations = _violations((path for path in files if path != adapter), LEGACY_ROOT_MODULES)

    # The adapter alone may translate the new PostgreSQL model into the old DTO.
    for path in files:
        if path != adapter and "models" in _imported_modules(path):
            violations.append(f"{path.relative_to(ROOT)}: imports models")

    assert not violations, "New app modules must not depend on legacy roots:\n" + "\n".join(
        violations
    )


def test_new_api_does_not_import_legacy_sqlite() -> None:
    files = _python_files(ROOT / "app" / "api")
    violations = _violations(files, {"database", "sqlite3"})

    assert not violations, "app/api must use repositories and AsyncSession:\n" + "\n".join(
        violations
    )


def test_postgres_rag_mainline_does_not_import_sqlite_compatibility() -> None:
    files = [
        ROOT / "app" / "services" / "research_knowledge.py",
        ROOT / "app" / "workers" / "run_knowledge_ingestion.py",
        ROOT / "routes" / "knowledge.py",
        ROOT / "routes" / "research.py",
    ]
    violations = _violations(files, SQLITE_RAG_MODULES | {"database", "sqlite3"})

    assert not violations, "PostgreSQL RAG must not invoke SQLite RAG:\n" + "\n".join(violations)


def test_workflow_does_not_import_legacy_local_knowledge_base() -> None:
    files = [
        *_python_files(ROOT / "workflows"),
        ROOT / "app" / "workers" / "run_research_workflow.py",
        ROOT / "routes" / "workflows.py",
    ]
    forbidden = SQLITE_RAG_MODULES | {"database", "services.knowledge_data"}
    violations = _violations(files, forbidden)

    assert not violations, "Governed workflows must use PostgreSQL knowledge services:\n" + (
        "\n".join(violations)
    )


def test_legacy_portfolio_adapter_is_a_one_way_boundary() -> None:
    adapter = ROOT / "app" / "services" / "legacy_portfolio_adapter.py"
    violations = _violations([adapter], LEGACY_ROOT_MODULES)

    assert "models" in _imported_modules(adapter)
    assert not violations, "The compatibility adapter may use DTOs, not legacy stores:\n" + (
        "\n".join(violations)
    )


def test_postgres_mainline_does_not_use_global_portfolio_state() -> None:
    files = [
        *_python_files(ROOT / "app" / "api"),
        *_python_files(ROOT / "app" / "services"),
    ]
    violations = _violations(files, {"state"})

    assert not violations, "PostgreSQL APIs and services must not use portfolio_data:\n" + (
        "\n".join(violations)
    )
