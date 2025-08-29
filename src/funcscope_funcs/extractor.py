from __future__ import annotations

import argparse
import ast
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ----------------------------- Data model -----------------------------

@dataclass
class FunctionRecord:
    file_path: str
    rel_path: str
    module: str

    keyword: str          # "def" or "async def"
    name: str
    qualname: str         # e.g., Class.method or function
    class_name: Optional[str]

    lineno: int
    end_lineno: int

    signature: str
    parameters: List[Dict[str, Any]]  # structured params
    returns_annotation: Optional[str]

    decorators: List[str]
    docstring: Optional[str]
    body: str             # code inside the function block (no signature/decorators)
    source: str           # full function text including signature


# ----------------------------- Helpers -----------------------------

def _module_name(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)

def _unparse(node: Optional[ast.AST]) -> Optional[str]:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None

def _param_dict(arg: ast.arg, default: Optional[ast.AST], kind: str, ann: Optional[ast.AST]) -> Dict[str, Any]:
    return {
        "name": arg.arg if hasattr(arg, "arg") else None,
        "annotation": _unparse(ann) if ann is not None else _unparse(getattr(arg, "annotation", None)),
        "default": _unparse(default) if default is not None else None,
        "kind": kind,  # "posonly" | "pos" | "vararg" | "kwonly" | "varkw"
    }

def _build_parameters(args: ast.arguments) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Return (signature_str, parameters_list)
    """
    parts: List[str] = []
    plist: List[Dict[str, Any]] = []

    # Pos-only
    if getattr(args, "posonlyargs", []):
        for a in args.posonlyargs:
            seg = a.arg
            if a.annotation is not None:
                seg += f": {_unparse(a.annotation)}"
            parts.append(seg)
            plist.append(_param_dict(a, None, "posonly", a.annotation))
        parts.append("/")

    # Positional or keyword
    ndefs = len(args.defaults)
    for i, a in enumerate(args.args):
        seg = a.arg
        if a.annotation is not None:
            seg += f": {_unparse(a.annotation)}"
        default = None
        if ndefs and i >= len(args.args) - ndefs:
            default = args.defaults[i - (len(args.args) - ndefs)]
            seg += f"={_unparse(default)}"
        parts.append(seg)
        plist.append(_param_dict(a, default, "pos", a.annotation))

    # Vararg
    if args.vararg:
        a = args.vararg
        seg = f"*{a.arg}"
        if a.annotation is not None:
            seg += f": {_unparse(a.annotation)}"
        parts.append(seg)
        plist.append(_param_dict(a, None, "vararg", a.annotation))

    # Keyword-only
    if args.kwonlyargs:
        if not args.vararg:
            parts.append("*")
        for i, a in enumerate(args.kwonlyargs):
            seg = a.arg
            if a.annotation is not None:
                seg += f": {_unparse(a.annotation)}"
            default = args.kw_defaults[i]
            if default is not None:
                seg += f"={_unparse(default)}"
            parts.append(seg)
            plist.append(_param_dict(a, default, "kwonly", a.annotation))

    # Kw-varargs
    if args.kwarg:
        a = args.kwarg
        seg = f"**{a.arg}"
        if a.annotation is not None:
            seg += f": {_unparse(a.annotation)}"
        parts.append(seg)
        plist.append(_param_dict(a, None, "varkw", a.annotation))

    return "(" + ", ".join(parts) + ")", plist

def _function_body_source(node: ast.AST, file_lines: List[str]) -> str:
    """
    Extract the function *body* (without decorators/signature).
    We use the first statement's lineno to the function end_lineno.
    If there's no body, return empty string.
    """
    body_nodes = getattr(node, "body", [])
    if not body_nodes:
        return ""
    start = getattr(body_nodes[0], "lineno", getattr(node, "lineno", 1))
    end = getattr(node, "end_lineno", start)
    start_idx = max(0, start - 1)
    end_idx = min(len(file_lines), end)
    return "\n".join(file_lines[start_idx:end_idx])

def _function_full_source(node: ast.AST, file_lines: List[str]) -> str:
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    return "\n".join(file_lines[start - 1:end])

def _collect_returns(node: ast.AST) -> List[str]:
    rets: List[str] = []
    class V(ast.NodeVisitor):
        def visit_Return(self, n: ast.Return) -> None:
            if n.value is not None:
                rets.append(_unparse(n.value) or "")
            else:
                rets.append("")  # bare return
    V().visit(node)
    return rets


# ----------------------------- Core extraction -----------------------------

class _FnCollector(ast.NodeVisitor):
    def __init__(self, file_path: Path, root: Path, file_text: str) -> None:
        self.file_path = file_path
        self.root = root
        self.lines = file_text.splitlines()
        self.class_stack: List[str] = []
        self.records: List[FunctionRecord] = []

    def _add(self, node: ast.AST, name: str, is_async: bool, args: ast.arguments, returns: Optional[ast.AST], decorator_list: List[ast.AST]) -> None:
        rel = self.file_path.relative_to(self.root)
        module = _module_name(rel)
        class_name = self.class_stack[-1] if self.class_stack else None
        qualname = f"{class_name}.{name}" if class_name else name

        sig, params = _build_parameters(args)
        doc = ast.get_docstring(node)
        deco = []
        for d in decorator_list:
            try:
                deco.append(ast.unparse(d))
            except Exception:
                deco.append("<decorator>")

        rec = FunctionRecord(
            file_path=str(self.file_path),
            rel_path=str(rel),
            module=module,

            keyword="async def" if is_async else "def",
            name=name,
            qualname=qualname,
            class_name=class_name,

            lineno=getattr(node, "lineno", 1),
            end_lineno=getattr(node, "end_lineno", getattr(node, "lineno", 1)),

            signature=sig + (f" -> {_unparse(returns)}" if returns is not None else ""),
            parameters=params,
            returns_annotation=_unparse(returns),

            decorators=deco,
            docstring=doc,
            body=_function_body_source(node, self.lines),
            source=_function_full_source(node, self.lines),
        )
        self.records.append(rec)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add(node, node.name, False, node.args, node.returns, node.decorator_list)
        # Do NOT descend into nested defs by default (keeps output focused)
        # If you want nested functions too, uncomment:
        # self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add(node, node.name, True, node.args, node.returns, node.decorator_list)
        # self.generic_visit(node)


def extract_functions_from_file(py_path: Path, root: Path) -> List[FunctionRecord]:
    try:
        text = py_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    col = _FnCollector(py_path, root, text)
    col.visit(tree)

    # Attach return statements (walk after to avoid recompute)
    for rec in col.records:
        # reconstruct node to collect returns? We already lost node.
        # Instead, quickly re-parse and locate by lines (cheap for a single file).
        # We can scan the source block of each function:
        try:
            f_tree = ast.parse(rec.source)
            returns = _collect_returns(f_tree)
        except Exception:
            returns = []
        # drop onto a dynamic field via asdict later; add to record via monkey patch:
        # We'll put into a dict during serialization
        rec.__dict__["return_statements"] = returns
    return col.records


# ----------------------------- File discovery -----------------------------

def _py_files_from_tree_json(tree_json: Path) -> List[Path]:
    """
    Traverse a serialized treewalker JSON and collect .py file paths.
    """
    obj = json.loads(tree_json.read_text(encoding="utf-8"))
    files: List[str] = []

    def walk(node: Dict[str, Any]) -> None:
        if not isinstance(node, dict):
            return
        if node.get("is_dir"):
            for ch in node.get("children", []):
                walk(ch)
        else:
            p = node.get("path", "")
            if p.lower().endswith(".py"):
                files.append(p)

    walk(obj)
    return [Path(p) for p in files]


def _discover_py_files(root: Path, exclude_relpaths: Iterable[str]) -> List[Path]:
    """
    Fallback discovery if no tree JSON is supplied: walk the filesystem.
    Skips common junk by default plus user-provided `--exclude`.
    """
    default_excludes = {".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache", "build", "dist", ".tox", "node_modules"}
    exclude_abs = {(root / Path(rel)).resolve() for rel in exclude_relpaths}
    out: List[Path] = []
    for p in root.rglob("*.py"):
        if any(part in default_excludes for part in p.parts):
            continue
        if any(str(p).startswith(str(ex)) for ex in exclude_abs):
            continue
        out.append(p.resolve())
    return out


# ----------------------------- Writing -----------------------------

def _write_functions_for_file(file_path: Path, records: List[FunctionRecord], root: Path, out_dir: Path) -> Optional[Path]:
    """
    Create: OUT/<rel_dir>/<filename>/functions.json
    """
    rel = file_path.relative_to(root)
    leaf_dir = out_dir / rel.parent / rel.name
    leaf_dir.mkdir(parents=True, exist_ok=True)
    out_json = leaf_dir / "functions.json"

    # Convert records to JSON-safe dicts and include return_statements we added dynamically
    payload: List[Dict[str, Any]] = []
    for r in records:
        d = asdict(r)
        d["return_statements"] = r.__dict__.get("return_statements", [])
        payload.append(d)

    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_json


# ----------------------------- CLI -----------------------------

def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(
        prog="python -m src.funcscope_funcs.extractor",
        description="Extract Python functions (def/async def) and write per-file JSON summaries.",
    )
    ap.add_argument("--from-tree", type=str, default=None,
                    help="Path to JSON produced by funcscope.treewalker (faster & respects its filters).")
    ap.add_argument("--root", type=str, default=".",
                    help="Project root (used to compute relative paths and as a fallback discovery root).")
    ap.add_argument("--exclude", action="append", default=[],
                    help="Relative PATH under root to exclude when discovering without --from-tree (repeatable).")
    ap.add_argument("--out", type=str, default="funcscope-functions",
                    help="Output base directory (default: ./funcscope-functions).")
    ap.add_argument("--workers", type=int, default=None,
                    help="Max worker threads for parsing (default: auto).")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Discover target .py files
    if args.from_tree:
        py_files = _py_files_from_tree_json(Path(args.from_tree).resolve())
    else:
        py_files = _discover_py_files(root, args.exclude)

    if not py_files:
        print("[extractor] No Python files found.")
        return

    # Parallel parse
    if args.workers is None:
        cpu = os.cpu_count() or 4
        workers = min(32, max(4, cpu * 5))
    else:
        workers = max(1, args.workers)

    print(f"[extractor] Processing {len(py_files)} Python files with {workers} workers…")

    futures = []
    results: List[Tuple[Path, List[FunctionRecord]]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for p in py_files:
            futures.append(ex.submit(extract_functions_from_file, p, root))
        for p, fut in zip(py_files, as_completed(futures)):
            try:
                recs = fut.result()
                results.append((p, recs))
            except Exception:
                results.append((p, []))

    # Write per-file JSON
    wrote = 0
    for file_path, recs in results:
        if recs:
            _write_functions_for_file(file_path, recs, root, out_dir)
            wrote += 1

    print(f"[extractor] Wrote JSON for {wrote} file(s) under: {out_dir}")


if __name__ == "__main__":
    main()
