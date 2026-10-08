"""Minimal, safe OOXML package reader shared by the XLSX / PPTX / DOCX inventories.

Independent of openpyxl, python-pptx and python-docx: it reads the raw ZIP + XML. Parsing uses
defusedxml, and zip bombs are refused before anything is decompressed.
"""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path

from defusedxml import ElementTree as ET

MAX_TOTAL_UNCOMPRESSED = 600 * 1024 * 1024
MAX_PART_BYTES = 200 * 1024 * 1024

NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
}


def q(prefix: str, name: str) -> str:
    """Clark-notation tag: q('w', 'p') -> '{...wordprocessingml/2006/main}p'."""
    return f"{{{NS[prefix]}}}{name}"


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


class PackageError(Exception):
    pass


class Package:
    def __init__(self, path: Path):
        try:
            self._zip = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise PackageError(f"not a readable OOXML package: {exc}") from exc
        total = sum(i.file_size for i in self._zip.infolist())
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise PackageError("package is too large to inventory safely")
        self._names = set(self._zip.namelist())

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "Package":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def has(self, part: str) -> bool:
        return part in self._names

    def read(self, part: str) -> bytes:
        info = self._zip.getinfo(part)
        if info.file_size > MAX_PART_BYTES:
            raise PackageError(f"{part} is too large to inventory safely")
        return self._zip.read(part)

    def xml(self, part: str):
        return ET.fromstring(self.read(part))

    def open(self, part: str):
        return self._zip.open(part)

    def rels(self, part: str) -> dict[str, tuple[str, str]]:
        """rId -> (relationship type, absolute part name) for the relationships of `part`."""
        folder, name = posixpath.split(part)
        rel_part = posixpath.join(folder, "_rels", name + ".rels")
        if not self.has(rel_part):
            return {}
        out = {}
        for rel in self.xml(rel_part):
            target = rel.get("Target", "")
            if rel.get("TargetMode") == "External":
                continue
            absolute = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
            out[rel.get("Id", "")] = (rel.get("Type", ""), absolute)
        return out


def text_of(el, *tags: str) -> str:
    """Concatenate the text of descendant elements whose local name is in `tags`."""
    return "".join((n.text or "") for n in el.iter() if local(n.tag) in tags)
