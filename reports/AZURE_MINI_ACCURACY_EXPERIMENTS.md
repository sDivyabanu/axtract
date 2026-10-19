# Mini-only accuracy experiments — 2026-10-19

All API calls used gpt-5.4-mini. No stronger model was used. The fixed diagnostic is not the full 1,403-page corpus.

## Retained change
Table-only normalization removes LaTeX wrappers around superscript footnote stars. It preserves prose and actual mathematical exponents. Rescoring the saved complete 70-page output improved macro accuracy from 65.92% to 66.16% (302/430 assertions, tables 36/60). No new API calls were needed for this comparison.

## Experiments, not production defaults
AXTRACT_AZURE_PROMPT=layout adds explicit layout, historical transcription, header/footer, and rectangular-table instructions. AXTRACT_AZURE_REASONING accepts default, none, low, medium, high. Prior legacy/default behavior remains selected unless explicitly overridden. These controls are isolated to Azure extraction.

1. Layout prompt, original detail, reasoning none, crops off, rereads off: $0.622962 for 56 attempted pages; 55 succeeded. The $0.70 run allowance prevented dispatching the remaining 14. The 52.69% all-70 score counts missing pages as failures and is not a complete-run model comparison. On the identical 56-page subset, baseline macro was 68.80%, experiment 68.11%. Headers and tables improved but math regressed.
2. Layout prompt, high detail, low reasoning, crops off, rereads off, output allowance 8192: 14-page pilot, 13 outputs, 61.69% macro, $0.108789. This does not establish full-corpus accuracy or production cost.

Additional measured/reserved API spend for these experiments: $0.731751. Pricing estimates are not invoices. Lower image resolution and changed reasoning were tested together; these experiments cannot isolate their individual causal effects.

## Validation
99 focused tests passed. All eleven protected LightOnOCR/shared files match the saved before hashes. No local OCR implementation or benchmark assertions were changed. No changes committed.

## Limitations and next research
80% has not been achieved. The complete retained result is 66.16%. Do not select outputs by benchmark answers or hardcode document-specific exclusions. A future region-based transcription or structured table extraction experiment needs independent held-out evaluation and measured budget trade-offs. Neither has been established to reach 80% with mini. A full-corpus run is deferred because current diagnostics do not meet the accuracy and cost targets.
