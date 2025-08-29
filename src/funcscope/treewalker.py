from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, Optional, Set

# ✅ Correct imports
from .walker import build_tree, render_ascii_tree, count_nodes
from .filters import DEFAULT_EXCLUDE_DIRS, DEFAULT_EXCLUDE_FILES


def _normalize_exts(exts: Optional[Iterable[str]]) -> Optional[Set[str]]:
    """
    Accept ".py" or "py" etc. Return a normalized set with leading dots and lowercase.
    (We normalize here only for display; walker also normalizes internally.)
    """
    if not exts:
        return None
    out: Set[str] = set()
    for e in exts:
        e = (e or "").strip().lower()
        if not e:
            continue
        out.add(e if e.startswith(".") else f".{e}")
    return out


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(
        prog="python -m src.funcscope.treewalker",
        description="Recursively list project structure (parallel) and export JSON.",
    )

    # Root / depth / workers
    ap.add_argument("root", nargs="?", default=".", help="Root folder (default: current dir).")
    ap.add_argument("--max-depth", type=int, default=None, help="Limit recursion depth.")
    ap.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Max worker threads (default: auto; I/O-optimized).",
    )

    # Name-based excludes (keep defaults unless disabled)
    ap.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="Disable built-in name-based excludes (venv, __pycache__, .git, etc.).",
    )
    ap.add_argument(
        "--exclude-dir-name",
        action="append",
        default=[],
        help="Additional directory NAME to exclude (repeatable).",
    )
    ap.add_argument(
        "--exclude-file-name",
        action="append",
        default=[],
        help="Additional file NAME to exclude (repeatable).",
    )

    # Path-based excludes relative to root (skip whole subtrees)
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Relative PATH (dir) under root to exclude entirely (repeatable). "
             "Example: --exclude myenv --exclude src/build",
    )

    # File-type filters
    g = ap.add_mutually_exclusive_group()
    g.add_argument(
        "--only-py",
        dest="only_py",
        action="store_true",
        help="Include only '.py' files.",
    )
    g.add_argument(
        "--include-ext",
        action="append",
        default=[],
        help="Include only these extensions (repeatable). Example: --include-ext py --include-ext .md",
    )

    # JSON output controls
    ap.add_argument(
        "--json-out",
        default=None,
        help="Write the resulting tree to this JSON file. "
             "If omitted, defaults to './funcscope-tree.json'.",
    )
    ap.add_argument(
        "--no-json",
        action="store_true",
        help="Do not write any JSON file (overrides --json-out).",
    )
    ap.add_argument(
        "--print-json",
        action="store_true",
        help="Also print JSON to stdout.",
    )

    args = ap.parse_args(argv)

    # Effective name-based excludes
    if args.no_default_excludes:
        exclude_dir_names = set(args.exclude_dir_name or [])
        exclude_file_names = set(args.exclude_file_name or [])
    else:
        exclude_dir_names = set(DEFAULT_EXCLUDE_DIRS) | set(args.exclude_dir_name or [])
        exclude_file_names = set(DEFAULT_EXCLUDE_FILES) | set(args.exclude_file_name or [])

    # Effective extension filter
    include_exts = None
    if args.only_py:
        include_exts = {".py"}
    elif args.include_ext:
        include_exts = _normalize_exts(args.include_ext)

    # Build the tree (parallel)
    tree = build_tree(
        args.root,
        exclude_dirs_by_name=exclude_dir_names,
        exclude_files_by_name=exclude_file_names,
        exclude_relpaths=args.exclude,
        include_exts=include_exts,
        max_depth=args.max_depth,
        max_workers=args.workers,
    )

    # ASCII preview
    print(render_ascii_tree(tree))
    dirs, files = count_nodes(tree)
    print(f"\nSummary: {dirs} dirs, {files} files")

    # JSON output (default path if none provided), unless disabled
    if not args.no_json:
        out_path = Path(args.json_out or "funcscope-tree.json").resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(tree.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[treewalker] JSON saved → {out_path}")

    if args.print_json:
        print(json.dumps(tree.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
