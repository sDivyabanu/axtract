"""Independent source inventories: what exists in the ORIGINAL file.

`build_inventory()` never raises. If a collector fails, the returned inventory carries `error`
and no units, and the extraction is unaffected.
"""

from __future__ import annotations

from pathlib import Path

from verify.inventory.docx import inventory_docx
from verify.inventory.image import inventory_image
from verify.inventory.models import (
    Contract, InventorySignal, SourceInventory, SourceLocator, SourceObject, SourceObjectType, SourceUnit,
)
from verify.inventory.options import InventoryOptions
from verify.inventory.pdf import inventory_pdf
from verify.inventory.pptx import inventory_pptx
from verify.inventory.xlsx import inventory_xlsx

_COLLECTORS = {
    "xlsx": inventory_xlsx, "pptx": inventory_pptx, "docx": inventory_docx, "pdf": inventory_pdf,
    "png": inventory_image, "jpg": inventory_image, "jpeg": inventory_image,
}


def build_inventory(path: Path, file_type: str, opts: InventoryOptions | None = None) -> SourceInventory:
    ft = (file_type or "").lower()
    collector = _COLLECTORS.get(ft)
    if collector is None:
        return SourceInventory(file_type=ft, error=f"no inventory collector for .{ft}")
    try:
        return collector(Path(path), opts)
    except Exception as exc:  # noqa: BLE001 - collectors already guard themselves; this is the last net
        return SourceInventory(file_type=ft, error=f"{type(exc).__name__}: {exc}")


__all__ = [
    "Contract", "InventoryOptions", "InventorySignal", "SourceInventory", "SourceLocator", "SourceObject",
    "SourceObjectType", "SourceUnit", "build_inventory",
]
