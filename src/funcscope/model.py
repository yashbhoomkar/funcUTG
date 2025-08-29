from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


__all__ = [
    "Node",
    "render_ascii_tree",
    "count_nodes",
]


@dataclass
class Node:
    """
    Represents a path entry in the project tree.

    - Directories: is_dir=True, size=None, children=[...]
    - Files:       is_dir=False, size=<bytes>, children=[]
    """
    name: str
    path: str
    is_dir: bool
    size: Optional[int] = None
    children: List["Node"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe representation suitable for writing to disk or UI transport."""
        return {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
            "size": self.size,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def file(cls, p: Path, size: Optional[int]) -> "Node":
        """Convenience constructor for files."""
        return cls(name=p.name, path=str(p), is_dir=False, size=size, children=[])

    @classmethod
    def directory(cls, p: Path) -> "Node":
        """Convenience constructor for directories (initially empty)."""
        name = p.name or str(p)
        return cls(name=name, path=str(p), is_dir=True, size=None, children=[])


def render_ascii_tree(node: Node) -> str:
    """
    Render a `tree`-style ASCII view of the subtree rooted at `node`.
    Directories come first in insertion order; files follow.
    """
    header = node.path if node.is_dir else f"{node.path} ({node.size or 0} B)"
    lines = [header]

    def _ascii_lines(n: Node, prefix: str = "", is_last: bool = True) -> List[str]:
        connector = "└── " if is_last else "├── "
        line = f"{prefix}{connector}{n.name}"
        out = [line]
        if n.is_dir and n.children:
            new_prefix = f"{prefix}{'    ' if is_last else '│   '}"
            for i, ch in enumerate(n.children):
                last = (i == len(n.children) - 1)
                out.extend(_ascii_lines(ch, new_prefix, last))
        return out

    for i, ch in enumerate(node.children):
        last = (i == len(node.children) - 1)
        lines.extend(_ascii_lines(ch, "", last))

    return "\n".join(lines)


def count_nodes(node: Node) -> Tuple[int, int]:
    """
    Return (dir_count, file_count) for the subtree rooted at `node`.
    Counts include the root node if it is a directory.
    """
    dirs = 1 if node.is_dir else 0
    files = 0 if node.is_dir else 1
    for ch in node.children:
        cd, cf = count_nodes(ch)
        dirs += cd
        files += cf
    return dirs, files
