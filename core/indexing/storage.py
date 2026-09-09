"""Step 6 — Incremental Anchor Index.

Key: (subject_entity_normalized, canonical_metric) -> List[NormalizedFact]
Incremental: adding Doc N never reprocesses Docs 1..N-1.
Persist: JSON file (default storage.json) so FastAPI restarts keep state.
"""
import json
import os
from collections import defaultdict
from typing import Dict, List, Tuple

from core.schemas import DocumentProfile, NormalizedFact


def _norm_entity(e: str) -> str:
    return (e or "UNKNOWN").strip().lower()


class AnchorStore:
    def __init__(self, persist_path: str = "storage.json"):
        self.persist_path = persist_path
        self.docs: Dict[str, dict] = {}  # doc_id -> {profile dict, num_facts}
        self.buckets: Dict[Tuple[str, str], List[dict]] = defaultdict(list)

    def add_document(self, doc_id: str, profile: DocumentProfile, facts: List[NormalizedFact], processed_pages: list[int] | None = None):
        """Incremental append. Overwrites same doc_id unless incremental append is used."""
        # Remove old facts from this doc if re-uploaded (full replace)
        self.remove_document(doc_id)
        self.docs[doc_id] = {
            "profile": profile.model_dump(),
            "num_facts": len(facts),
            "processed_pages": sorted(set(processed_pages or [])),
        }
        for f in facts:
            key = (_norm_entity(f.raw.subject_entity), f.canonical_metric)
            self.buckets[key].append(f.model_dump())
        self.save()

    def append_facts(self, doc_id: str, profile: DocumentProfile, new_facts: List[NormalizedFact], new_pages: list[int]):
        """Incrementally append new pages' facts to existing doc without reprocessing old pages."""
        if doc_id not in self.docs:
            return self.add_document(doc_id, profile, new_facts, processed_pages=new_pages)
        # dedup by (metric, value, excerpt_page, quote) to avoid duplicates on retry
        existing_keys = {
            (d.get("canonical_metric"), d["raw"]["raw_value"], d["raw"]["excerpt_page"], d["raw"]["exact_quote"][:60])
            for lst in self.buckets.values()
            for d in lst
            if d.get("source_doc_id") == doc_id
        }
        to_add = []
        for f in new_facts:
            k = (f.canonical_metric, f.raw.raw_value, f.raw.excerpt_page, f.raw.exact_quote[:60])
            if k not in existing_keys:
                to_add.append(f)
        for f in to_add:
            key = (_norm_entity(f.raw.subject_entity), f.canonical_metric)
            self.buckets[key].append(f.model_dump())
        # update doc stats
        prev_pages = set(self.docs[doc_id].get("processed_pages", []))
        prev_pages.update(new_pages)
        self.docs[doc_id]["profile"] = profile.model_dump()
        self.docs[doc_id]["num_facts"] = sum(
            1 for lst in self.buckets.values() for d in lst if d.get("source_doc_id") == doc_id
        )
        self.docs[doc_id]["processed_pages"] = sorted(prev_pages)
        self.save()

    def remove_document(self, doc_id: str):
        if doc_id in self.docs:
            del self.docs[doc_id]
        for key in list(self.buckets.keys()):
            self.buckets[key] = [d for d in self.buckets[key] if d.get("source_doc_id") != doc_id]
            if not self.buckets[key]:
                del self.buckets[key]

    def all_facts(self) -> List[NormalizedFact]:
        out = []
        for lst in self.buckets.values():
            for d in lst:
                out.append(NormalizedFact(**d))
        return out

    def buckets_with_multi_docs(self) -> Dict[Tuple[str, str], List[NormalizedFact]]:
        """Buckets containing facts from >= 2 distinct docs (reconciliation candidates)."""
        out = {}
        for key, lst in self.buckets.items():
            docs = {d.get("source_doc_id") for d in lst}
            if len(docs) >= 2:
                out[key] = [NormalizedFact(**d) for d in lst]
        return out

    def stats(self) -> dict:
        return {
            "num_documents": len(self.docs),
            "num_facts": sum(len(v) for v in self.buckets.values()),
            "num_buckets": len(self.buckets),
            "documents": [
                {"doc_id": k, **v} for k, v in self.docs.items()
            ],
        }

    def save(self):
        try:
            data = {
                "docs": self.docs,
                "buckets": {f"{k[0]}|||{k[1]}": v for k, v in self.buckets.items()},
            }
            with open(self.persist_path, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def load(self):
        if not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path) as f:
                data = json.load(f)
            self.docs = data.get("docs", {})
            self.buckets = defaultdict(list)
            for kk, v in data.get("buckets", {}).items():
                e, m = kk.split("|||", 1)
                self.buckets[(e, m)] = v
        except Exception:
            pass
