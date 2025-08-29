from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional, Dict, Tuple, Set
from concurrent.futures import ThreadPoolExecutor

from .model import Node, render_ascii_tree, count_nodes
from .filters import (
    DEFAULT_EXCLUDE_DIRS,
    DEFAULT_EXCLUDE_FILES,
    norm_ext,
    is_under,
    sorted_entries,
)

__all__ = [
    "build_tree",
    "render_ascii_tree",
    "count_nodes",
    "DEFAULT_EXCLUDE_DIRS",
    "DEFAULT_EXCLUDE_FILES",
]
# --------------------- core (parallel) ---------------------

def build_tree(
    root: Path | str,
    *,
    exclude_dirs_by_name: Optional[Iterable[str]] = None,
    exclude_files_by_name: Optional[Iterable[str]] = None,
    exclude_relpaths: Optional[Iterable[str]] = None,
    include_exts: Optional[Iterable[str]] = None,
    follow_symlinks: bool = False,
    max_depth: Optional[int] = None,
    max_workers: Optional[int] = None,
) -> Node:
    """
    Build a directory tree rooted at `root` (parallelized).

    - exclude_dirs_by_name / exclude_files_by_name: plain name filters (no globs)
    - exclude_relpaths: subtrees to skip, relative to `root` (e.g., "myenv", "src/build")
    - include_exts: when provided, include ONLY these file extensions ('.py', 'md', etc.)
    - follow_symlinks: False by default
    - max_depth: None for unlimited; 0 means only root node
    - max_workers: None → auto (IO-optimized); set explicitly to control parallelism
    """
    root = Path(root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Root not found: {root}")

    # Normalize filters
    dir_names = set(DEFAULT_EXCLUDE_DIRS if exclude_dirs_by_name is None else exclude_dirs_by_name)
    file_names = set(DEFAULT_EXCLUDE_FILES if exclude_files_by_name is None else exclude_files_by_name)

    include_exts_norm: Optional[Set[str]] = None
    if include_exts:
        include_exts_norm = {norm_ext(e) for e in include_exts if norm_ext(e)}

    exclude_abs_paths: List[Path] = []
    for rel in (exclude_relpaths or []):
        p = (root / Path(rel)).resolve()
        if is_under(p, root):
            exclude_abs_paths.append(p)

    def _skip_entire_dir(path: Path) -> bool:
        return any(is_under(path, ex) for ex in exclude_abs_paths)

    # Choose worker count tuned for I/O (dir walking/stat are I/O-heavy)
    if max_workers is None:
        cpu = os.cpu_count() or 4
        max_workers = min(32, max(4, cpu * 5))

    def _scan_dir(dir_path: Path, depth: int, executor: ThreadPoolExecutor) -> Node:
        node = Node.directory(dir_path)

        if max_depth is not None and depth >= max_depth:
            return node

        if _skip_entire_dir(dir_path):
            return node

        try:
            with os.scandir(dir_path) as scan:
                entries = sorted_entries(scan)
        except PermissionError:
            return node

        dir_futures: List[Tuple[os.DirEntry, "object"]] = []
        files_to_add: List[Node] = []

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=follow_symlinks):
                    if entry.name in dir_names:
                        continue
                    p = Path(entry.path)
                    if _skip_entire_dir(p):
                        continue
                    fut = executor.submit(_scan_dir, p, depth + 1, executor)
                    dir_futures.append((entry, fut))
                else:
                    if entry.name in file_names:
                        continue
                    ep = Path(entry.path)
                    if include_exts_norm is not None and ep.suffix.lower() not in include_exts_norm:
                        continue
                    size = None
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except Exception:
                        pass
                    files_to_add.append(Node.file(ep, size))
            except PermissionError:
                continue

        # Collect directory results in deterministic order
        for entry, fut in dir_futures:
            try:
                child = fut.result()
                node.children.append(child)
            except Exception:
                continue

        node.children.extend(files_to_add)
        return node

    if root.is_file():
        size = None
        try:
            size = root.stat().st_size
        except Exception:
            pass
        if include_exts_norm is not None and root.suffix.lower() not in include_exts_norm:
            # Represent as an empty folder (filtered out single file)
            return Node.directory(root.parent)
        return Node.file(root, size)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return _scan_dir(root, depth=0, executor=ex)
