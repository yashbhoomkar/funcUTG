from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Dict, Any, Tuple


# Default excludes for speed + sanity. You can extend/disable via CLI.
DEFAULT_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache",
    "build", "dist", ".tox",
    "node_modules",
}

DEFAULT_EXCLUDE_FILES = {".DS_Store"}


@dataclass
class Node:
    name: str
    path: str
    is_dir: bool
    size: Optional[int] = None          # file size in bytes, None for dirs
    children: List["Node"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe dict (good for Streamlit later)."""
        return {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
            "size": self.size,
            "children": [c.to_dict() for c in self.children],
        }


def _is_under(child: Path, parent: Path) -> bool:
    """Return True if `child` is inside `parent` (or equal)."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _sorted_entries(it: Iterable[os.DirEntry]) -> List[os.DirEntry]:
    # Dirs first (A–Z), then files (A–Z), case-insensitive
    entries = list(it)
    entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    return entries


def build_tree(
    root: Path | str,
    exclude_dirs_by_name: Optional[Iterable[str]] = None,
    exclude_files_by_name: Optional[Iterable[str]] = None,
    exclude_relpaths: Optional[Iterable[str]] = None,
    follow_symlinks: bool = False,
    max_depth: Optional[int] = None,
) -> Node:
    """
    Recursively build a Node tree for the directory at `root`.

    - exclude_dirs_by_name / exclude_files_by_name: simple name matches (no globs)
    - exclude_relpaths: relative folder paths from `root` to skip entirely
      (e.g., "myenv", "src/build", "notebooks/old")
    - follow_symlinks: whether to traverse symlinked dirs
    - max_depth: None for unlimited; 0 means only the root node
    """
    root = Path(root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Root not found: {root}")

    # Defaults (can be overridden by passing an explicit list)
    exclude_dirs_by_name = set(
        DEFAULT_EXCLUDE_DIRS if exclude_dirs_by_name is None else exclude_dirs_by_name
    )
    exclude_files_by_name = set(
        DEFAULT_EXCLUDE_FILES if exclude_files_by_name is None else exclude_files_by_name
    )

    # Normalize user-provided relative exclude paths to absolute Paths
    exclude_abs_paths: List[Path] = []
    for rel in (exclude_relpaths or []):
        # Clean any leading ./ and normalize
        rel_norm = Path(rel)
        # security: keep it under root only
        abs_path = (root / rel_norm).resolve()
        if _is_under(abs_path, root):
            exclude_abs_paths.append(abs_path)

    def _skip_entire_dir(path: Path) -> bool:
        # Skip if matches any absolute excluded path (as ancestor or equal)
        return any(_is_under(path, ex) for ex in exclude_abs_paths)

    def _walk(dir_path: Path, depth: int) -> Node:
        node = Node(name=dir_path.name or str(dir_path), path=str(dir_path), is_dir=True)

        if max_depth is not None and depth >= max_depth:
            return node

        if _skip_entire_dir(dir_path):
            # Return empty node; parent will simply not include this branch.
            return node

        try:
            with os.scandir(dir_path) as scan:
                for entry in _sorted_entries(scan):
                    try:
                        entry_path = Path(entry.path)
                        if entry.is_dir(follow_symlinks=follow_symlinks):
                            # name-based skip
                            if entry.name in exclude_dirs_by_name:
                                continue
                            # path-based skip
                            if _skip_entire_dir(entry_path):
                                continue
                            child = _walk(entry_path, depth + 1)
                            # If child has no children and is a dir we still include
                            # the node to reflect structure truthfully.
                            node.children.append(child)
                        else:
                            if entry.name in exclude_files_by_name:
                                continue
                            size = None
                            try:
                                size = entry.stat(follow_symlinks=False).st_size
                            except Exception:
                                pass
                            node.children.append(Node(
                                name=entry.name,
                                path=str(entry_path),
                                is_dir=False,
                                size=size,
                            ))
                    except PermissionError:
                        # Skip unreadable entries cleanly
                        continue
        except PermissionError:
            # Skip unreadable dirs cleanly
            pass

        return node

    if root.is_file():
        # If a file was passed, return a single-node tree
        size = None
        try:
            size = root.stat().st_size
        except Exception:
            pass
        return Node(name=root.name, path=str(root), is_dir=False, size=size)

    return _walk(root, depth=0)


# ---------- ASCII rendering for quick verification ----------

def _ascii_lines(node: Node, prefix: str = "", is_last: bool = True) -> List[str]:
    connector = "└── " if is_last else "├── "
    line = f"{prefix}{connector}{node.name}"
    lines = [line]

    if node.is_dir and node.children:
        new_prefix = f"{prefix}{'    ' if is_last else '│   '}"
        for i, child in enumerate(node.children):
            last = (i == len(node.children) - 1)
            lines.extend(_ascii_lines(child, new_prefix, last))

    return lines


def render_ascii_tree(node: Node) -> str:
    header = node.path if node.is_dir else f"{node.path} ({node.size or 0} B)"
    lines = [header]
    for i, child in enumerate(node.children):
        last = (i == len(node.children) - 1)
        lines.extend(_ascii_lines(child, "", last))
    return "\n".join(lines)


def count_nodes(node: Node) -> Tuple[int, int]:
    d = 1 if node.is_dir else 0
    f = 0 if node.is_dir else 1
    for ch in node.children:
        cd, cf = count_nodes(ch)
        d += cd
        f += cf
    return d, f


# ---------------------- CLI for testing/JSON dump ----------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Recursively list project structure.")
    ap.add_argument("root", nargs="?", default=".", help="Root folder (default: current dir).")
    ap.add_argument("--max-depth", type=int, default=None, help="Limit recursion depth.")

    # Name-based excludes (keep defaults unless disabled)
    ap.add_argument("--no-default-excludes", action="store_true",
                    help="Disable built-in name-based excludes (venv, __pycache__, .git, etc.).")
    ap.add_argument("--exclude-dir-name", action="append", default=[],
                    help="Additional directory NAME to exclude (can repeat).")
    ap.add_argument("--exclude-file-name", action="append", default=[],
                    help="Additional file NAME to exclude (can repeat).")

    # Path-based excludes relative to root
    ap.add_argument("--exclude", action="append", default=[],
                    help="Relative PATH (dir) under root to exclude entirely (can repeat). Example: --exclude myenv --exclude src/build")

    # JSON output
    ap.add_argument("--json-out", default=None,
                    help="Write the resulting tree to this JSON file. If omitted, no file is written.")
    ap.add_argument("--print-json", action="store_true",
                    help="Print JSON to stdout (in addition to ASCII tree).")

    args = ap.parse_args()

    # Compute effective name-based excludes
    dir_names = set()
    file_names = set()

    if args.no_default_excludes:
        # Only use what user provided
        dir_names = set(args.exclude_dir_name or [])
        file_names = set(args.exclude_file_name or [])
    else:
        # Merge defaults + user additions
        dir_names = set(DEFAULT_EXCLUDE_DIRS) | set(args.exclude_dir_name or [])
        file_names = set(DEFAULT_EXCLUDE_FILES) | set(args.exclude_file_name or [])

    tree = build_tree(
        args.root,
        exclude_dirs_by_name=dir_names,
        exclude_files_by_name=file_names,
        exclude_relpaths=args.exclude,
        max_depth=args.max_depth,
    )

    # ASCII preview
    print(render_ascii_tree(tree))
    dirs, files = count_nodes(tree)
    print(f"\nSummary: {dirs} dirs, {files} files")

    # JSON to stdout
    if args.print_json:
        print(json.dumps(tree.to_dict(), ensure_ascii=False, indent=2))

    # JSON to file
    if args.json_out:
        out_path = Path(args.json_out).resolve()
        out_path.write_text(json.dumps(tree.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[treewalker] JSON saved → {out_path}")
