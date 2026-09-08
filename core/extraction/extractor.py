"""Step 7 — Targeted Chunk Extraction (LLM structured output -> List[RawFact]).

Context injection per spec: prepend
[DOCUMENT: {title} | ENTITY: {entity} | AUTHORITY: {tier}]
to every page payload.
Grounding: exact_quote must be a verbatim substring of page payload (checked).
"""
import json
from typing import List

from core.schemas import DocumentProfile, PagePayload, RawFact


SYSTEM_PROMPT = """You extract grounded financial/operational facts from a PDF page.
Rules:
- Extract only meaningful numerical or semantic facts (revenues, profits, margins, pin codes, gateway counts, share counts, dates of events).
- Skip boilerplate (addresses, registrar details) unless it is a real fact.
- raw_metric: metric name as written. raw_value: verbatim number string WITH units (e.g. '₹48,105.30 Mn', '18,000+').
- raw_unit: unit phrase if present else null. raw_period: period phrase if present else null.
- scope: Consolidated / Standalone / segment name if stated else null.
- exact_quote: VERBATIM sentence or table row from the page text that supports this fact. Copy exactly, including '*' footnotes. Do not paraphrase.
- subject_entity: the company/institution the fact is about.
Return ONLY JSON: {"facts": [ {subject_entity, raw_metric, raw_value, raw_unit, raw_period, scope, exact_quote}, ... ]}. Max 10 facts per page. Empty list if none.
"""


def build_extraction_messages(profile: DocumentProfile, payload: PagePayload) -> list:
    header = (
        f"[DOCUMENT: {profile.detected_title} | ENTITY: {profile.issuing_entity} "
        f"| AUTHORITY: {profile.authority_tier} | EXCERPT_PAGE: {payload.excerpt_page} "
        f"| PRINTED_PAGE: {payload.printed_page or 'unknown'}]"
    )
    body = payload.combined_payload[:8000]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{header}\n\n{body}"},
    ]


def _grounded(quote: str, payload: PagePayload) -> bool:
    """Verbatim substring check (whitespace-normalized)."""
    import re

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip().lower()

    q = norm(quote)
    if len(q) < 15:
        return False
    return q in norm(payload.combined_payload)


def extract_facts_for_page(
    profile: DocumentProfile, payload: PagePayload, client=None, model: str = ""
) -> List[RawFact]:
    from core.config import get_llm_client, GROQ_MODEL

    if client is None:
        client = get_llm_client()
    model = model or GROQ_MODEL
    resp = client.chat.completions.create(
        model=model,
        messages=build_extraction_messages(profile, payload),
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content)
    items = data.get("facts", []) if isinstance(data, dict) else []
    out: List[RawFact] = []
    for it in items[:10]:
        try:
            q = it.get("exact_quote", "")
            if not _grounded(q, payload):
                continue  # drop hallucinations
            out.append(
                RawFact(
                    subject_entity=it.get("subject_entity") or profile.issuing_entity,
                    raw_metric=it["raw_metric"],
                    raw_value=it["raw_value"],
                    raw_unit=it.get("raw_unit"),
                    raw_period=it.get("raw_period"),
                    scope=it.get("scope"),
                    exact_quote=q,
                    excerpt_page=payload.excerpt_page,
                    printed_page=payload.printed_page,
                )
            )
        except Exception:
            continue
    return out


def extract_facts_for_doc(
    profile: DocumentProfile,
    payloads: List[PagePayload],
    max_pages: int = 100,
    progress_cb=None,
) -> List[RawFact]:
    """Run extraction over pages. Caller controls max_pages for large PDFs."""
    from core.config import get_llm_client

    client = get_llm_client()
    all_facts: List[RawFact] = []
    for p in payloads[:max_pages]:
        if not p.combined_payload.strip():
            continue
        try:
            facts = extract_facts_for_page(profile, p, client=client)
            all_facts.extend(facts)
        except Exception as e:
            if progress_cb:
                progress_cb(p.excerpt_page, str(e))
            continue
        if progress_cb:
            progress_cb(p.excerpt_page, f"ok:{len(facts)}")
    return all_facts
