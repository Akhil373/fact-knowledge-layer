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


def _extract_json(content: str) -> dict:
    """Parse JSON even if wrapped in ```json``` fences (needed when response_format not used)."""
    import re

    try:
        return json.loads(content)
    except Exception:
        pass
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


def extract_facts_for_page(
    profile: DocumentProfile, payload: PagePayload, client=None, model: str = "", max_retries: int = 2
) -> List[RawFact]:
    from core.config import get_llm_client, GROQ_MODEL, chat_extra_body
    import time

    if client is None:
        client = get_llm_client()
    model = model or GROQ_MODEL
    extra = chat_extra_body()
    last_err = None
    for attempt in range(max_retries + 1):
        # First try with response_format (Groq), fallback without for Bynara/muse-spark which rejects it
        try_with_format = attempt == 0
        try:
            kwargs = dict(
                model=model,
                messages=build_extraction_messages(profile, payload),
                temperature=0,
                timeout=20,
            )
            if try_with_format:
                kwargs["response_format"] = {"type": "json_object"}
            if extra:
                kwargs["extra_body"] = extra
            resp = client.chat.completions.create(**kwargs)
            break
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            is_invalid = "invalid" in msg or "response_format" in msg
            is_429 = "429" in msg or "rate_limit" in msg
            # If first attempt failed due to invalid (e.g. Bynara rejecting response_format), retry without it
            if is_invalid and try_with_format and attempt == 0:
                continue
            if attempt < max_retries:
                time.sleep(5 if is_429 else 2)
                continue
            raise last_err
    data = _extract_json(resp.choices[0].message.content)
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


def _sample_payloads(payloads: List[PagePayload], max_pages: int) -> List[PagePayload]:
    """Table-aware stratified sampling: prioritize table-heavy pages, then cover jumps."""
    if len(payloads) <= max_pages:
        return payloads
    # Score pages: table count primary, text length secondary (financial tables are dense)
    scored = sorted(
        enumerate(payloads),
        key=lambda x: (len(x[1].markdown_tables), len(x[1].combined_payload)),
        reverse=True,
    )
    # Keep top table-heavy pages plus first 2 for profiler context
    top_n = max_pages - 2
    top_indices = set(idx for idx, _ in scored[:top_n])
    # Always include first 2 pages for document header context
    top_indices.update([0, 1])
    # Return in original order, truncated to max_pages
    selected = [payloads[i] for i in sorted(top_indices)[:max_pages]]
    return selected


def extract_facts_for_doc(
    profile: DocumentProfile,
    payloads: List[PagePayload],
    max_pages: int = 100,
    progress_cb=None,
) -> List[RawFact]:
    """Run extraction over pages. Uses stratified sampling when max_pages < total."""
    from core.config import get_llm_client

    client = get_llm_client()
    sampled = _sample_payloads(payloads, max_pages)
    all_facts: List[RawFact] = []
    for p in sampled:
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
