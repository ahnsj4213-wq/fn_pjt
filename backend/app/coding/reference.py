"""A template's reference solution as the program a candidate would submit. For verification only (tools/verify_coding_bank.py,
tests, the browser E2E): nothing in the application serves it, and no candidate-facing path imports this module."""
import ast
import inspect

from app.coding import refsol, templates
from app.coding.templates import TEMPLATES

_MAIN = "\n\nimport sys as _sys\n_sys.stdout.write({name}(_sys.stdin.read()))\n"


def _stdlib_imports(tree: ast.Module, source: str) -> list[str]:
    return [ast.get_source_segment(source, n) for n in tree.body
            if isinstance(n, ast.Import | ast.ImportFrom) and not getattr(n, "module", "").startswith("app")]


def reference_program(key: str) -> str:
    """The stdlib imports, the two output helpers and the one solution. First problems: everything above their table in templates.py."""
    fn = TEMPLATES[key].solve
    mod = inspect.getmodule(fn)
    source = inspect.getsource(mod)
    tree = ast.parse(source)
    if mod is templates:
        parts = _stdlib_imports(tree, source)
        for n in tree.body:
            if isinstance(n, ast.FunctionDef | ast.Assign) and not _is_table(n):
                parts.append(ast.get_source_segment(source, n))
    else:
        parts = _stdlib_imports(tree, source)
        parts += [ast.get_source_segment(source, n) for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name in ("_ln", "_arr", fn.__name__)]
    return "\n\n\n".join(parts) + _MAIN.format(name=fn.__name__)


def _is_table(n: ast.AST) -> bool:
    if isinstance(n, ast.FunctionDef):
        return n.name == "_placed"
    names = {t.id for t in n.targets if isinstance(t, ast.Name)} if isinstance(n, ast.Assign) else set()
    return bool(names & {"TEMPLATES", "BY_TITLE", "_ARR", "_GRID", "_FIRST"}) or isinstance(n, ast.AnnAssign)


__all__ = ["reference_program", "refsol"]
