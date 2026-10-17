# Hybrid table recovery

## Routing and source conservation

Native PDF extraction remains the first choice. Repeated adjacent closed cell
rectangles are discovery evidence even without numbers. A separate narrow
numeric-grid path requires at least five consecutive integer row labels, three
aligned numeric columns, a physical header rule and complete source ownership.
Wrapped headers are separated from captions by physical baseline gaps.

On pages already undergoing pixel routing, `raster_table_service` detects closed
ruled regions with repeated horizontal boundaries. A surrounding figure frame
is discarded as a candidate when it contains smaller independent grids.
Neither an axis pair nor a diagram of little boxes establishes a table.

Existing OCR boxes are reused. Native PDF word boxes take precedence wherever
available. Embedded image tables can request crop-only RapidOCR; there is no
new OCR/model dependency. A borderless proposal requires independent layout
evidence and persistent whitespace across repeated dense baselines. Aligned
equations, prose and newspaper columns are explicitly rejected. A single
condition in a financial header is not sufficient equation evidence; standalone
operators or a substantial concentration of mathematical tokens are required.

Real ruling segments establish cells and explicit rowspan/colspan metadata.
Persistent gutters may infer columns inside ruled bands, while dense repeated
body baselines may split tall bands. Wrapped headers remain in their original
bands. Missing OCR values remain empty; there is no dictionary of benchmark
answers or inferred text replacement.

Accepted blocks use the existing table schema and serializer. Metadata retains
normalized cell boxes, source-token boxes/IDs, real OCR confidences, exact
consumed source blocks and geometry evidence. Missing borders may establish a
rectangular merged cell; inconsistent nonrectangular spans are rejected. The
anchor cell's box covers the complete proven span, and covered positions are
distinct from genuinely empty cells. Low OCR confidence remains reviewable even
when the ruling geometry is unambiguous. The
existing serializer emits escaped HTML for real spans and literal cell pipes,
and Markdown for ordinary rectangular tables. Sanitizer code is unchanged.

Only fully owned source paragraphs are replaced. A paragraph straddling a
candidate makes that candidate ineligible. Existing tables, charts and equations
are protected from overlap. An encompassing figure is retained because it may
also contain graphs; surrounding prose and captions keep their own blocks.
Failed OCR/reconstruction leaves the original fallback intact.

## Bounds and defaults

- `AXTRACT_RASTER_TABLES=1` enables geometry recovery; `0` selects the old path.
- No rendering outside the existing 2,500-pixel side / 4.5-megapixel caps.
- At most 12 candidates and six crop OCR calls per page.
- Do not begin another recovery with fewer than eight seconds remaining.
- Existing render caching, OCR sessions, request deadlines and caller resource
  guards are reused. Experimental scanned math remains independently OFF.
- This is conservative recovery, not complete table recognition. Missing OCR
  glyphs, unruled complex headers and ambiguous cell merges can still fail.

## Research and model decision

The [official olmOCR scorer](https://github.com/allenai/olmocr/blob/main/olmocr/bench/tests.py)
checks cells and their relationships; dumping a table's text is insufficient.
The locally pinned scorer and assertions remain unchanged.

[img2table's algorithm documentation](https://github.com/xavctn/img2table/blob/main/docs/table-detection-algorithms.md)
describes complementary ruling-line and persistent-whitespace evidence. The
implementation uses these general techniques with AXTRACT's existing OpenCV
and OCR infrastructure; it does not import img2table.

[Microsoft TATR](https://github.com/microsoft/table-transformer) separates
detection and structure recognition and lists approximately 110 MB checkpoints.
Its code is MIT licensed. It is a PyTorch-based option, not an installed ONNX
table runtime in this application. Windows/Python 3.14 packaging and checkpoint
redistribution would require an isolated validation before adoption.

[PaddleOCR structure recognition](https://www.paddleocr.ai/v3.3.1/en/version3.x/module_usage/table_structure_recognition.html)
lists 6.9 MB SLANet/SLANet-plus and 351 MB SLANeXt models. Its published CPU
measurements use an Ubuntu/Paddle runtime; they do not establish this Windows
ONNX deployment's performance or compatibility. PaddleOCR code is Apache-2.0.
AXTRACT has Paddle-trained OCR through RapidOCR/ONNX, **not** an installed native
PaddleOCR/PaddlePaddle table runtime. No specialist checkpoint, package, GPU
stack or dataset was downloaded for this change.

## Validation

`backend/tests/test_hybrid_table_recovery.py` exercises production geometry,
source ownership, routing, failure fallback, actual renderer allocation guards,
native real-PDF recovery and serialization parsed by the unchanged official
table parser in its existing Python 3.12 environment. The application itself
continues using its Python 3.14 environment.

Frozen comparison evidence is saved under ignored `backend/data/measurements/`:
the baseline `stratified70_20261017T174537Z_01ff50` and fresh `table_candidate70_*`
directories. The exact original 70 PDF identities, 430 non-baseline assertions,
scorer, thresholds and scanned-math configuration are retained. Candidate source
hashes and patches identify precommit runs; postcommit equivalence checks bind
the measured implementation to its local Git commit. These diagnostic samples
must never be presented as a full-corpus official score.
