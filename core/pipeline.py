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

    def process_pdf(self, pdf_path: str, max_pages: int = 100, progress_cb=None, incremental: bool = False) -> dict:
        payloads = parse_pdf(pdf_path)
        profile = profile_document(payloads)
        doc_id = doc_id_for(pdf_path, profile.detected_title)

        # Incremental: only process newly sampled pages not yet seen for this doc
        if incremental and doc_id in self.store.docs:
            from core.extraction.extractor import _sample_payloads

            already = set(self.store.docs[doc_id].get("processed_pages", []))
            # compute what the full stratified sample would be at requested max_pages
            full_sampled = _sample_payloads(payloads, max_pages)
            needed = [p for p in full_sampled if p.excerpt_page not in already]
            if not needed:
                results = reconcile_all(self.store.buckets_with_multi_docs())
                return {
                    "doc_id": doc_id,
                    "profile": profile.model_dump(),
                    "num_pages": len(payloads),
                    "num_facts": 0,
                    "num_facts_total": self.store.docs[doc_id].get("num_facts", 0),
                    "num_reconciliations": len(results),
                    "incremental": True,
                    "already_processed_pages": sorted(already),
                    "page_map": [
                        {"excerpt_page": p.excerpt_page, "printed_page": p.printed_page}
                        for p in payloads
                    ],
                }
            # extract only needed pages
            from core.extraction.extractor import extract_facts_for_page

            from core.config import get_llm_client

            client = get_llm_client()
            raw_facts = []
            for p in needed:
                if not p.combined_payload.strip():
                    continue
                try:
                    raw_facts.extend(extract_facts_for_page(profile, p, client=client))
                except Exception as e:
                    if progress_cb:
                        progress_cb(p.excerpt_page, str(e))
                    continue

            normed: List[NormalizedFact] = []
            for rf in raw_facts:
                canon = self.registry.resolve(rf.raw_metric)
                normed.append(normalize_fact(rf, canon, profile.authority_tier, doc_id))

            new_pages = [p.excerpt_page for p in needed]
            self.store.append_facts(doc_id, profile, normed, new_pages)

            results = reconcile_all(self.store.buckets_with_multi_docs())
            return {
                "doc_id": doc_id,
                "profile": profile.model_dump(),
                "num_pages": len(payloads),
                "num_facts": len(normed),
                "num_facts_total": self.store.docs[doc_id].get("num_facts", 0),
                "num_reconciliations": len(results),
                "incremental": True,
                "new_pages": new_pages,
                "already_processed_pages": sorted(already),
                "page_map": [
                    {"excerpt_page": p.excerpt_page, "printed_page": p.printed_page}
                    for p in payloads
                ],
            }

        # Full (non-incremental) path
        raw_facts = extract_facts_for_doc(profile, payloads, max_pages=max_pages, progress_cb=progress_cb)

        normed: List[NormalizedFact] = []
        for rf in raw_facts:
            canon = self.registry.resolve(rf.raw_metric)
            normed.append(normalize_fact(rf, canon, profile.authority_tier, doc_id))

        # track which pages were sampled
        from core.extraction.extractor import _sample_payloads

        sampled = _sample_payloads(payloads, max_pages)
        sampled_pages = [p.excerpt_page for p in sampled]
        self.store.add_document(doc_id, profile, normed, processed_pages=sampled_pages)

        results = reconcile_all(self.store.buckets_with_multi_docs())
        return {
            "doc_id": doc_id,
            "profile": profile.model_dump(),
            "num_pages": len(payloads),
            "num_facts": len(normed),
            "num_reconciliations": len(results),
            "incremental": False,
            "page_map": [
                {"excerpt_page": p.excerpt_page, "printed_page": p.printed_page}
                for p in payloads
            ],
        }
