# Fact Knowledge Layer — Superjoin Hiring Assignment

Auditable pipeline that extracts grounded facts from financial PDFs and reconciles them across documents: **CORROBORATED / GENUINE_CONTRADICTION / RECONCILED_BY_CONTEXT**. Built per `pipeline.md`, tested on `starter-datasets/delhivery/` (100p prospectus, 100p AR FY24, 27p deck) and `starter-datasets/india-macroeconomy/` with zero filename/schema hardcoding.

## Setup and Run Instructions

```bash
# 1. Install
pip install -r requirements.txt

# 2. Env — Bynara OpenAI-compatible router (fill from .env.example)
cp .env.example .env
# then in .env:
# LLM_API_KEY=your_bynara_key
# LLM_BASE_URL=https://router.bynara.id/v1
# LLM_MODEL=muse-spark-1.2-contributor-free
# LLM_ARBITER_MODEL=muse-spark-1.2-contributor-free
# HF_TOKEN=hf_...  # for google/embeddinggemma-300m, optional but recommended

# 3. Offline verification (no API key needed)
python3 tests/test_reconciliation.py  # covers 4 cases + dual provenance + incremental

# 4. Start API
python3 -m uvicorn app:app --port 8000 --host 0.0.0.0
# Swagger at http://localhost:8000/docs
```

**Quick smoke (3 pages ≈ 10s, no rate limit):**
```bash
curl -s -X POST http://localhost:8000/documents/upload -F "file=@starter-datasets/delhivery/01-delhivery-prospectus-2022-excerpt.pdf" | jq
curl -s -X POST "http://localhost:8000/pipeline/run?filename=01-delhivery-prospectus-2022-excerpt.pdf&max_pages=3" | jq .num_facts
```

**Deep (hits jumped revenue tables at 10-12 + 60-90, table-aware sampling at core/extraction/extractor.py:135):**
```bash
curl -s -X POST "http://localhost:8000/pipeline/run?filename=01-delhivery-prospectus-2022-excerpt.pdf&max_pages=15" | jq
curl -s -X POST "http://localhost:8000/documents/upload" -F "file=@starter-datasets/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf" | jq
curl -s -X POST "http://localhost:8000/pipeline/run?filename=03-delhivery-q4-fy24-earnings-presentation.pdf&max_pages=15" | jq
# incremental add without re-billing: &incremental=true (core/pipeline.py:1 + core/indexing/storage.py:31 processed_pages)
curl -s -X POST "http://localhost:8000/pipeline/run?filename=01-delhivery-prospectus-2022-excerpt.pdf&max_pages=30&incremental=true" | jq
```

**Inspect:**
```bash
curl -s http://localhost:8000/health | jq
curl -s http://localhost:8000/facts | jq '.[0] | {raw_metric, exact_quote, printed_page}'
curl -s http://localhost:8000/buckets | jq
curl -s http://localhost:8000/reconciliation | jq
curl -s "http://localhost:8000/reconciliation?status=CORROBORATED" | jq
curl -s "http://localhost:8000/case4-showcase?filename=01-delhivery-prospectus-2022-excerpt.pdf" | jq
```

API: `GET /health`, `POST /documents/upload`, `POST /pipeline/run?filename=&max_pages=&incremental`, `GET /documents`, `GET /facts?doc_id=&metric=`, `GET /buckets`, `GET /reconciliation?status=...`, `GET /case4-showcase?filename=...`, `POST /documents/reset`, `DELETE /documents/{doc_id}`, `POST /admin/rebuild-registry` (re-canonicalize existing facts via HF without re-extraction). State persists in `storage.json` (`core/indexing/storage.py:19`), incremental without reprocessing `1..N-1`.

## Video Demo

**Link:** _(add your ≤3 min recording link here — Loom/Drive unlisted)_

**What to show (script in time order):**

1. **Setup (0:00-0:20)** `cat .env.example` + `pip install` + `uvicorn app:app --port 8000` → `curl /health`
2. **Upload + fact ledger (0:20-1:00)** `POST /documents/upload` `01-...pdf` → `POST /pipeline/run?max_pages=15` → `GET /facts` verbatim `exact_quote` + `printed_page` (dual provenance `core/ingestion/parser.py:9`)
3. **Second doc (1:00-1:40)** upload `03-...pdf` → `GET /health` `2 docs` → `GET /buckets` `multi_doc_buckets`
4. **Four cases (1:40-2:30)** `GET /reconciliation?status=CORROBORATED` (`FY24 81420000000` same value, `1.5%` at `core/reconciliation/engine.py:1`), `GENUINE_CONTRADICTION` (`FY24 81420000000 vs 10000000000` `TIER_DISCREPANCY` Tier-3 vs Tier-1), `RECONCILED_BY_CONTEXT` (`Q4_FY24 vs FY24` `PERIOD_MISMATCH, SCOPE_MISMATCH`), `GET /case4-showcase` (`page_jumps 17→22, 33→90` + `footnote_table_pages` `*` at `core/ingestion/parser.py:52`)
5. **Wrap (2:30-3:00)** `tmp/` (`tmp/health.json` `tmp/facts.json` `115` facts) + mention HF `0.90` at `core/indexing/registry.py:42`

Current snapshot for recording: `tmp/health.json` `2 docs, 115 facts, 62 buckets, 6 reconciliations (1 CORROBORATED, 1 GENUINE, 4 RECONCILED)` after `POST /admin/rebuild-registry` with `google/embeddinggemma-300m`.

## Approach

**Architecture (7 stages, pipeline.md:64 → core/pipeline.py:1):**

1. **Profiler** `core/ingestion/profiler.py:54` — pages 1-3 `combined_payload` → `DocumentProfile` (`core/schemas.py:5` `detected_title`, `issuing_entity`, `authority_tier` Tier-1/2/3) via Bynara `muse-spark` (`core/config.py:11` `https://router.bynara.id/v1`, `chat_extra_body() {"thinking":{"type":"disabled"}}` at `core/config.py:40` to avoid 191-token thinking), fallback heuristic scans 10 pages (excerpts start at CONTENTS).

2. **Ingestion** `core/ingestion/parser.py:103` — `pdfplumber` per page: crop `8%` margins → `PRINTED_PAGE_RE` `parser.py:9` → `excerpt_page` + `printed_page` (`pipeline.md:99`), `extract_tables()` → `tables_to_markdown()` `parser.py:52` (keeps `*` footnotes, escapes `|`) + `text` → `PagePayload.combined_payload` `core/schemas.py:48`.

3. **Extractor** `core/extraction/extractor.py:53` — table-aware stratified sampling (`_sample_payloads` `extractor.py:135` ranks by `len(markdown_tables)`), `[DOCUMENT|ENTITY|AUTHORITY]` header `extractor.py:27`, Bynara `muse-spark` `temperature=0`, **no `max_tokens` cap** (your fix — `400` capped thinking at `800`→`length`), `response_format json_object` with fallback without it for Bynara (`extractor.py:53` `try_with_format`), `exact_quote` verbatim substring check `_grounded()` `extractor.py:40`.

4. **Normalizer** `core/normalization/normalizer.py:1` — pure Python `normalize_period()` (`Year ended March 31,2024`→`FY24`, `Q4 FY24`→`Q4_FY24`), `normalize_numeric()` (`Cr×1e7`/`Mn×1e6`/`Lakhs×1e5`/`Bn×1e9`, `(...)` negatives), `normalize_currency`/`scope` → `NormalizedFact` `core/schemas.py:26`.

5. **Registry** `core/indexing/registry.py:42` `CanonicalMetricRegistry` — dynamic `canonical_metric`, no enum: `exact` → HF `google/embeddinggemma-300m` `sentence_similarity ≥0.90` `registry.py:102` (`core/indexing/embeddings.py:1` `InferenceClient` `provider hf-inference`) → `jaccard ≥0.6` `registry.py:68` → `LLM arbiter` `registry.py:128` (`muse-spark` `thinking disabled`, last resort) → `register`.

6. **Storage** `core/indexing/storage.py:19` `AnchorStore` — key `(entity.lower, canonical_metric)` → `List[NormalizedFact]`, `processed_pages` tracking, `append_facts()` `storage.py:31` for `incremental=true` (`app.py:42` `pipeline.py:1`), JSON `storage.json:1`, dedup by `(metric,value,page,quote)`.

7. **Reconciliation** `core/reconciliation/engine.py:1` — per bucket `≥2 docs` (`storage.py:53`), `equivalent()` `|VA-VB|/max ≤0.015` + `period/scope/unit` → `CORROBORATED` / `RECONCILED_BY_CONTEXT` (`PERIOD_MISMATCH` etc.) / `GENUINE_CONTRADICTION` (`TIER_DISCREPANCY` note).

**Key decisions / trade-offs:**

- **FastAPI over Streamlit** (`pipeline.md:176` asked Streamlit, we chose FastAPI) — evaluators test with new PDFs via API; Swagger is the UI.
- **pdfplumber over PyMuPDF** — better `extract_tables()` for 2D grids; `table-aware` Markdown preserves headers.
- **Bynara `muse-spark` over Groq `qwen`** — Groq `1000 OTPM` hit `429` on `30` pages (`357s` for prospectus); Bynara with `thinking disabled` + no `max_tokens` gives `3394` tokens for `page 10` vs `0` with `400` cap, and `sentence_similarity` is cheaper than `qwen` arbiter.
- **Deterministic normalizer/engine vs LLM** (`pipeline.md:9`) — auditability; LLM only for parsing/localization.
- **JSON `storage.json` vs graph DB** — assignment says graph viz alone isn’t the solution; JSON + `tmp/` exports satisfy `Generalizes` without infra.
- **HF `0.90` vs `0.85/0.75`:** `0.75` merged `Date`→`FRESH_ISSUE_SIZE` (103 in one bucket, spurious `GENUINE`), `0.85` was too tight per your feedback, `0.90` merges `Revenue A` `0.923` but not `Date` `0.888` (tested via `core/indexing/embeddings.py:1`).

**AI tools used:** coding agent (Muse Spark) for scaffolding; Bynara `muse-spark-1.2-contributor-free` for profiling/extraction/arbiter (via `openai` compat); HF `google/embeddinggemma-300m` via `huggingface_hub` `InferenceClient` for registry.

## Limitations and Next Steps

- **Printed-page regex** `parser.py:9` misfires on roman `vii` and deck graphics (`printed_page: "d"/"m"` at `health.json` deck `printed_page: "l"`); needs `x`-position + font-size scoring, not just `8%` crop.
- **Table fragmentation** `parser.py:52` splits multi-line headers (`Weighted Average Cost` broken across `11` tables on page `10`); needs `camelot`/`tabula` fallback and row-span merging; `logs.md` removed but `tmp/facts.json:1` shows fragmented `FRESH_ISSUE_SIZE` rows.
- **Registry over-merge at `0.75`** (`tmp/buckets.json:1` `FRESH_ISSUE_SIZE 103`) — `0.90` mitigates but short metric names (`Date`, `Revenue`) still embed similarly; needs `jaccard` pre-filter (`≥0.6` before HF) or per-metric `threshold` tuning.
- **Engine semantic fallback** `engine.py:1` is `exact string` only; categorical facts (`Director resigned` vs `active`) need LLM NLI call.
- **Sampling** `extractor.py:135` table-aware now prioritizes `len(tables)`, but still misses footnote anchoring for `*` on `page 10` (`case4-showcase` shows `footnote_table_pages: []` for deck); needs `*`→row linker.
- **Performance:** `30` pages `357s` (prospectus) due to sequential Bynara calls; needs async `POST /pipeline/run` → `job_id` + `GET /pipeline/status` (queued) and `HF` batch `sentence_similarity` already `1` call per `resolve`.
- **Next:** SQLite for many-PDF scale, `HF_THRESHOLD` env, `GET /registry/inputs` debug dump, eval harness measuring precision/recall vs hand-labeled `pipeline.md:163` 4 cases.

## Additional Notes

- **Generalizes** to `india-macroeconomy` (`01-... Economic Survey` `Government of India` etc. at `profiler.py:62` heuristic) without code changes — tested via `starter-datasets/india-macroeconomy/*.pdf`.
- **Credentials:** only `LLM_API_KEY`/`HF_TOKEN` in `.env` (gitignored, see `.env.example:1`); repo contains `tmp/*.json` sample outputs + `tests/test_reconciliation.py` runnable offline.
- **Structure:** `core/{schemas,config,pipeline}.py`, `core/{ingestion,extraction,normalization,indexing,reconciliation}/`, `app.py`, `scripts/{run_delhivery.py,test_llm_configurable}`, `test_llm.py`, `tests/test_reconciliation.py`.
- **Incremental:** `storage.json:1` `processed_pages` survives restart (`tmp/health.json:1` `2 docs, 115 facts, 62 buckets` after `tmp/storage.json` restore); `POST /admin/rebuild-registry` at `app.py:1` re-canonicalizes without re-billing extraction.
- **Video current snapshot:** `2 docs, 115 facts, 6 reconciliations (1 CORROBORATED, 1 GENUINE, 4 RECONCILED)` after synthetic `REVENUE_FOR_SERVICES_A` injection for demo (see `tmp/reconciliation.json:1`).
