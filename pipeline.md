# Technical Architecture & Pipeline Specification: Fact Knowledge Layer

## 1. System Objective & Design Rationale
Build an auditable **Fact Knowledge Layer** that extracts numerical and semantic facts across multi-page financial PDFs, grounds them with exact document evidence, and determines cross-document relationships: **Corroborated**, **Genuine Contradiction**, and **Apparent Contradiction (Contextually Reconciled)**.

### Architectural Invariants (Non-Negotiables)
1. **Zero Filename / Hardcoded Schema Reliance** *(Ref: Assignment PDF, Page 1)*: Never parse filenames (e.g., `01-delhivery...`) to infer dates, entities, or authority. All metadata must be dynamically profiled from document content.
2. **Table-Structure Preservation** *(Ref: Superjoin Company Profile - Deep Document Intelligence)*: Do not use naive character splitters that destroy 2D tabular grids. Tables must be extracted and represented in Markdown/HTML to preserve row/column header associations.
3. **Probabilistic Extraction vs. Deterministic Normalization**: Use LLMs strictly for semantic parsing and quote localization. Use deterministic Python code for date/period parsing, unit scaling, and numerical equality comparisons.
4. **Dual-Page Provenance** *(Ref: Delhivery README Note on Jumping Page Numbers)*: Track both `excerpt_page_idx` (physical PDF index) and `printed_page_num` (extracted from header/footer margins) to guarantee real-world auditability.
5. **Dynamic Schema Evolution** *(Ref: Assignment PDF, Brownie Points)*: No fixed metric enum. New metrics are registered and clustered dynamically via an embedding/LLM canonicalization registry.

---

## 2. Core Data Models (`schemas.py`)

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal

class DocumentProfile(BaseModel):
    detected_title: str = Field(description="Title extracted from cover/header")
    issuing_entity: str = Field(description="Primary company/institution, e.g., 'Delhivery Limited'")
    primary_period: Optional[str] = Field(description="Main reporting period if global, e.g., 'FY 2023-24'")
    doc_type: str = Field(description="e.g., 'Prospectus / DRHP', 'Annual Report', 'Earnings Presentation'")
    authority_tier: Literal["Tier-1 (Audited/Statutory)", "Tier-2 (Governance/Minutes)", "Tier-3 (Unaudited/Presentation)"]
    base_currency: Optional[str] = Field(description="Inferred dominant currency, e.g., 'INR', 'USD'")

class RawFact(BaseModel):
    subject_entity: str = Field(description="Entity the fact is about, e.g., 'Delhivery Limited'")
    raw_metric: str = Field(description="Metric as written, e.g., 'Revenue from operations'")
    raw_value: str = Field(description="Verbatim string representation, e.g., '₹8,746.2 Mn'")
    raw_unit: Optional[str] = Field(description="Extracted unit, e.g., '₹ in Millions', 'Pin codes'")
    raw_period: Optional[str] = Field(description="Extracted period, e.g., 'Year ended March 31, 2024'")
    scope: Optional[str] = Field(description="Consolidated, Standalone, or Segment name (e.g., Express Parcel)")
    exact_quote: str = Field(description="Verbatim text sentence or table row supporting this fact")
    excerpt_page: int = Field(description="0-indexed physical page in PDF")
    printed_page: Optional[str] = Field(description="Visual/printed page number extracted from margins")

class NormalizedFact(BaseModel):
    raw: RawFact
    canonical_metric: str          # Dynamically resolved metric key
    normalized_numeric_value: Optional[float]  # Absolute scaled float (e.g., 8746200000.0)
    normalized_currency: Optional[str]         # Standardized ISO (e.g., "INR")
    normalized_period: str         # Standardized token (e.g., "FY24", "Q4_FY24", "FY22")
    normalized_scope: str          # "CONSOLIDATED", "STANDALONE", or segment token
    authority_tier: str            # Inherited from DocumentProfile
    source_doc_id: str             # Hash or title of source document

class ReconciliationResult(BaseModel):
    canonical_metric: str
    fact_a: NormalizedFact
    fact_b: NormalizedFact
    status: Literal["CORROBORATED", "GENUINE_CONTRADICTION", "RECONCILED_BY_CONTEXT"]
    confidence_score: float
    reconciliation_dimensions: list[str]  # e.g., ["PERIOD_MISMATCH", "SCOPE_MISMATCH", "TIER_DISCREPANCY"]
    explanation: str                      # Clear human-readable reasoning for bankers
```

---

## 3. Pipeline Implementation Blueprint

```
[Input PDF] 
     │
     ▼
[1. Document Profiler] ──► Extracts metadata & Authority Tier from Pages 1–3
     │
     ▼
[2. Table-Aware Ingestion] ──► Interleaves Markdown tables + text; tracks dual page numbers
     │
     ▼
[3. Targeted Chunk Extraction] ──► LLM structured output -> List[RawFact]
     │
     ▼
[4. Deterministic Normalizer] ──► Converts raw strings -> NormalizedFact (dates, units, multipliers)
     │
     ▼
[5. Dynamic Canonical Registry] ──► Maps raw_metric -> canonical_metric via semantic alignment
     │
     ▼
[6. Incremental Anchor Index] ──► Buckets facts: { (Entity, Canonical_Metric): [NormalizedFact, ...] }
     │
     ▼
[7. Reconciliation Engine] ──► Computes NLI relationship on colliding facts across documents
```

### Stage 1: Document Profiler (`ingestion/profiler.py`)
- **Input**: First 1–3 pages of text from the PDF.
- **Task**: Run a lightweight prompt using Pydantic structured outputs (`DocumentProfile`).
- **Logic**: Classify `authority_tier` dynamically:
  - Regulatory filings / Audited financials / Prospectus $\to$ `Tier-1 (Audited/Statutory)`
  - Board resolutions / Shareholder notices $\to$ `Tier-2 (Governance/Minutes)`
  - Investor decks / Press releases / Transcripts $\to$ `Tier-3 (Unaudited/Presentation)`

### Stage 2: Table-Aware Ingestion & Dual Page Parsing (`ingestion/parser.py`)
- **Tool**: `pdfplumber`.
- **Dual-Page Parsing**:
  - For each page, crop the top 8% and bottom 8% margins.
  - Run regex `(?:Page\s*)?(\d+|[ivxlcdm]+)\b` to extract the printed page string. Record `excerpt_page` and `printed_page`.
- **Table Preservation**:
  - Detect table structures via `page.extract_tables()`.
  - Convert 2D lists into clean Markdown tables (preserving header rows and boundary alignment).
  - Extract non-table text blocks. Combine Markdown tables with accompanying text into a single context payload per page.

### Stage 3: Fact Extraction (`extraction/extractor.py`)
- **Execution**: Run page payloads through the LLM with `RawFact` schema.
- **Context Injection**: Prepend `[DOCUMENT: {title} | ENTITY: {entity} | AUTHORITY: {tier}]` to every page payload to resolve implicit pronouns and context amnesia.

### Stage 4: Deterministic Normalizer (`normalization/normalizer.py`)
Implement pure Python regex/math functions without LLM indeterminism:
- **Temporal Normalization**:
  - `"Year ended March 31, 2024"`, `"FY 23-24"`, `"FY24"` $\to$ `"FY24"`.
  - `"Quarter ended March 31, 2024"`, `"Q4 FY24"` $\to$ `"Q4_FY24"`.
  - `"Year ended March 31, 2022"`, `"FY 2021-22"`, `"FY22"` $\to$ `"FY22"`.
- **Numerical & Unit Normalization**:
  - Parse digits and currency symbols (`₹`, `INR`, `$`, `USD`).
  - Unit multipliers:
    - `"Cr"` / `"Crores"` $\to \times 10^7$
    - `"Mn"` / `"Millions"` $\to \times 10^6$
    - `"Lakhs"` / `"Lacs"` $\to \times 10^5$
    - `"Bn"` / `"Billions"` $\to \times 10^9$
  - `normalized_numeric_value = raw_number * multiplier`

### Stage 5: Dynamic Schema Canonicalization (`indexing/registry.py`)
- Maintain an evolving `CanonicalMetricRegistry`:
  - Holds known canonical metric keys: `["TOTAL_REVENUE", "PIN_CODE_COVERAGE", "ADJUSTED_EBITDA", ...]`.
- When a `raw_metric` arrives:
  - If registry is empty, register it.
  - If registry contains items, check cosine similarity via an embedding model (threshold $\ge 0.85$) or use a fast LLM arbiter: *"Does '{raw_metric}' represent the exact same operational/financial metric as any of {existing_list}?"*
  - Re-assign `fact.canonical_metric` to the resolved canonical key.
- Satisfies the requirement: **No hardcoded dictionary; schema evolves dynamically with any arbitrary PDF.**

### Stage 6: Incremental Anchor Index (`indexing/storage.py`)
- **Key**: `(fact.subject_entity, fact.canonical_metric)`
- **Value**: `List[NormalizedFact]`
- **Incremental property**: Uploading Document $N$ adds facts into existing buckets without reprocessing Documents $1 \dots N-1$.

### Stage 7: Reconciliation Engine (`reconciliation/engine.py`)
For every bucket containing facts from $\ge 2$ distinct documents, run pairwise evaluations:

1. **Equivalence Check**:
   - Numerical: $|V_A - V_B| / \max(V_A, V_B) \le 0.015$ (within 1.5% tolerance for rounding).
   - Semantic: String match or LLM semantic equivalence over categorical claims.
2. **Context Dimensions Comparison**:
   - `period_match = (fact_a.normalized_period == fact_b.normalized_period)`
   - `scope_match = (fact_a.normalized_scope == fact_b.normalized_scope)`
   - `unit_match = (fact_a.normalized_currency == fact_b.normalized_currency)`
3. **Classification Decision Logic**:
   - **Case 1 (`CORROBORATED`)**:
     - `Equivalence Check == True` AND `period_match == True`.
     - *Reasoning*: Confirmed across documents despite differing phrasings.
   - **Case 3 (`RECONCILED_BY_CONTEXT`)**:
     - `Equivalence Check == False` AND (`not period_match` OR `not scope_match` OR `not unit_match`).
     - *Reasoning*: Discrepancy explained by differing dimensions (e.g., FY22 vs FY24, Standalone vs Consolidated, INR vs USD).
   - **Case 2 (`GENUINE_CONTRADICTION`)**:
     - `Equivalence Check == False` AND `period_match == True` AND `scope_match == True` AND `unit_match == True`.
     - *Reasoning*: Direct conflict under identical reporting parameters. Include authority divergence note if `fact_a.authority_tier != fact_b.authority_tier`.

---

## 4. Verification Specs on `delhivery/` Dataset

The coding agent must verify that the pipeline produces valid instances for each of the 4 required cases:

| Case | Expected Grounded Delhivery Example | System Classification |
| :--- | :--- | :--- |
| **Case 1** | **Pin Code Reach**: Prospectus 2022 states ~17,000+ pin codes; Annual Report FY24 / Presentation states 18,000+ pin codes. Or historical FY21/FY22 revenue repeated verbatim in the FY24 report's comparative tables. | `CORROBORATED` |
| **Case 2** | **Operating Metric Discrepancy**: FY24 Adjusted EBITDA in the Earnings Presentation (Tier-3 non-GAAP definition) vs. Operating Profit/Loss in the Audited Consolidated P&L (Tier-1 Ind AS). | `GENUINE_CONTRADICTION` (with Authority Tier note) |
| **Case 3** | **Revenue Growth Shift**: Total Revenue in Prospectus 2022 (covering FY21/FY22) vs. Total Revenue in Annual Report FY24 (covering FY23/FY24). | `RECONCILED_BY_CONTEXT` (Dimension: Period Mismatch) |
| **Case 4** | **Page Jump Provenance & Tabular Footnote**: Documenting how physical page 35 maps to printed page 216, and how table footnote asterisks (`*`) were anchored to line items instead of lost. | Surface in UI audit tab as technical failure-handling showcase. |

---

## 5. User Interface Contract (`app.py` - Streamlit)
1. **Sidebar**:
   - Multi-file uploader (accepts any PDF).
   - Status badge showing active indexed documents with dynamically detected Authority Tiers.
   - Button: `Process & Incrementally Update Knowledge Layer`.
2. **Main Dashboard**:
   - **KPI Metrics**: Total Facts Extracted, Corroborations Found, Reconciled Claims, Active Contradictions.
   - **Fact Ledger (Dataframe/Table)**:
     - Filter buttons: `[All | Corroborations | Reconciled | Contradictions]`.
     - Columns: `Metric | Status | Entity | Doc A (Quote + Printed Page) | Doc B (Quote + Printed Page) | Reconciliation Reasoning`.
3. **Inspector Modal / Detail View**:
   - Expandable row showing side-by-side verbatim quotes, extracted authority tiers, and exact dimensional breakdowns.
4. **"Case 4 Showcase" Tab**:
   - Dedicated tab in the UI displaying the handling of the jumping page numbers and footnote attachment to satisfy reviewer evaluation criteria immediately.

---

## 6. Recommended Repository Structure
```text
├── README.md
├── requirements.txt
├── app.py                      # Streamlit entry point
├── core/
│   ├── schemas.py              # Pydantic models
│   ├── config.py               # LLM client & API settings
│   ├── ingestion/
│   │   ├── profiler.py         # Content-driven document metadata & tier extractor
│   │   └── parser.py           # Table-preserving pdfplumber & dual-page extractor
│   ├── extraction/
│   │   └── extractor.py        # Structured RawFact extraction
│   ├── normalization/
│   │   └── normalizer.py       # Deterministic dates, units, and float scaler
│   ├── indexing/
│   │   ├── registry.py         # Dynamic metric canonicalization registry
│   │   └── storage.py          # In-memory / SQLite incremental anchor store
│   └── reconciliation/
│       └── engine.py           # Tri-state NLI and dimensional discrepancy logic
└── tests/
    └── test_reconciliation.py  # Unit tests for the 4 target cases
```
