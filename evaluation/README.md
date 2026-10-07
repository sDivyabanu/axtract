# AXTRACT Evaluation Framework

## Directory Structure

```
evaluation/
  scripts/
    evaluate_text.py        Text extraction accuracy (similarity, coverage, WER, CER)
    evaluate_tables.py      Table extraction accuracy (cell correctness, structure)
    benchmark.py            Performance benchmarking (throughput, timing, failure rates)
  fixtures/                 Ground truth test files (add your own)
  multiformat/              Multi-format extraction evaluations
  ocr/                      OCR-specific evaluations
  tables/                   Table extraction evaluations
  charts_equations/         Chart and equation evaluations
  layout/                   Layout and reading order evaluations
```

## Metrics

### Text Extraction
- **Normalized text similarity**: Levenshtein-based similarity (0.0–1.0)
- **Content coverage**: fraction of expected words found in extraction
- **Word Error Rate (WER)**: word-level edit distance / reference length
- **Character Error Rate (CER)**: character-level edit distance / reference length

### OCR
- **CER and WER** as above, comparing OCR output to ground truth text
- **Confidence distribution**: real confidence scores from RapidOCR engine

### Tables
- **Cell-value correctness**: fraction of cells matching ground truth
- **Row/column structure match**: correct dimensions
- **Header detection accuracy**: first row identified correctly

### Charts & Equations
- **Chart type correctness**: correct classification of chart type
- **Equation LaTeX match**: normalized LaTeX comparison (requires ground truth)

### Layout & Reading Order
- **Block sequence accuracy**: extracted order vs expected reading order
- **Column detection**: correct identification of multi-column layouts

### Bounding Boxes
- **IoU (Intersection over Union)**: requires manually annotated ground truth

### Fail-safe
- **Ambiguous/unsupported correctly flagged**: requires_review rate on uncertain content

## Usage

### Text evaluation with ground truth
```bash
cd backend
# First, save a parse result:
curl -s -F "file=@document.pdf" http://localhost:8000/api/parse > result.json

# Run evaluation:
python ../evaluation/scripts/evaluate_text.py result.json ground_truth.txt
```

### Table evaluation
```bash
python ../evaluation/scripts/evaluate_tables.py result.json tables_ground_truth.json
```

### Performance benchmark
```bash
python ../evaluation/scripts/benchmark.py /path/to/test/files/
python ../evaluation/scripts/benchmark.py document.pdf --runs 5
python ../evaluation/scripts/benchmark.py /path/to/files/ --json > benchmark_results.json
```

## Ground Truth Format

### Text ground truth
Plain text file with expected document content.

### Table ground truth (JSON)
```json
{
  "tables": [
    {
      "rows": [["Header1", "Header2"], ["val1", "val2"]],
      "header": ["Header1", "Header2"]
    }
  ]
}
```

## Status

| Metric | Script | Status |
|--------|--------|--------|
| Text similarity | evaluate_text.py | Implemented, needs ground truth data |
| WER/CER | evaluate_text.py | Implemented, needs ground truth data |
| Table cell accuracy | evaluate_tables.py | Implemented, needs ground truth data |
| Throughput benchmark | benchmark.py | Implemented, ready to run |
| Chart evaluation | — | Requires ground truth + chart extraction |
| Equation LaTeX match | — | Requires ground truth + LaTeX extraction |
| Layout/reading order | — | Requires annotated ground truth |
| Bounding box IoU | — | Requires manually annotated coordinates |

No fabricated accuracy numbers are reported. All metrics require real ground-truth
data to produce meaningful results.
