# RAG Chatbot v2 — Update Prompts

## 0. Ground rules for every prompt below
- **UI/frontend theme must not change.** Every prompt explicitly instructs the agent to reuse existing components, styles, and layout patterns already in the codebase — extend, don't redesign. If a new UI element is genuinely needed (e.g. a file-type icon, a comparison view), it must be built using the same design tokens/components already used elsewhere in the app, not a new visual style.
- **Ship in this order**: CSV/XLSX support → structured extraction pipeline → multi-document mode → (optional, lower priority) DOCX/URL/scanned-image support. Don't jump ahead — later phases depend on the structured-extraction foundation from Phase 2.
- Test each phase against real, slightly messy files before moving to the next — not just clean, ideal-case files.

---

## PHASE 1 — CSV / XLSX Support

**Goal**: Accept spreadsheet files as a first-class input, feeding directly into structured data (no OCR/text-parsing needed since the data is already tabular).

**Prompt:**
```
I'm upgrading my existing RAG chatbot (currently PDF-only) to also accept
CSV and XLSX files. IMPORTANT: do not change any existing UI components,
layout, colors, or styling — reuse the existing file upload component,
chat interface, and design system exactly as they are. Only extend the
backend logic and, where absolutely necessary, add minimal UI elements
(like a file-type badge) using the same existing design tokens/components
already used elsewhere in the app.

1. Extend the file upload handler to accept .csv and .xlsx in addition to
   .pdf, using the same upload UI component already in place — just widen
   the accepted file types, don't redesign the upload area.

2. Build a new ingestion path for spreadsheet files:
   - For CSV: parse directly into a structured table object (rows, columns,
     inferred column types e.g. numeric/text/date)
   - For XLSX: use a library like openpyxl or pandas' read_excel, handle
     multiple sheets by treating each sheet as a separate named table
   - Store this structured table data separately from the existing
     text-chunk-based vector store used for PDFs — spreadsheets should NOT
     go through the same text-chunking/embedding pipeline, since that would
     destroy the tabular structure

3. Update the chat/query logic to detect when a question is about
   structured tabular data (e.g. "what's the average of column X",
   "show me rows where Y > 100") versus general document Q&A, and route
   to a direct pandas-based computation against the stored table rather
   than an LLM-guessed answer. Use the LLM only to translate the natural
   language question into the correct pandas operation, then execute that
   operation for the actual numeric answer — never let the LLM state a
   computed number without it being verified by real code execution.

4. Reuse the existing chat UI to display results — if the answer is a
   table, render it using whatever table/data-display component already
   exists in the app (or the simplest possible reuse of existing styling
   if no table component exists yet), not a new custom design.

Test against a real messy CSV/XLSX (missing values, mixed types in a
column, multiple sheets) before considering this phase done.
```

---

## PHASE 2 — Structured Data Extraction from PDFs (foundation for safe charting)

**Goal**: Extract tables/numeric data from PDFs into verified structured form, separate from prose, so charts are grounded in real extracted data rather than LLM-guessed numbers.

**Prompt:**
```
I'm adding a structured data extraction layer to my existing PDF RAG
pipeline, so that chart/visualization generation is grounded in verified
data rather than the LLM guessing numbers from prose. IMPORTANT: do not
change any existing UI, chat flow, or styling — this is a backend
extraction layer feeding into the existing chat interface as it already
works.

1. Build a new extraction step that runs when a PDF is uploaded (alongside
   the existing text-chunking pipeline, not replacing it):
   - Detect table-like structures in the PDF (using a library like
     camelot-py, pdfplumber, or tabula-py depending on what's already in
     my stack — pick whichever integrates most easily)
   - For each detected table, extract it into a structured object: column
     headers, rows, inferred units where present (e.g. "$", "%", "units")
   - Store these structured tables separately from the text-chunk vector
     store, tagged with which page/section they came from (needed for
     citation, which I already have working for text — extend the same
     citation mechanism to cover table-sourced data)

2. Update the chat logic: when a user asks a question that implies
   charting or a numeric claim (e.g. "show me revenue growth", "chart
   the trend"), first check if the relevant data exists in an extracted
   structured table. If yes, generate the chart/answer FROM that verified
   table data only. If the needed data is NOT found in any extracted
   table (only in prose), the response must say so explicitly — e.g.
   "I found this number mentioned in the text but couldn't verify it as
   structured data, treat with caution" — rather than silently charting
   an inferred number as if it were verified.

3. Add a simple internal flag per chart/numeric answer: "verified from
   extracted table" vs "inferred from text" — surface this distinction
   in the existing citation/confidence UI I already have, don't build a
   new UI pattern for it, just extend the existing indicator to cover
   this new case.

4. For chart rendering itself, use whatever charting library is already
   in my frontend (or if none, use a lightweight one like Chart.js or
   Recharts depending on my frontend framework) styled to match my
   existing color palette and typography — charts should look like they
   belong in my app, not like a generic default-styled chart.

Build a test set of 5 real PDFs with tables of increasing messiness
(clean simple table, table with merged cells, table split across two
pages, a financial report with footnote-adjusted numbers) and verify
extraction accuracy on each before considering this phase done. Do not
skip the messy cases — that's where extraction usually breaks silently.
```

---

## PHASE 3 — Multi-Document RAG (cross-document synthesis)

**Goal**: Let users load multiple related documents into one session and ask comparative/synthesis questions across all of them.

**Prompt:**
```
I'm extending my RAG chatbot to support multiple documents in a single
session, so users can ask comparative questions across them (e.g. "how
did revenue change between these two reports"). IMPORTANT: reuse my
existing upload UI, chat interface, and citation display exactly as they
are — do not redesign anything. The only new UI element allowed is a
minimal way to show which documents are currently loaded in the session
(e.g. a small list/chip row), styled using my existing design system.

1. Extend the session/upload logic to allow multiple files (PDF and/or
   CSV/XLSX from Phase 1) to be added to the same chat session, rather
   than the current one-file-per-session model. Each document's chunks/
   extracted tables should be tagged with a document identifier so
   retrieval can distinguish which source each piece of information
   came from.

2. Update the retrieval step: when a user asks a question, retrieve
   relevant chunks/tables across ALL loaded documents (not just one),
   and pass the LLM enough context to know which document each piece of
   retrieved information came from.

3. Update citations to include the document name/identifier alongside
   the existing page/section reference (e.g. "Q3-Report.pdf, page 4")
   so users can tell which source backs which part of a comparative
   answer, reusing my existing citation UI pattern.

4. For comparative numeric questions involving extracted tables from
   Phase 2 across multiple documents (e.g. comparing a metric across two
   reports), the underlying computation should merge/align the relevant
   structured tables and compute the comparison directly (pandas-based),
   not have the LLM eyeball two separate numbers and guess the
   difference.

5. Add a lightweight UI element (using existing design tokens) showing
   which documents are currently active in the session, with a way to
   remove one if needed — keep this minimal, consistent with existing
   patterns, not a new visual style.

Test with 2-3 real related documents (e.g. quarterly reports from
different periods) and verify comparative answers are correctly grounded
in the right source per document before considering this phase done.
```

---

## PHASE 4 (optional, lower priority) — Additional Format Support

**Goal**: Broaden input formats once Phases 1-3 are solid. Only do this if time remains — these add real value but are lower priority than the structured extraction and multi-doc work above.

**Prompt:**
```
I'm adding additional input format support to my RAG chatbot (which
currently handles PDF, CSV, and XLSX). IMPORTANT: reuse all existing UI
components and styling exactly as they are — this should feel like a
natural extension of the existing upload flow, not a new interface.

1. DOCX support: add a DOCX text extractor (e.g. python-docx) feeding
   into the same text-chunking pipeline already used for PDFs. Tables
   within DOCX files should go through the same structured-extraction
   path built in Phase 2, not just flat text extraction.

2. URL/web page support: add an option to paste a URL instead of
   uploading a file. Use a readability-focused extraction library (e.g.
   trafilatura) to pull clean article text, feeding into the same
   text-chunking pipeline as PDFs. Reuse the existing upload UI area,
   just add a URL input option alongside the file picker, styled
   consistently with the existing upload component.

3. Scanned document / image-based table support (most technically risky,
   do this last): add OCR (e.g. Tesseract or a cloud OCR API) as a
   fallback path when a PDF page contains no extractable text layer
   (i.e. it's a scanned image). Route OCR'd table-like content through
   the same Phase 2 structured-extraction logic where possible. Clearly
   flag OCR-derived data as lower-confidence than natively-extracted
   data in the existing confidence indicator UI, since OCR accuracy on
   messy scans is meaningfully lower than native text/table extraction.

Test each new format against at least 2 real files before considering
this phase done, and confirm OCR-derived answers are visibly flagged as
lower-confidence in the existing UI, not presented with equal certainty
to natively-extracted data.
```

---

## Definition of Done for v2
- [ ] CSV/XLSX upload works through the existing UI, computations verified via real code execution, not LLM-guessed
- [ ] PDF tables are extracted into structured form and tested against messy real-world cases, not just clean ones
- [ ] Charts/numeric answers are clearly flagged as "verified from extracted data" vs "inferred from text"
- [ ] Multi-document sessions work, with per-document citations reusing the existing citation UI
- [ ] No UI/frontend redesign anywhere — every addition reuses existing components, colors, and layout patterns
- [ ] (If Phase 4 attempted) new formats tested on real files, OCR-derived data visibly flagged as lower-confidence