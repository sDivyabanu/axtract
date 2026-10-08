# Axtract — What We Built Beyond the Brief

## Security (not in DQCL, our addition)

- Every document is scanned for hidden adversarial content before
  a single token reaches the downstream LLM
- White-on-white text, sub-1pt fonts, off-page text and text hidden
  under images are separated into a quarantine field — never in the
  main output
- Prompt injection phrases are detected with aggressive normalisation
  that catches leetspeak, spaced letters and mixed-case bypasses
- Zero-width characters and bidi override controls that can reverse
  displayed text are stripped and flagged
- Mixed Latin/Cyrillic/Greek words (homoglyphs) are flagged so entity
  matching cannot be fooled by a visually identical but different string
- PDF JavaScript, OpenAction, Launch and EmbeddedFiles are detected
  and reported — never executed
- Office VBA macros, DDE field instructions and OLE objects are
  detected and reported — never executed
- Excel financial cells with hardcoded values and no formula backing
  are flagged as manual override suspected — a red flag in diligence
- Remote template references in Office files that would silently fetch
  a URL on open are detected and blocked — zero network requests made
- All extracted text is HTML-escaped and dangerous URIs are neutralised
  before reaching any output
- Every security control has a synthetic finance-domain test file and
  an auto-scorer that prints PASS/FAIL with timing in one command

## Financial intelligence (beyond basic extraction)

- Financial numbers are parsed into actual floats — parenthesized
  negatives, thousands separators, currency symbols and percentages
  are all handled, not left as strings
- Tables split across page breaks are detected and stitched into one
  logical table with correct headers — not two broken half-tables
- Reading order is reconstructed across multi-column layouts so left
  and right columns never interleave

## Trustworthiness (beyond what parsers normally provide)

- Every extracted block carries its source page and normalised
  bounding box so any number can be traced back to its exact location
- Confidence is preserved from the real extractor signal and is never
  fabricated — deterministic extractors get null, not an invented score
- Ambiguous and low-confidence blocks are flagged requires_review
  instead of being hallucinated or silently dropped
- Hidden content is quarantined in its own field so the main body
  seen by the LLM is clean and the analyst can inspect what was hidden

## Production readiness

- One API endpoint handles all supported formats — no per-format
  tool selection required
- File type is verified from magic bytes, not the filename extension
- Every failure mode returns a structured error code within 60 seconds
  — no crashes, no hangs, no empty responses
- Output is validated against the Pydantic schema before returning —
  a broken internal result never reaches the caller as a malformed
  payload