from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional, Set


# Public exports
__all__ = [
    "DEFAULT_EXCLUDE_DIRS",
    "DEFAULT_EXCLUDE_FILES",
    "norm_ext",
    "is_under",
    "sorted_entries",
    "effective_name_excludes",
]

# Sensible production defaults
DEFAULT_EXCLUDE_DIRS: Set[str] = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache",
    "build", "dist", ".tox",
    "node_modules",
}
DEFAULT_EXCLUDE_FILES: Set[str] = {".DS_Store"}


def norm_ext(ext: str) -> str:
    """Normalize extension to '.ext' lowercase. Accepts 'py' or '.py'."""
    ext = (ext or "").strip().lower()
    if not ext:
        return ""
    return ext if ext.startswith(".") else f".{ext}"


def is_under(child: Path, parent: Path) -> bool:
    """True if `child` is inside `parent` (or equal). Robust across symlinks."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def sorted_entries(it: Iterable[os.DirEntry]) -> List[os.DirEntry]:
    """Deterministic ordering: directories first (A–Z), then files (A–Z)."""
    entries = list(it)
    entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    return entries


def effective_name_excludes(
    *,
    no_defaults: bool,
    extra_dir_names: Optional[Iterable[str]] = None,
    extra_file_names: Optional[Iterable[str]] = None,
) -> tuple[Set[str], Set[str]]:
    """
    Merge built-in name excludes with user-provided ones, or use only user ones
    if no_defaults=True.
    """
    extra_dir_names = set(extra_dir_names or [])
    extra_file_names = set(extra_file_names or [])
    if no_defaults:
        return extra_dir_names, extra_file_names
    return (set(DEFAULT_EXCLUDE_DIRS) | extra_dir_names,
            set(DEFAULT_EXCLUDE_FILES) | extra_file_names)
