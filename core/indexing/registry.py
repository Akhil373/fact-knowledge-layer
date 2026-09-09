"""Step 5 — Dynamic CanonicalMetricRegistry (no hardcoded enum).

Pipeline spec: new raw_metric -> cosine >=0.90 OR fast LLM arbiter
"Does '{raw_metric}' represent the exact same metric as any of {existing}?"
Implementation: HF API embedding (google/embeddinggemma-300m) >=0.90 -> merge,
else jaccard/LLM fallback, else register new key.
"""

import json
import re
from typing import Dict, List, Optional


def _tokens(s: str) -> set:
    toks = re.findall(r"[a-z0-9]+", s.lower())
    stop = {"from", "of", "the", "and", "for", "in", "on", "total", "net"}
    return {t for t in toks if t not in stop} or set(
        re.findall(r"[a-z0-9]+", s.lower())
    )


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _to_key(raw_metric: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", raw_metric.lower()).strip("_").upper()
    key = re.sub(r"_+", "_", key)
    return key[:64] if key else "UNKNOWN_METRIC"


class CanonicalMetricRegistry:
    """Evolving map: canonical_key -> [raw_metric variants]."""

    def __init__(self, threshold: float = 0.90):
        self.threshold = threshold
        self._keys: List[str] = []  # insertion order
        self._variants: Dict[str, List[str]] = {}

    def __len__(self):
        return len(self._keys)

    def keys(self) -> List[str]:
        return list(self._keys)

    def resolve(
        self, raw_metric: str, use_llm: bool = True, use_embedding: bool = True
    ) -> str:
        """Map raw_metric -> canonical key, registering new keys dynamically."""
        raw = (raw_metric or "").strip()
        if not raw:
            return "UNKNOWN_METRIC"
        if not self._keys:
            return self._register(raw)

        # Fast path: exact / case-insensitive match on variant
        for k, variants in self._variants.items():
            if any(raw.lower() == v.lower() for v in variants):
                return k

        # HF embedding path (google/embeddinggemma-300m) — skip if HF_TOKEN not set
        if use_embedding:
            hit = self._embedding_match(raw)
            if hit:
                self._variants[hit].append(raw)
                return hit

        # Jaccard fallback (no HF available or below threshold)
        best, best_score = None, 0.0
        for k in self._keys:
            for v in self._variants[k]:
                s = _jaccard(raw, v)
                if s > best_score:
                    best, best_score = k, s
        if best and best_score >= 0.6:  # conservative deterministic merge
            self._variants[best].append(raw)
            return best

        # LLM arbiter (Groq, cheap model)
        if use_llm:
            hit = self._llm_arbiter(raw)
            if hit:
                self._variants[hit].append(raw)
                return hit

        return self._register(raw)

    def _register(self, raw: str) -> str:
        key = _to_key(raw)
        # dedupe key collision: append _2, _3...
        base, i = key, 2
        while key in self._variants:
            key = f"{base}_{i}"
            i += 1
        self._keys.append(key)
        self._variants[key] = [raw]
        return key

    def _embedding_match(self, raw: str) -> Optional[str]:
        """Try HF sentence_similarity >= threshold via InferenceClient."""
        try:
            from core.indexing.embeddings import sentence_similarity, is_available

            if not is_available():
                return None
            # Collect all variants flat with key mapping
            all_variants: List[str] = []
            variant_to_key: List[str] = []
            for k, variants in self._variants.items():
                for v in variants:
                    all_variants.append(v)
                    variant_to_key.append(k)
            if not all_variants:
                return None
            # Single InferenceClient call: source=raw vs all existing variants
            scores = sentence_similarity(raw, all_variants)
            best_key, best_score = None, 0.0
            for key, score in zip(variant_to_key, scores):
                if score > best_score:
                    best_key, best_score = key, score
            if best_key and best_score >= self.threshold:
                return best_key
            return None
        except Exception:
            return None

    def _llm_arbiter(self, raw: str) -> Optional[str]:
        try:
            from core.config import (
                is_llm_configured,
                get_llm_client,
                GROQ_ARBITER_MODEL,
                chat_extra_body,
            )
            import re

            if not is_llm_configured():
                return None
            client = get_llm_client()
            extra = chat_extra_body()
            kwargs = dict(
                model=GROQ_ARBITER_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": 'You map financial metric names. Reply ONLY JSON: {"match": <canonical key or null>}',
                    },
                    {
                        "role": "user",
                        "content": f"Does '{raw}' represent the exact same operational/financial metric as any of {self._keys}? If yes reply with that exact key, else null.",
                    },
                ],
                temperature=0,
                timeout=15,
            )
            # Try with response_format first, fallback without for Bynara
            try:
                kwargs["response_format"] = {"type": "json_object"}
                if extra:
                    kwargs["extra_body"] = extra
                resp = client.chat.completions.create(**kwargs)
            except Exception as e:
                if "invalid" in str(e).lower() or "response_format" in str(e).lower():
                    kwargs.pop("response_format", None)
                    resp = client.chat.completions.create(**kwargs)
                else:
                    raise
            content = resp.choices[0].message.content
            try:
                data = json.loads(content)
            except Exception:
                import re

                m2 = re.search(r"\{.*\}", content, re.DOTALL)
                try:
                    data = json.loads(m2.group(0)) if m2 else {}
                except Exception:
                    data = {}
            m = data.get("match")
            return m if m in self._variants else None
        except Exception:
            return None

    def to_dict(self) -> dict:
        return {"keys": self._keys, "variants": self._variants}

    @classmethod
    def from_dict(cls, d: dict) -> "CanonicalMetricRegistry":
        r = cls()
        r._keys = d.get("keys", [])
        r._variants = d.get("variants", {})
        return r
