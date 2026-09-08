"""Orchestrator: wires Stages 1-7. Incremental: process one PDF at a time."""
import hashlib
from typing import List

from core.ingestion.parser import parse_pdf
from core.ingestion.profiler import profile_document
from core.extraction.extractor import extract_facts_for_doc
from core.normalization.normalizer import normalize_fact
from core.indexing.registry import CanonicalMetricRegistry
from core.indexing.storage import AnchorStore
from core.reconciliation.engine import reconcile_all
from core.schemas import NormalizedFact


def doc_id_for(path: str, title: str) -> str:
    h = hashlib.md5(path.encode()).hexdigest()[:8]
    safe = "".join(c if c.isalnum() else "_" for c in title)[:40]
    return f"{safe}_{h}"


class Pipeline:
    def __init__(self, store: AnchorStore, registry: CanonicalMetricRegistry):
        self.store = store
        self.registry = registry

    def process_pdf(self, pdf_path: str, max_pages: int = 100, progress_cb=None) -> dict:
        payloads = parse_pdf(pdf_path)
        profile = profile_document(payloads)
        doc_id = doc_id_for(pdf_path, profile.detected_title)

        raw_facts = extract_facts_for_doc(profile, payloads, max_pages=max_pages, progress_cb=progress_cb)

        normed: List[NormalizedFact] = []
        for rf in raw_facts:
            canon = self.registry.resolve(rf.raw_metric)
            normed.append(normalize_fact(rf, canon, profile.authority_tier, doc_id))

        self.store.add_document(doc_id, profile, normed)

        results = reconcile_all(self.store.buckets_with_multi_docs())
        return {
            "doc_id": doc_id,
            "profile": profile.model_dump(),
            "num_pages": len(payloads),
            "num_facts": len(normed),
            "num_reconciliations": len(results),
            "page_map": [
                {"excerpt_page": p.excerpt_page, "printed_page": p.printed_page}
                for p in payloads
            ],
        }
