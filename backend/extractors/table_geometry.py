"""Conservative native table geometry; no OCR or model initialization."""
from __future__ import annotations


def _covered(intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    end = float("-inf")
    for lo, hi in sorted(intervals):
        total += max(0.0, hi - max(lo, end))
        end = max(end, hi)
    return total


def has_ruled_grid(drawings: list[dict], bbox: tuple) -> bool:
    """Recognize joined thin rules, including zero-area stroked segments.

    Shaded cell rectangles are not chart bars when repeated horizontal AND
    vertical rules span the region. Axes alone do not constitute a grid.
    """
    x0, y0, x1, y1 = bbox
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return False
    horizontal: dict[int, list[tuple[float, float]]] = {}
    vertical: dict[int, list[tuple[float, float]]] = {}
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None:
            continue
        a, b, c, d = rect
        if c < x0 - 1 or a > x1 + 1 or d < y0 - 1 or b > y1 + 1:
            continue
        # The path rectangle may have zero height/width for a stroked line.
        if 0 <= d - b <= 3.2 and c > a:
            horizontal.setdefault(round((b + d) / 2), []).append((max(a, x0), min(c, x1)))
        if 0 <= c - a <= 3.2 and d > b:
            vertical.setdefault(round((a + c) / 2), []).append((max(b, y0), min(d, y1)))
    horizontal_count = sum(_covered(parts) >= width * 0.8 for parts in horizontal.values())
    vertical_count = sum(_covered(parts) >= height * 0.8 for parts in vertical.values())
    return horizontal_count >= 2 and vertical_count >= 2



def physical_rows(words: list[tuple]) -> list[list[tuple]]:
    """Join cells by baseline geometry, independent of PDF block/line IDs."""
    from statistics import median
    if not words:
        return []
    height = median(max(1.0, w[3] - w[1]) for w in words)
    rows: list[list[tuple]] = []
    centers: list[float] = []
    for word in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        center = (word[1] + word[3]) / 2
        if rows and abs(center - centers[-1]) <= height * 0.35:
            rows[-1].append(word)
            centers[-1] = sum((w[1] + w[3]) / 2 for w in rows[-1]) / len(rows[-1])
        else:
            rows.append([word])
            centers.append(center)
    return [sorted(row, key=lambda w: w[0]) for row in rows]


def numeric_table_candidates(words: list[tuple], drawings: list[dict] | None = None) -> list[dict]:
    """Recover repeated label + numeric-column grids without ruling lines.

    Require three nearby rows with labels and two recurring right-aligned
    numeric positions separated by real gutters. Every source word in each
    chosen physical row is retained; empty columns are never filled by guessing.
    """
    import re
    from statistics import median
    numeric = re.compile(r"^[+\-\u2212]?(?:[\u00a3$\u20ac])?\(?\d[\d,.]*(?:%|\))?$")
    rows = physical_rows(words)
    if len(rows) < 3:
        return []
    height = median(max(1.0, w[3] - w[1]) for w in words)
    tolerance = max(2.0, height * 0.35)
    points = sorted((w[2], i, w) for i, row in enumerate(rows) for w in row if numeric.fullmatch(str(w[4])))
    clusters: list[list[tuple]] = []
    for point in points:
        if clusters and point[0] - clusters[-1][0][0] <= tolerance * 2:
            clusters[-1].append(point)
        else:
            clusters.append([point])
    clusters = [cluster for cluster in clusters if len({p[1] for p in cluster}) >= 3]
    anchors = [median(p[0] for p in cluster) for cluster in clusters]
    if not 2 <= len(anchors) <= 24:
        return []

    qualified = []
    for i, row in enumerate(rows):
        hits = {}
        for w in row:
            if numeric.fullmatch(str(w[4])):
                col = min(range(len(anchors)), key=lambda j: abs(w[2] - anchors[j]))
                if abs(w[2] - anchors[col]) <= tolerance:
                    hits[col] = w
        if len(hits) < 2:
            continue
        first = min(w[0] for w in hits.values())
        labels = [w for w in row if w[2] < first - height * 0.8]
        # A neighbouring prose column can share the same physical baseline.
        # Only the contiguous label chunk immediately before the numeric grid
        # belongs to this table, not every word to its left.
        for j in range(len(labels)-1, 0, -1):
            if labels[j][0] - labels[j-1][2] > max(12.0, height * 1.2):
                labels = labels[j:]
                break
        if labels and any(re.search(r"[^\W\d_]", str(w[4])) for w in labels):
            # Keep prose with numbers embedded in sentences out of the table path.
            if len(labels) <= 20 and not str(labels[-1][4]).endswith((".", ";")):
                qualified.append((i, hits, labels))
    runs: list[list[tuple]] = []
    for record in qualified:
        i = record[0]
        if (runs and i - runs[-1][-1][0] <= 3
                and rows[i][0][1] - rows[runs[-1][-1][0]][0][1] <= height * 3.5):
            runs[-1].append(record)
        else:
            runs.append([record])
    candidates = []
    for run in runs:
        if len(run) < 3:
            continue
        columns = sorted({c for _, hits, _ in run for c in hits
                          if sum(c in h for _, h, _ in run) >= 3})
        if len(columns) < 2:
            continue
        numeric_words = [[hits[c] for _, hits, _ in run if c in hits] for c in columns]
        # Columns must have open gutters, not overlapping numeric text.
        lefts = [min(w[0] for w in col) for col in numeric_words]
        rights = [max(w[2] for w in col) for col in numeric_words]
        if any(lefts[i+1] - rights[i] < height * 0.8 for i in range(len(columns)-1)):
            continue
        label_right = max(w[2] for _, _, labels in run for w in labels)
        if lefts[0] - label_right < height * 0.8:
            continue
        boundaries = [(label_right + lefts[0]) / 2]
        boundaries += [(rights[i] + lefts[i+1]) / 2 for i in range(len(columns)-1)]
        start, end = run[0][0], run[-1][0]
        x_left = min(w[0] for _, _, labels in run for w in labels)
        x_right = max(rights) + height
        def in_columns(row):
            return [w for w in row if x_left - 1 <= (w[0]+w[2])/2 <= x_right]
        # Include an adjacent short, multi-column header, never distant prose.
        if start > 0:
            prior = in_columns(rows[start-1])
            assignments = {sum((w[0]+w[2])/2 > edge for edge in boundaries) for w in prior}
            gap = rows[start][0][1] - prior[0][1] if prior else float("inf")
            # A thin, full-width source separator positively connects a header
            # to the numeric grid even when its padding exceeds two text heights.
            separators = []
            for drawing in drawings or []:
                rect = drawing.get("rect")
                if rect is None or not prior:
                    continue
                a,b,c,d = rect
                if (0 <= d-b <= 3.2 and max(w[3] for w in prior)-1 <= (b+d)/2 <= min(w[1] for w in rows[start])+1):
                    separators.append((max(a,x_left),min(c,x_right)))
            ruled_header = (_covered(separators) >= (max(rights)-x_left)*.8)
            if (prior and len(assignments) >= 2 and len(prior) <= 18
                    and (gap <= height*2 or (gap <= height*3 and ruled_header))):
                start -= 1
        selected = [in_columns(row) for row in rows[start:end+1]]
        selected = [row for row in selected if row]
        bbox = (min(w[0] for row in selected for w in row), min(w[1] for row in selected for w in row),
                max(w[2] for row in selected for w in row), max(w[3] for row in selected for w in row))
        selected_ids = {id(w) for row in selected for w in row}
        if any(id(w) not in selected_ids and w[0]>=bbox[0]-1 and w[2]<=bbox[2]+1
               and w[1]>=bbox[1]-1 and w[3]<=bbox[3]+1 for w in words):
            continue  # Never mask a source word not represented in the grid.
        grid, cell_boxes = [], []
        for row in selected:
            cells = [[] for _ in range(len(columns)+1)]
            for word in row:
                col = sum((word[0]+word[2])/2 > edge for edge in boundaries)
                cells[col].append(word)
            grid.append([" ".join(str(w[4]) for w in cell) for cell in cells])
            cell_boxes.append([(min(w[0] for w in cell), min(w[1] for w in cell), max(w[2] for w in cell), max(w[3] for w in cell)) if cell else None for cell in cells])
        candidates.append({"rows": grid, "bbox": bbox, "cell_bboxes": cell_boxes})
    return candidates



def ruled_text_candidates(words: list[tuple], drawings: list[dict]) -> list[dict]:
    """Use repeated segmented horizontal borders as explicit column evidence.

    Some PDFs draw each cell's top/bottom border but omit all vertical lines.
    Three matching sets of breakpoints delimit the table and its columns.
    Native block overlap supplies logical rows, retaining wrapped cell lines.
    """
    from collections import defaultdict
    from statistics import median
    if not words:
        return []
    height = median(max(1.0, w[3]-w[1]) for w in words)
    rules = defaultdict(list)
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None:
            continue
        a,b,c,d = rect
        if 0 <= d-b <= 3.2 and c-a >= height*2:
            rules[round((b+d)/2)].append((a,c))
    patterns: list[dict] = []
    for y, pieces in sorted(rules.items()):
        pieces = sorted(set(pieces))
        if len(pieces) < 2 or any(abs(pieces[i+1][0]-pieces[i][1]) > 3 for i in range(len(pieces)-1)):
            continue
        edges = [pieces[0][0]] + [(pieces[i][1]+pieces[i+1][0])/2 for i in range(len(pieces)-1)] + [pieces[-1][1]]
        if any(edges[i+1]-edges[i] < height*2 for i in range(len(edges)-1)):
            continue
        pattern = next((p for p in patterns if len(p["edges"])==len(edges)
                        and all(abs(a-b)<=3 for a,b in zip(p["edges"],edges))), None)
        if pattern is None:
            patterns.append({"edges":edges,"ys":[y]})
        else:
            pattern["ys"].append(y)
    candidates=[]
    for pattern in patterns:
        edges, ys = pattern["edges"], pattern["ys"]
        if len(ys)<3 or ys[-1]-ys[0]<height*4 or ys[1]-ys[0]>height*4:
            continue
        region=(edges[0],ys[0]-1,edges[-1],ys[-1]+1)
        inside=[w for w in words if w[0]>=region[0]-1 and w[2]<=region[2]+1 and w[1]>=region[1]-1 and w[3]<=region[3]+1]
        by_block=defaultdict(list)
        for w in inside:
            by_block[w[5]].append(w)
        chunks=sorted(by_block.values(),key=lambda ws:min(w[1] for w in ws))
        # A native block spanning many rows could be prose or an unresolved grid.
        if not chunks or any(max(w[3] for w in ws)-min(w[1] for w in ws)>height*4 for ws in chunks):
            continue
        logical=[]
        for chunk in chunks:
            lo=min(w[1] for w in chunk)
            if logical and lo < max(w[3] for w in logical[-1])-height*0.2:
                logical[-1].extend(chunk)
            else:
                logical.append(list(chunk))
        if len(logical)<4:
            continue
        grid,boxes=[],[]
        for group in logical:
            cells=[[] for _ in range(len(edges)-1)]
            for row in physical_rows(group):
                for w in row:
                    col=min(len(cells)-1,sum((w[0]+w[2])/2>e for e in edges[1:-1]))
                    cells[col].append(w)
            grid.append([" ".join(str(w[4]) for w in cell) for cell in cells])
            boxes.append([(min(w[0] for w in cell),min(w[1] for w in cell),max(w[2] for w in cell),max(w[3] for w in cell)) if cell else None for cell in cells])
        if sum(sum(bool(cell) for cell in row)>=2 for row in grid)<3:
            continue
        candidates.append({"rows":grid,"bbox":region,"cell_bboxes":boxes,"source":"segmented_horizontal_rules"})
    return candidates



def is_detached_margin_header(raw: dict, blocks: list[dict], width: float, height: float, body_font: float) -> bool:
    """Require extreme-margin, typographic AND multi-column layout evidence.

    Never classify bottom footnotes, narrow body citations, large/bold titles or
    text contiguous with the body this way. The source text remains structured.
    """
    if width <= 0 or height <= 0 or body_font <= 0 or raw.get("math_font") or raw.get("text_layer_math"):
        return False
    x0,y0,x1,y1 = raw["bbox"]
    if (y0 < 0 or y1 > height*0.035 or x1-x0 < width*0.6
            or raw.get("is_bold") or raw.get("font_size",0)>body_font*1.1
            or not 3 <= len(raw["content"].split()) <= 60):
        return False
    separation = max(height*0.025, body_font*2)
    if any(b is not raw and y1 <= b["bbox"][1] < y1+separation for b in blocks):
        return False
    body=[b for b in blocks if b is not raw and b["bbox"][1]>=height*0.07
          and b["bbox"][3]<=height*0.93]
    if not body or min(b["bbox"][1] for b in body)-y1 < max(height*0.025,body_font*2):
        return False
    if sum(len(b["content"].split()) for b in body)<80:
        return False
    narrow=[b for b in body if b["bbox"][2]-b["bbox"][0]<=width*0.55]
    left=[b for b in narrow if b["bbox"][0]<width*0.3]
    right=[b for b in narrow if b["bbox"][0]>width*0.5]
    return len(left)>=2 and len(right)>=2



def ruled_grid_regions(drawings: list[dict]) -> list[tuple]:
    """Find source-proven grids without requiring digits in the text layer."""
    from collections import defaultdict
    horizontal=defaultdict(list)
    for drawing in drawings:
        rect=drawing.get("rect")
        if rect is None:
            continue
        a,b,c,d=rect
        if 0<=d-b<=3.2 and c-a>=20:
            horizontal[round((b+d)/2)].append((a,c))
    patterns=[]
    for y, pieces in sorted(horizontal.items()):
        runs=[]
        for lo,hi in sorted(pieces):
            if runs and lo<=runs[-1][1]+3:
                runs[-1]=(runs[-1][0],max(runs[-1][1],hi))
            else:
                runs.append((lo,hi))
        for lo,hi in runs:
            pattern=next((p for p in patterns if abs(lo-p[0])<=3 and abs(hi-p[1])<=3),None)
            if pattern is None:
                patterns.append([lo,hi,[y]])
            else:
                pattern[2].append(y)
    regions=[]
    for lo,hi,ys in patterns:
        if len(ys)>=3 and max(ys)-min(ys)>=20:
            region=(lo,min(ys),hi,max(ys))
            if has_ruled_grid(drawings,region):
                regions.append(region)
    return regions



def has_plot_strokes(drawings: list[dict], bbox: tuple) -> bool:
    """A multi-segment data curve is chart evidence even on a ruled background."""
    x0,y0,x1,y1=bbox
    for drawing in drawings:
        points=[]
        segments=0
        for item in drawing.get("items", []):
            if item[0]=="l":
                a,b=item[1:3]
                if abs(a[0]-b[0])>=3 and abs(a[1]-b[1])>=3:
                    segments+=1;points.extend((a,b))
            elif item[0]=="c":
                segments+=1;points.extend(item[1:])
        if segments<3 or not points:
            continue
        a,b=min(p[0] for p in points),min(p[1] for p in points)
        c,d=max(p[0] for p in points),max(p[1] for p in points)
        if (a>=x0-1 and c<=x1+1 and b>=y0-1 and d<=y1+1
                and c-a>=(x1-x0)*0.3 and d-b>=(y1-y0)*0.2):
            return True
    return False



def split_stacked_rows(rows: list[list], boxes: list[list], words: list[tuple]):
    """Split a native row only when >=3 labelled data baselines agree across cells.

    Wrapping in one cell is not row evidence. Require two numeric columns plus
    a nonnumeric label column with identical baseline counts/positions and exact
    conservation of every existing cell's source text. Merged cells are excluded.
    """
    import re
    from statistics import median
    numeric = re.compile(r"^[+\-\u2212]?(?:[\u00a3$\u20ac])?\(?\d[\d,.]*(?:%|\))?$")
    output, geometry, repairs = [], [], []
    for index, cells in enumerate(rows):
        cell_boxes = boxes[index] if index < len(boxes) else []
        original = lambda: (output.append(cells), geometry.append(cell_boxes))
        if len(cell_boxes) != len(cells) or any(b is None for b in cell_boxes):
            original(); continue
        lines = []
        for text, box in zip(cells, cell_boxes):
            inside = [w for w in words if w[0]>=box[0]-1 and w[2]<=box[2]+1
                      and w[1]>=box[1]-1 and w[3]<=box[3]+1]
            groups = physical_rows(inside)
            # Require source and existing cells to agree before changing structure.
            value = " ".join(str(w[4]) for group in groups for w in group)
            if " ".join(str(text or "").split()) != " ".join(value.split()):
                break
            lines.append(groups)
        if len(lines) != len(cells):
            original(); continue
        counts = [len(group) for group in lines if group]
        if not counts or max(counts)<3 or len(set(counts))!=1:
            original(); continue
        count = counts[0]
        numeric_cols = [i for i,groups in enumerate(lines) if groups and all(
            numeric.fullmatch(" ".join(str(w[4]) for w in line)) for line in groups)]
        label_cols = [i for i,groups in enumerate(lines) if groups and i not in numeric_cols
                      and all(any(re.search(r"[^\W\d_]", str(w[4])) for w in line) for line in groups)]
        if len(numeric_cols)<2 or not label_cols:
            original(); continue
        centers = [[median((w[1]+w[3])/2 for w in line) for line in groups] for groups in lines if groups]
        height = median(w[3]-w[1] for groups in lines for line in groups for w in line)
        if any(max(c[r] for c in centers)-min(c[r] for c in centers)>height*.3 for r in range(count)):
            original(); continue
        ys = [median(c[r] for c in centers) for r in range(count)]
        cuts = [min(b[1] for b in cell_boxes)] + [(a+b)/2 for a,b in zip(ys,ys[1:])] + [max(b[3] for b in cell_boxes)]
        for r in range(count):
            output.append([" ".join(str(w[4]) for w in groups[r]) if groups else "" for groups in lines])
            geometry.append([(b[0],cuts[r],b[2],cuts[r+1]) for b in cell_boxes])
        repairs.append({"source_row":index,"row_count":count,"evidence":"aligned_label_and_numeric_baselines"})
    return output, geometry, repairs



def native_line_numeric_candidates(words: list[tuple]) -> list[dict]:
    """Find local numeric grids without merging neighbouring prose baselines.

    Native PDF lines preserve number/percentage pairs as one cell. Three rows
    must repeat >=2 numeric columns and a short aligned label. Only completely
    represented source rectangles are accepted. Nearby native header blocks are
    combined per column, preserving wrapped labels without guessing spans.
    """
    import re
    from collections import defaultdict
    from statistics import median
    by_line, by_block = defaultdict(list), defaultdict(list)
    for w in words:
        by_line[(w[5],w[6])].append(w)
        by_block[w[5]].append(w)
    def box(ws):
        return (min(w[0] for w in ws),min(w[1] for w in ws),max(w[2] for w in ws),max(w[3] for w in ws))
    def text(ws): return " ".join(str(w[4]) for w in sorted(ws,key=lambda w:w[0]))
    lines = [sorted(ws,key=lambda w:w[0]) for ws in by_line.values()]
    numeric = re.compile(r"(?=.*\d)^[\d\s,.()+\-\u2212%/$\u00a3\u20ac]+$")
    digits = [ws for ws in lines if numeric.fullmatch(text(ws))]
    if len(digits)<6:
        return []
    height = median(w[3]-w[1] for ws in digits for w in ws)
    if height <= 0:
        return []
    cells = [(*box(ws),text(ws),i,0,0) for i,ws in enumerate(digits)]
    records=[]
    labels=[ws for ws in lines if len(ws)<=12 and re.search(r"[^\W\d_]",text(ws))
            and not text(ws).endswith((".",";")) and not re.search(r"[=<>$\\]",text(ws))]
    for row in physical_rows(cells):
        if len(row)<2:
            continue
        y=median((w[1]+w[3])/2 for w in row)
        candidates=[ws for ws in labels if box(ws)[2]<row[0][0]-height*.6
                    and abs((box(ws)[1]+box(ws)[3])/2-y)<height*.35
                    and .65*height<=median(w[3]-w[1] for w in ws)<=height*1.35]
        if not candidates:
            continue
        label=max(candidates,key=lambda ws:box(ws)[2])
        if row[0][0]-box(label)[2]>height*12:
            continue
        records.append({"cells":row,"label":label,"y":y})
    runs=[]
    for record in records:
        prior=runs[-1][-1] if runs else None
        if (prior and len(prior["cells"])==len(record["cells"])
                and record["y"]-prior["y"]<=height*3
                and abs(box(prior["label"])[0]-box(record["label"])[0])<=height*1.5
                and all(abs(a[0]-b[0])<=height*.85 for a,b in zip(prior["cells"],record["cells"]))):
            runs[-1].append(record)
        else:
            runs.append([record])
    result=[]
    for run in runs:
        if len(run)<3:
            continue
        ncols=len(run[0]["cells"])
        if not 2<=ncols<=16:
            continue
        groups=[[r["label"]]+[digits[cell[5]] for cell in r["cells"]] for r in run]
        lefts=[min(box(row[c])[0] for row in groups) for c in range(ncols+1)]
        rights=[max(box(row[c])[2] for row in groups) for c in range(ncols+1)]
        if any(lefts[c+1]-rights[c]<height*.6 for c in range(ncols)):
            continue
        edges=[(rights[c]+lefts[c+1])/2 for c in range(ncols)]
        first_y=min(w[1] for cell in groups[0] for w in cell)
        headers=[]
        for ws in by_block.values():
            b=box(ws)
            if (b[3]<=first_y+1 and first_y-b[3]<=height*2
                    and b[3]-b[1]<=height*4 and b[0]>=lefts[0]-height*.25
                    and b[2]<=rights[-1]+height*6):
                assignments={sum((w[0]+w[2])/2>e for e in edges) for w in ws}
                if len(assignments)>=2 and any(re.search(r"[^\W\d_]",str(w[4])) for w in ws):
                    headers.append(ws)
        if headers:
            header=max(headers,key=lambda ws:box(ws)[3])
            columns=[[] for _ in range(ncols+1)]
            for line in physical_rows(header):
                for w in line:
                    columns[sum((w[0]+w[2])/2>e for e in edges)].append(w)
            groups.insert(0,columns)
        selected=[w for row in groups for cell in row for w in cell]
        region=box(selected)
        selected_ids={id(w) for w in selected}
        if any(id(w) not in selected_ids and w[0]>=region[0]-1 and w[2]<=region[2]+1
               and w[1]>=region[1]-1 and w[3]<=region[3]+1 for w in words):
            continue
        grid=[[" ".join(str(w[4]) for line in physical_rows(cell) for w in line) for cell in row] for row in groups]
        result.append({"bbox":region,"rows":grid,"cell_bboxes":[[box(cell) if cell else None for cell in row] for row in groups],
                       "source":"native_line_numeric_geometry"})
    return result



def shadows_complete_grid(candidate: dict, existing: list[dict]) -> bool:
    """Do not let a local numeric fragment steal rows/columns from a larger grid.

    A smaller rectangle can look source-complete while omitting adjacent cells.
    Preserve an already discovered wider grid, or a same-left grid with more
    rows. A locally isolated label column may still replace contaminated prose.
    """
    a=candidate["bbox"]
    for prior in existing:
        b=prior["bbox"]
        if min(a[2],b[2])<=max(a[0],b[0]) or min(a[3],b[3])<=max(a[1],b[1]):
            continue
        if len(prior["rows"][0])>len(candidate["rows"][0]):
            return True
        row_height=(a[3]-a[1])/max(1,len(candidate["rows"]))
        if abs(a[0]-b[0])<=row_height and len(prior["rows"])>len(candidate["rows"]):
            return True
    return False
