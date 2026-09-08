"""FastAPI entry point: upload PDFs, inspect facts + reconciliations."""
import os
import shutil
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import JSONResponse

from core.indexing.registry import CanonicalMetricRegistry
from core.indexing.storage import AnchorStore
from core.pipeline import Pipeline
from core.reconciliation.engine import reconcile_all

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="Fact Knowledge Layer (Superjoin)")

store = AnchorStore(persist_path="storage.json")
store.load()
registry = CanonicalMetricRegistry()
pipe = Pipeline(store, registry)


@app.get("/health")
def health():
    from core.config import is_llm_configured

    return {"status": "ok", "llm_configured": is_llm_configured(), **store.stats()}


@app.post("/documents/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDFs accepted")
    dest = os.path.join(UPLOAD_DIR, file.filename)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"filename": file.filename, "path": dest}


@app.post("/pipeline/run")
def run_pipeline(filename: str, max_pages: int = 100):
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404, f"Upload {filename} first via /documents/upload")
    try:
        return pipe.process_pdf(path, max_pages=max_pages)
    except ValueError as e:
        # e.g. GROQ_API_KEY missing
        raise HTTPException(400, str(e))


@app.get("/documents")
def list_documents():
    return store.stats()


@app.get("/facts")
def list_facts(doc_id: Optional[str] = None, metric: Optional[str] = None):
    facts = store.all_facts()
    if doc_id:
        facts = [f for f in facts if f.source_doc_id == doc_id]
    if metric:
        facts = [f for f in facts if f.canonical_metric == metric]
    return [f.model_dump() for f in facts]


@app.get("/reconciliation")
def list_reconciliation(status: Optional[str] = Query(default=None, pattern="^(CORROBORATED|GENUINE_CONTRADICTION|RECONCILED_BY_CONTEXT)$")):
    results = reconcile_all(store.buckets_with_multi_docs())
    if status:
        results = [r for r in results if r.status == status]
    return [r.model_dump() for r in results]


@app.get("/case4-showcase")
def case4_showcase(filename: str):
    """Page-jump + footnote audit for a given uploaded filename."""
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404, "Upload first")
    from core.ingestion.parser import parse_pdf

    payloads = parse_pdf(path)
    # Detect jumps in printed sequence
    jumps = []
    prev = None
    for p in payloads:
        try:
            cur = int(p.printed_page) if p.printed_page and p.printed_page.isdigit() else None
        except Exception:
            cur = None
        if prev is not None and cur is not None and cur - prev > 1:
            jumps.append({"excerpt_page": p.excerpt_page, "printed_page": p.printed_page, "gap_from": prev})
        if cur is not None:
            prev = cur
    # Footnote check: tables containing '*'
    footnote_pages = [
        {"excerpt_page": p.excerpt_page, "printed_page": p.printed_page}
        for p in payloads
        if any("*" in t for t in p.markdown_tables)
    ][:20]
    return {
        "filename": filename,
        "num_pages": len(payloads),
        "page_jumps": jumps[:20],
        "footnote_table_pages": footnote_pages,
        "page_map": [
            {"excerpt_page": p.excerpt_page, "printed_page": p.printed_page}
            for p in payloads
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
