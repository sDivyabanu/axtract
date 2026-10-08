# DealLens — 3-minute demo script (Project Falcon)

*"It doesn't just read the data room. It audits it — and every answer comes with a receipt."*

**Before you start (once):** `scripts/setup_rag.sh` (Ollama + weights), `backend/.venv/bin/python scripts/make_demo_dataroom.py`,
then start the servers (backend: `cd backend && .venv/bin/python -m uvicorn main:app --port 8000`; frontend: `cd frontend && npm run dev`)
and open **http://localhost:3000** (use `localhost`, not `127.0.0.1`). Optional warm-up: ask one question so the model is loaded.

| Time | Click / say | What the audience sees |
|---|---|---|
| **0:00** | **DealLens → create "Project Falcon" → drop the 6 files from `demo/project_falcon/`.** "A synthetic deal: audited accounts, a CIM, a debt schedule, a loan agreement, management accounts, board minutes." | Each file goes *queued → parsing → indexing → ready* (≈15 s). Badges: doc type, pages, **flags**, **quarantined** (red) on two files. |
| **0:25** | **Ask:** "What is the total debt maturing in 2026?" | Answer in **< 1 s**: *Debt maturing in 2026: ₹104.8 Cr* with the badge **"Computed by table engine · no LLM arithmetic"**, **Grounding 2/2**, and a green **Number Receipt** listing every row it added. |
| **0:50** | **Click "Term Loan A" in the receipt.** | The debt schedule opens on the right with a box on **that exact cell** (page F-7); scroll the receipt to a row on page F-8: the *same table continues on page 2* and the receipt followed it. |
| **1:10** | **Contradictions tab.** | "The CIM says revenue ₹480.0 Cr, the audited accounts ₹452.0 Cr: **6.2 % gap**." Click each side: both cells highlight. EBITDA 84.0 vs 69.0 (flagged as adjusted-vs-reported). |
| **1:30** | **Compare tab → "Trap: Hidden instruction".** | Left (naive pipeline): *"No, Falcon Industries does not have any debt"* — it obeyed white-on-white text in the board minutes; our checks underline the retrieved instruction in red. Right (DealLens): *"Yes … ₹385.6 crore"* plus **"⚠ 1 source excluded: instruction aimed at an AI (Falcon_Board_Minutes.pdf p.1)"**. |
| **1:55** | **"Trap: Missing schedule" → then "Trap: Unanswerable".** | DealLens: *Not found — Schedule 3 is referenced in the loan agreement (p.1) but is not in the data room* / *Not found in this data room* + what was searched. No guessing. |
| **2:10** | **Quarantine tab → "Show in document".** | Every hidden/injected item with its reason, the quoted text and a box on the invisible line; the hidden spreadsheet sheet is listed too. Nothing executed, nothing deleted. |
| **2:25** | **Seller Questions tab.** | 9 numbered, severity-ranked questions drafted automatically (contradictions, the typed total that doesn't add up, hardcoded EBITDA, Schedule 3, hidden sheet, hidden instruction…), each with clickable evidence. Edit one; **Export DOCX**. |
| **2:45** | **Eval tab.** | Baseline vs DealLens on 27 golden questions: accuracy, numeric exact-match, citation accuracy, abstention, injection resistance, latency — produced by `scripts/run_rag_eval.py`, never edited by hand. Point at the *Honest limitations* box. |
| **3:00** | *(if asked)* **Maturity Wall** and **Diligence Packs**. | Bars per year with receipts (2026 = ₹104.8 Cr); a Financials matrix with one cited value per document. |

**If something goes wrong**
- *"LLM offline – extractive mode"* badge (amber): the demo still works; answers are exact table values and quoted passages. Start Ollama (`ollama serve`) for generated prose.
- Slow first answer: the model loads on the first call (~5 s). Ask any question once beforehand.
- A preview image is missing: LibreOffice is only needed for DOCX/PPTX/XLSX previews; PDFs always render.
