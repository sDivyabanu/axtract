"""Image source inventory: deliberately conservative.

Observed deterministically: format, pixel dimensions, colour mode, DPI metadata, frame count.

What is NOT claimed: any text, table, chart or layout. Reading them would need an OCR/vision
engine, and the only one available is the OCR AXTRACT already used. Comparing a result with the
same engine's second run proves nothing about correctness, so this inventory records that
independent text evidence is unavailable instead of inventing some.
"""

from __future__ import annotations

import time
from pathlib import Path

from verify.inventory.models import (
    InventorySignal, SourceInventory, SourceLocator, SourceObject, SourceObjectType, SourceUnit,
)
from verify.inventory.options import InventoryOptions
from verify.models import EvidenceKind, UnitRef, UnitType

DET, NA = EvidenceKind.DETERMINISTIC, EvidenceKind.UNAVAILABLE
SIGNALS = [
    InventorySignal(name="format_dimensions_mode", kind=DET, engine="PIL"),
    InventorySignal(name="dpi_and_frame_count", kind=DET, engine="PIL"),
    InventorySignal(name="text_regions", kind=NA, engine="none", available=False,
                    note="only OCR could read them, and AXTRACT already used that same OCR engine"),
    InventorySignal(name="tables_charts_figures", kind=NA, engine="none", available=False,
                    note="would need a layout/vision model; not used at this step"),
]
LIMITATIONS = [
    "OCR output cannot be validated independently here: the available reader is the one AXTRACT used.",
    "Only the file's own properties are known; nothing about what the image depicts.",
]


def inventory_image(path: Path, opts: InventoryOptions | None = None) -> SourceInventory:
    t0 = time.perf_counter()
    ext = path.suffix.lower().lstrip(".")
    inv = SourceInventory(file_type=ext, signals=list(SIGNALS), limitations=list(LIMITATIONS))
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
            props = dict(format=im.format, width_px=w, height_px=h, mode=im.mode,
                         dpi=tuple(round(float(d), 2) for d in im.info["dpi"]) if "dpi" in im.info else None,
                         frames=getattr(im, "n_frames", 1))
        unit = UnitRef(type=UnitType.IMAGE, index=1)
        inv.properties.update(props)
        inv.units.append(SourceUnit(
            unit=unit, properties=props, independent_text_available=False,
            independence_note="OCR is the only reader and it is the engine AXTRACT used; self-agreement proves nothing",
            objects=[SourceObject(
                id="image:1", type=SourceObjectType.PICTURE, unit=unit, locator=SourceLocator(page=1, bbox=(0.0, 0.0, 1.0, 1.0)),
                kind=DET, engine="PIL", metadata=props)]))
    except Exception as exc:  # noqa: BLE001 - an inventory failure never affects the extraction
        inv.error = f"{type(exc).__name__}: {exc}"
    inv.timing_ms = round((time.perf_counter() - t0) * 1000, 3)
    return inv
