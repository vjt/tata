"""Engineering rules from CLAUDE.md, enforced on the AST of src/."""

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "tata"


def modules() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text())) for p in sorted(SRC.rglob("*.py"))]


def test_no_float_anywhere() -> None:
    """Money is Decimal, time is int minutes."""
    offenders = [
        f"{path.name}:{node.lineno}"
        for path, tree in modules()
        for node in ast.walk(tree)
        if (isinstance(node, ast.Constant) and isinstance(node.value, float))
        or (isinstance(node, ast.Name) and node.id == "float")
    ]
    assert offenders == []


def test_no_default_arguments() -> None:
    """Defaults are silent degradation paths: every parameter is explicit."""
    offenders = [
        f"{path.name}:{node.lineno} {node.name}"
        for path, tree in modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and (node.args.defaults or any(d is not None for d in node.args.kw_defaults))
    ]
    assert offenders == []


def test_no_dataclasses() -> None:
    """Pydantic models only."""
    offenders = [
        f"{path.name}:{node.lineno}"
        for path, tree in modules()
        for node in ast.walk(tree)
        if (isinstance(node, ast.ImportFrom) and node.module == "dataclasses")
        or (isinstance(node, ast.Import) and any(a.name == "dataclasses" for a in node.names))
    ]
    assert offenders == []


def test_imports_at_module_top_only() -> None:
    """No lazy imports."""
    offenders = [
        f"{path.name}:{inner.lineno}"
        for path, tree in modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for inner in ast.walk(node)
        if isinstance(inner, ast.Import | ast.ImportFrom)
    ]
    assert offenders == []


def test_calculators_do_no_io() -> None:
    """Calc modules are pure: they never import the store, the web layer or sqlite."""
    pure = {"festivita", "hours", "payslip", "inps", "annual", "models", "rates"}
    forbidden = {"tata.store", "tata.web", "tata.pdf", "sqlite3", "fastapi"}
    offenders = [
        f"{path.name} imports {name}"
        for path, tree in modules()
        if path.stem in pure
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [a.name for a in node.names]
        )
        if name in forbidden
    ]
    assert offenders == []


def test_environment_is_read_in_one_place() -> None:
    offenders = [
        f"{path.name}:{node.lineno}"
        for path, tree in modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in ("environ", "getenv")
        and path.stem != "web"
    ]
    assert offenders == []
