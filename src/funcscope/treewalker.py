from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, List, Optional, Dict, Any, Tuple


# Default excludes for speed + sanity. You can extend at runtime.
DEFAULT_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache",
    "build", "dist", ".tox", ".DS_Store",
    "node_modules",
}

DEFAULT_EXCLUDE_FILES = {
    ".DS_Store"
}


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


def _should_skip_dir(entry: os.DirEntry, exclude_dirs: Iterable[str]) -> bool:
    name = entry.name
    return name in exclude_dirs


def _should_skip_file(entry: os.DirEntry, exclude_files: Iterable[str]) -> bool:
    name = entry.name
    return name in exclude_files


def _sorted_entries(it: Iterable[os.DirEntry]) -> List[os.DirEntry]:
    # Dirs first (A–Z), then files (A–Z), case-insensitive
    entries = list(it)
    entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    return entries


def build_tree(
    root: Path | str,
    exclude_dirs: Optional[Iterable[str]] = None,
    exclude_files: Optional[Iterable[str]] = None,
    follow_symlinks: bool = False,
    max_depth: Optional[int] = None,
) -> Node:
    """
    Recursively build a Node tree for the directory at `root`.

    - exclude_dirs / exclude_files: names to skip (not globs, simple names)
    - follow_symlinks: whether to traverse symlinked dirs
    - max_depth: None for unlimited; 0 means only the root node
    """
    root = Path(root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Root not found: {root}")

    exclude_dirs = set(DEFAULT_EXCLUDE_DIRS if exclude_dirs is None else exclude_dirs)
    exclude_files = set(DEFAULT_EXCLUDE_FILES if exclude_files is None else exclude_files)

    def _walk(dir_path: Path, depth: int) -> Node:
        node = Node(name=dir_path.name or str(dir_path), path=str(dir_path), is_dir=True)

        if max_depth is not None and depth >= max_depth:
            return node

        try:
            with os.scandir(dir_path) as scan:
                for entry in _sorted_entries(scan):
                    try:
                        if entry.is_dir(follow_symlinks=follow_symlinks):
                            if _should_skip_dir(entry, exclude_dirs):
                                continue
                            child = _walk(Path(entry.path), depth + 1)
                            node.children.append(child)
                        else:
                            if _should_skip_file(entry, exclude_files):
                                continue
                            size = None
                            try:
                                size = entry.stat(follow_symlinks=False).st_size
                            except Exception:
                                pass
                            node.children.append(Node(
                                name=entry.name,
                                path=str(Path(entry.path)),
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


# ---------- Convenience: ASCII rendering for quick verification ----------

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
    """
    Return a `tree`-like ASCII string for the given Node.
    """
    # root header
    header = node.path if node.is_dir else f"{node.path} ({node.size or 0} B)"
    lines = [header]
    # children
    for i, child in enumerate(node.children):
        last = (i == len(node.children) - 1)
        lines.extend(_ascii_lines(child, "", last))
    return "\n".join(lines)


def count_nodes(node: Node) -> Tuple[int, int]:
    """
    Return (dir_count, file_count) for the subtree.
    """
    d = 1 if node.is_dir else 0
    f = 0 if node.is_dir else 1
    for ch in node.children:
        cd, cf = count_nodes(ch)
        d += cd
        f += cf
    return d, f


# Allow `python -m src.funcscope.treewalker <path>` for quick testing
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Recursively list project structure.")
    ap.add_argument("root", nargs="?", default=".", help="Root folder (default: current dir).")
    ap.add_argument("--max-depth", type=int, default=None, help="Limit recursion depth.")
    ap.add_argument("--no-default-excludes", action="store_true", help="Disable built-in excludes.")
    args = ap.parse_args()

    ex_dirs = [] if args.no_default_excludes else None
    ex_files = [] if args.no_default_excludes else None

    tree = build_tree(args.root, exclude_dirs=ex_dirs, exclude_files=ex_files, max_depth=args.max_depth)
    print(render_ascii_tree(tree))
    dirs, files = count_nodes(tree)
    print(f"\nSummary: {dirs} dirs, {files} files")
