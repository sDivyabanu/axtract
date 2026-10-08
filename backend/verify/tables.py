"""Table comparison shared by the content and structure layers.

One function decides what KIND of difference two tables have, so the layers never double-report:
  identical          every cell equal after formatting normalisation
  cells_changed      same shape, some cell values differ            -> CONTENT
  rows_reordered     same shape and the same set of rows, new order  -> STRUCTURE
  columns_reordered  same shape and the same set of columns          -> STRUCTURE
  dimensions         a different number of rows or columns           -> STRUCTURE
                     (cell values in the overlapping area are still compared, for CONTENT)

None and "" are the same empty cell. Trailing empty rows/columns are ignored on both sides because
the extraction trims them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verify.normalize import normalize_text
from verify.textdiff import has_digit

MAX_CELL_DIFFS = 200


def cell_key(v: Any) -> str:
    return "" if v is None else normalize_text(str(v), canonical_numbers=True)


@dataclass(frozen=True)
class CellDiff:
    row: int  # 0-based
    col: int
    src: str
    out: str

    @property
    def numeric(self) -> bool:
        return has_digit(self.src) or has_digit(self.out)


@dataclass
class TableDiff:
    kind: str
    src_dims: tuple[int, int]
    out_dims: tuple[int, int]
    cell_diffs: list[CellDiff] = field(default_factory=list)
    compared_cells: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


def _grid(rows) -> list[list[str]]:
    g = [[cell_key(c) for c in (r if isinstance(r, (list, tuple)) else [])] for r in (rows or [])]
    width = max((len(r) for r in g), default=0)
    g = [r + [""] * (width - len(r)) for r in g]
    while g and not any(g[-1]):  # trailing empty rows
        g.pop()
    while g and width and not any(r[width - 1] for r in g):  # trailing empty columns
        width -= 1
        g = [r[:width] for r in g]
    return g


def ref(row: int, col: int) -> str:
    s, n = "", col + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row + 1}"


def diff_tables(src_rows, out_rows) -> TableDiff:
    a, b = _grid(src_rows), _grid(out_rows)
    da, db = (len(a), len(a[0]) if a else 0), (len(b), len(b[0]) if b else 0)
    diffs: list[CellDiff] = []
    compared = 0
    for r in range(min(da[0], db[0])):
        for c in range(min(da[1], db[1])):
            compared += 1
            if a[r][c] != b[r][c] and len(diffs) < MAX_CELL_DIFFS:
                diffs.append(CellDiff(r, c, a[r][c], b[r][c]))
    if da != db:
        return TableDiff("dimensions", da, db, diffs, compared)
    if not diffs and a == b:
        return TableDiff("identical", da, db, [], compared)
    if sorted(map(tuple, a)) == sorted(map(tuple, b)):
        moved = [r for r in range(da[0]) if a[r] != b[r]]
        return TableDiff("rows_reordered", da, db, diffs, compared, {"rows_out_of_place": moved})
    if sorted(zip(*a)) == sorted(zip(*b)):
        moved = [c for c in range(da[1]) if [row[c] for row in a] != [row[c] for row in b]]
        return TableDiff("columns_reordered", da, db, diffs, compared, {"columns_out_of_place": moved})
    return TableDiff("cells_changed", da, db, diffs, compared)


def moved(strict_a, strict_b, loose_a, loose_b) -> tuple[str, list[int]] | None:
    """Were the cells that differ merely MOVED? Tests the hypothesis on the differing rows (or columns) only.

    `strict_*` are exact grids; `loose_*` read cells without relying on position, so a value moved to a
    cell of a different type still compares equal. Returns ("rows_reordered" | "columns_reordered", indices).
    """
    if len(strict_a) != len(strict_b) or (strict_a and len(strict_a[0]) != len(strict_b[0])):
        return None
    bad_rows = [r for r in range(len(strict_a)) if strict_a[r] != strict_b[r]]
    if len(bad_rows) > 1 and sorted(tuple(loose_a[r]) for r in bad_rows) == sorted(tuple(loose_b[r]) for r in bad_rows):
        return "rows_reordered", bad_rows
    width = len(strict_a[0]) if strict_a else 0
    bad_cols = [c for c in range(width) if [row[c] for row in strict_a] != [row[c] for row in strict_b]]
    if len(bad_cols) > 1 and sorted(tuple(row[c] for row in loose_a) for c in bad_cols) == sorted(tuple(row[c] for row in loose_b) for c in bad_cols):
        return "columns_reordered", bad_cols
    return None
