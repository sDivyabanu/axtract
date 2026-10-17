"""Conservative, bounded table recovery from pixels and existing OCR boxes.

Ruling lines establish cells; persistent whitespace may subdivide a ruled
band, but never turns an unrestricted page of prose into a table. Source
tokens, cell boxes and the exact consumed OCR blocks remain in metadata.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from statistics import median

import numpy as np
from PIL import Image

from models.document import BlockType, DocumentBlock
from utils import deadline

MAX_SIDE = 2500
MAX_PIXELS = 4_500_000
MAX_CANDIDATES = 12


@dataclass(frozen=True)
class Token:
    box: tuple[float, float, float, float]
    text: str
    source_id: str
    confidence: float | None = None


def _groups(values: list[int]) -> list[float]:
    groups: list[list[int]] = []
    for value in values:
        if groups and value <= groups[-1][-1] + 2:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(g) / len(g) for g in groups]


def line_masks(image: Image.Image):
    """Bound allocations before converting pixels or running morphology."""
    if max(image.size) > MAX_SIDE or image.width * image.height > MAX_PIXELS:
        raise ValueError("Table raster exceeds the page render caps")
    import cv2

    gray = np.asarray(image.convert("L"))
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 31, 15)
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                np.ones((1, max(24, image.width // 40)), np.uint8))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                              np.ones((max(20, image.height // 60), 1), np.uint8))
    return horizontal, vertical


def ruled_regions(horizontal, vertical) -> list[tuple[int, int, int, int]]:
    import cv2

    height, width = horizontal.shape
    mask = cv2.dilate(horizontal | vertical, np.ones((3, 3), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    candidates = []
    for x, y, w, h, _ in stats[1:]:
        if w < max(80, width * .12) or h < 35 or w * h < 5000:
            continue
        xs = _groups(np.flatnonzero((vertical[y:y+h, x:x+w] > 0).mean(axis=0) > .6).tolist())
        ys = _groups(np.flatnonzero((horizontal[y:y+h, x:x+w] > 0).mean(axis=1) > .6).tolist())
        # Closed side walls + repeated horizontal boundaries. Chart axes alone
        # and rows of little diagram boxes do not meet this evidence threshold.
        if len(xs) >= 2 and len(ys) >= 3 and xs[0] < .08*w and xs[-1] > .92*w:
            candidates.append((int(x), int(y), int(x+w), int(y+h)))
    # A figure frame surrounding several independently ruled tables is not
    # another table. Keep the smaller structures instead of duplicating them.
    return [b for b in candidates if not any(
        b != c and b[0] <= c[0] and b[1] <= c[1] and b[2] >= c[2] and b[3] >= c[3]
        for c in candidates)][:MAX_CANDIDATES]


def text_lines(tokens: list[Token]) -> list[list[Token]]:
    if not tokens:
        return []
    tolerance = max(1., median(t.box[3]-t.box[1] for t in tokens) * .55)
    lines: list[list[Token]] = []
    for token in sorted(tokens, key=lambda t: ((t.box[1]+t.box[3])/2, t.box[0], t.source_id)):
        cy = (token.box[1]+token.box[3])/2
        if lines and abs(cy - median((t.box[1]+t.box[3])/2 for t in lines[-1])) <= tolerance:
            lines[-1].append(token)
        else:
            lines.append([token])
    return [sorted(line, key=lambda t: (t.box[0], t.source_id)) for line in lines]


def _column_cuts(tokens: list[Token], left: float, right: float) -> list[float]:
    """Persistent gutters across densely populated baselines, not left-edge chaining."""
    lines = [line for line in text_lines(tokens) if len(line) >= 3]
    if len(lines) < 3:
        return []
    # Repeated body baselines carry stronger column evidence than wrapped headers.
    dense = [line for line in lines if len(line) >= max(3, median(map(len, lines)))]
    numeric_body = [line for line in dense
                    if sum(bool(re.search(r"\d", t.text)) for t in line) >= .5*len(line)]
    if len(numeric_body) >= 3:
        dense = numeric_body
    if len(dense) < 3:
        return []
    width = int(right-left)+1
    if width <= 0:
        return []
    occupied = np.zeros(width, dtype=np.int16)
    for line in dense:
        row = np.zeros(width, dtype=bool)
        for t in line:
            row[max(0, int(t.box[0]-left)):min(width, int(np.ceil(t.box[2]-left))+1)] = True
        occupied += row
    empty = np.flatnonzero(occupied <= int(len(dense)*.15)).tolist()
    runs: list[list[int]] = []
    for x in empty:
        if runs and x == runs[-1][-1]+1:
            runs[-1].append(x)
        else:
            runs.append([x])
    char_width = median((t.box[2]-t.box[0])/max(1, len(t.text)) for line in dense for t in line)
    return [left+(run[0]+run[-1])/2 for run in runs
            if len(run) >= max(3., char_width*.9) and run[0] > 2 and run[-1] < width-3]


def reconstruct(tokens: list[Token], box, horizontal, vertical, *, layout_evidence=False):
    """Return rows/geometry/spans only when source boxes support a real grid."""
    x0, y0, x1, y1 = box
    tokens = [t for t in tokens if t.text.strip() and x0 <= (t.box[0]+t.box[2])/2 <= x1
              and y0 <= (t.box[1]+t.box[3])/2 <= y1]
    if len(tokens) < 6:
        return None
    xs = [x0+x for x in _groups(np.flatnonzero((vertical[y0:y1, x0:x1] > 0).mean(axis=0) > .6).tolist())]
    ys = [y0+y for y in _groups(np.flatnonzero((horizontal[y0:y1, x0:x1] > 0).mean(axis=1) > .3).tolist())]
    if len(xs) < 2 or len(ys) < 3:
        if not layout_evidence:
            return None
        xs, ys = [float(x0), float(x1)], [float(y0), float(y1)]
    inferred_columns = len(xs) == 2
    if inferred_columns:
        math_tokens = sum(bool(re.search(r"[=\u222b\u2211\u221a^]|\\(?:frac|sum|int)\b", t.text))
                          for t in tokens)
        standalone_operator = any(t.text.strip() in {"=", "\u222b", "\u2211", "\u221a", "^"} for t in tokens)
        if standalone_operator or math_tokens >= max(2, len(tokens)*.2):
            return None  # Aligned equations are not whitespace-defined tables.
        cuts = _column_cuts(tokens, xs[0], xs[-1])
        if not cuts:
            return None
        xs = [xs[0], *cuts, xs[-1]]
    if not 2 <= len(xs)-1 <= 32:
        return None
    physical_y_count = len(ys)
    height = median(t.box[3]-t.box[1] for t in tokens)
    boundaries = [ys[0]]
    for i, (top, bottom) in enumerate(zip(ys, ys[1:])):
        inside = [t for t in tokens if top < (t.box[1]+t.box[3])/2 < bottom]
        lines = text_lines(inside)
        # A physically bounded header band keeps its wrapped text. A tall body
        # band is split only on >=3 dense, short-cell baselines.
        dense = [line for line in lines if len(line) >= max(3, (len(xs)-1)*.6)
                 and sum(len(t.text.split()) <= 3 for t in line) >= .8*len(line)]
        if (i > 0 or layout_evidence) and bottom-top > 2.5*height and len(dense) >= 3 and len(dense) == len(lines):
            centers = [median((t.box[1]+t.box[3])/2 for t in line) for line in lines]
            boundaries.extend((a+b)/2 for a,b in zip(centers, centers[1:]))
        boundaries.append(bottom)
    ys = boundaries
    nr, nc = len(ys)-1, len(xs)-1
    if nr < 2 or nr > 100 or nr*nc > 1600:
        return None
    assigned = [[[] for _ in range(nc)] for _ in range(nr)]
    for t in tokens:
        cx, cy = (t.box[0]+t.box[2])/2, (t.box[1]+t.box[3])/2
        c = min(nc-1, max(0, int(np.searchsorted(xs, cx, side="right")-1)))
        r = min(nr-1, max(0, int(np.searchsorted(ys, cy, side="right")-1)))
        assigned[r][c].append(t)
    rows = [["\n".join(" ".join(t.text.strip() for t in line) for line in text_lines(cell))
             for cell in row] for row in assigned]
    populated = sum(bool(cell) for row in rows for cell in row)
    if populated < 6 or populated/(nr*nc) < .45:
        return None
    # A plot grid has labels on its edges, not a dense body of cells. Long
    # newspaper paragraphs likewise fail short-cell evidence for inferred grids.
    if inferred_columns and (nr < 3 or sum(len(c.split()) > 12 for row in rows for c in row) > .15*populated):
        return None
    cells = [[(xs[c], ys[r], xs[c+1], ys[r+1]) for c in range(nc)] for r in range(nr)]
    # Merge only where BOTH coordinates come from actual rulings and an internal
    # segment is absent. Never interpret an ordinary empty cell as a span.
    spans = []
    if not inferred_columns and len(boundaries) == physical_y_count:
        parent = list(range(nr*nc))

        def root(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def join(a,b):
            parent[root(b)] = root(a)

        for r in range(nr):
            for c in range(nc):
                if c+1 < nc:
                    x=int(round(xs[c+1]));a=int(ys[r]+3);b=int(ys[r+1]-3)
                    support=(vertical[a:b,max(0,x-2):x+3]>0).any(axis=1).mean() if b>a else 1.
                    if support < .4:
                        join(r*nc+c,r*nc+c+1)
                if r+1 < nr:
                    y=int(round(ys[r+1]));a=int(xs[c]+3);b=int(xs[c+1]-3)
                    support=(horizontal[max(0,y-2):y+3,a:b]>0).any(axis=0).mean() if b>a else 1.
                    if support < .4:
                        join(r*nc+c,(r+1)*nc+c)
        components: dict[int,list[tuple[int,int]]] = {}
        for r in range(nr):
            for c in range(nc):
                components.setdefault(root(r*nc+c),[]).append((r,c))
        for component in components.values():
            if len(component)==1:
                continue
            r0=min(r for r,c in component);r1=max(r for r,c in component)
            c0=min(c for r,c in component);c1=max(c for r,c in component)
            if len(component)!=(r1-r0+1)*(c1-c0+1):
                return None  # Conflicting/broken borders cannot prove a valid span.
            group=[t for r,c in component for t in assigned[r][c]]
            for r,c in component:
                rows[r][c]=""
                cells[r][c]=None
            rows[r0][c0]="\n".join(" ".join(t.text for t in line) for line in text_lines(group))
            cells[r0][c0]=(xs[c0],ys[r0],xs[c1+1],ys[r1+1])
            spans.append([r0,c0,r1-r0+1,c1-c0+1])
    return {"rows":rows,"cell_bboxes":cells,"spans":spans,
            "inferred_columns":inferred_columns,"tokens":tokens,"bbox":(xs[0],ys[0],xs[-1],ys[-1])}


def recover_page_tables(image: Image.Image, page: int, blocks: list[DocumentBlock],
                        regions, figure_boxes, native_words=()) -> None:
    """Mutate only accepted regions; all failures preserve original source blocks."""
    if os.environ.get("AXTRACT_RASTER_TABLES", "1") == "0" or deadline.remaining() < 8:
        return
    horizontal, vertical = line_masks(image)
    width,height=image.size
    scanned = any(b.page==page and b.metadata.get("route") in ("scanned","mixed-ocr") for b in blocks)
    candidates=ruled_regions(horizontal,vertical)
    # Borderless proposals need independent high-confidence layout evidence.
    for region in regions:
        if region.cls=="table" and region.score>=.9:
            bb=tuple(int(v*s) for v,s in zip(region.bbox,(width,height,width,height)))
            bb=(max(0,bb[0]),max(0,bb[1]),min(width,bb[2]),min(height,bb[3]))
            if not any(_intersection(bb,b)/max(1,_area(bb))>.5 for b in candidates):
                candidates.append(bb)
    ocr_calls=0
    for box in candidates[:MAX_CANDIDATES]:
        if deadline.remaining()<8:
            break
        normalized=tuple(v/s for v,s in zip(box,(width,height,width,height)))
        if not scanned and not any(_intersection(normalized,b)/max(_area(normalized),1e-9)>.9 for b in figure_boxes):
            continue
        if any(b.page==page and b.bbox and b.type in (BlockType.TABLE,BlockType.CHART,BlockType.EQUATION)
               and _intersection(normalized,b.bbox)/max(_area(normalized),1e-9)>.2 for b in blocks):
            continue
        selected=[b for b in blocks if b.page==page and b.bbox and b.type==BlockType.PARAGRAPH
                  and _intersection(normalized,b.bbox)/max(_area(b.bbox),1e-9)>.98]
        crossing=[b for b in blocks if b.page==page and b.bbox and b.type in (BlockType.PARAGRAPH,BlockType.LIST)
                  and b not in selected and _intersection(normalized,b.bbox)/max(_area(b.bbox),1e-9)>.15]
        if crossing:
            continue  # Do not delete a paragraph that straddles a table boundary.
        tokens=[Token(tuple(v*s for v,s in zip(b.bbox,(width,height,width,height))),b.content,b.id,b.confidence)
                for b in selected if b.extractor=="rapidocr"]
        # Native word boxes are more trustworthy than OCR wherever present.
        native=[Token(tuple(w[:4]),str(w[4]),f"native-word-{n}") for n,w in enumerate(native_words)
                if _intersection(box,tuple(w[:4]))/max(_area(tuple(w[:4])),1e-9)>.98]
        if native:
            tokens=native
        elif len(tokens)<6 and ocr_calls<6:
            from extractors.ocr_extractor import OCRExtractor
            crop=image.crop(box)
            try:
                found,errors=OCRExtractor().ocr_pil_image(crop,page)
            except Exception:
                continue
            finally:
                crop.close()
            ocr_calls+=1
            if errors:
                continue
            x0,y0,x1,y1=box
            tokens=[Token((x0+b.bbox[0]*(x1-x0),y0+b.bbox[1]*(y1-y0),
                           x0+b.bbox[2]*(x1-x0),y0+b.bbox[3]*(y1-y0)),b.content,
                          f"p{page}-raster{box[0]}-{box[1]}-{n}",b.confidence) for n,b in enumerate(found) if b.bbox]
        layout=any(r.cls=="table" and r.score>=.9 and _intersection(normalized,r.bbox)/max(_area(normalized),1e-9)>.7 for r in regions)
        grid=reconstruct(tokens,box,horizontal,vertical,layout_evidence=layout)
        if grid is None:
            continue
        bbox=tuple(v/s for v,s in zip(grid['bbox'],(width,height,width,height)))
        confidences=[t.confidence for t in grid['tokens'] if t.confidence is not None]
        ident=f"p{page}-raster-table-{box[0]}-{box[1]}"
        table=DocumentBlock(id=ident,type=BlockType.TABLE,content="\n".join(" | ".join(r) for r in grid['rows']),
            page=page,bbox=bbox,extractor="native_raster_grid" if native else "rapidocr_raster_grid",
            confidence=min(confidences) if confidences else None,
            requires_review=grid['inferred_columns'] or any(c < .6 for c in confidences),
            metadata={"rows":grid['rows'],"row_count":len(grid['rows']),"col_count":len(grid['rows'][0]),
                "header":grid['rows'][0],
                "cell_bboxes":[[list(v/s for v,s in zip(c,(width,height,width,height))) if c else None
                                for c in row] for row in grid['cell_bboxes']],
                "source":"ruling_lines_and_source_boxes","route":"scanned" if scanned else "digital",
                "merged_cells":{"source":"extractor","merged_regions":grid['spans']},
                "evidence_flags":["persistent_gutters" if grid['inferred_columns'] else "ruled_cells"],
                "source_blocks":[b.model_dump(mode="json") for b in selected],
                "source_tokens":[{"text":t.text,"bbox":[v/s for v,s in zip(t.box,(width,height,width,height))],
                                  "source_id":t.source_id,"confidence":t.confidence} for t in grid['tokens']]})
        blocks[:]=[b for b in blocks if b not in selected]
        blocks.append(table)


def _area(box):
    return max(0,box[2]-box[0])*max(0,box[3]-box[1])


def _intersection(a,b):
    return _area((max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])))
