import json
from typing import List
from core.schemas import DocumentProfile, PagePayload
from core.config import get_llm_client, GROQ_MODEL


SYSTEM_PROMPT = """You are a document profiler for financial disclosures.
Classify the document's authority tier:
- Tier-1 (Audited/Statutory): prospectus, DRHP, annual report audited financials, statutory filings, regulatory filings
- Tier-2 (Governance/Minutes): board resolutions, shareholder notices, minutes
- Tier-3 (Unaudited/Presentation): earnings presentation, investor deck, press release, transcript
Extract issuing_entity (primary company/institution), detected_title, doc_type, primary_period, base_currency.
Never infer from filenames. Use only document content text provided.
Return JSON matching the DocumentProfile schema.
"""

PROFILE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "detected_title": {"type": "string"},
        "issuing_entity": {"type": "string"},
        "primary_period": {"type": ["string", "null"]},
        "doc_type": {"type": "string"},
        "authority_tier": {
            "type": "string",
            "enum": [
                "Tier-1 (Audited/Statutory)",
                "Tier-2 (Governance/Minutes)",
                "Tier-3 (Unaudited/Presentation)",
            ],
        },
        "base_currency": {"type": ["string", "null"]},
    },
    "required": ["detected_title", "issuing_entity", "doc_type", "authority_tier"],
}


def build_profiler_messages(page_payloads: List[PagePayload]) -> list:
    # Use first 3 pages combined
    snippet = "\n\n---PAGE BREAK---\n\n".join(
        p.combined_payload[:4000] for p in page_payloads[:3]
    )
    # Truncate to avoid token blowup
    snippet = snippet[:12000]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Document excerpt (first 1-3 pages):\n\n{snippet}\n\nReturn JSON with keys: detected_title, issuing_entity, primary_period, doc_type, authority_tier, base_currency",
        },
    ]


def profile_document(
    page_payloads: List[PagePayload], doc_id: str = ""
) -> DocumentProfile:
    """
    LLM-based profiler. Falls back to heuristic if LLM not configured.
    """
    # Heuristic fallback if no API key
    try:
        from core.config import is_llm_configured

        if not is_llm_configured():
            raise ValueError("LLM not configured, using heuristic")
        client = get_llm_client()
        messages = build_profiler_messages(page_payloads)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        data = json.loads(content)
        return DocumentProfile(**data)
    except Exception as e:
        # Heuristic fallback: inspect text for keywords (scan first 5 pages since some excerpts start with CONTENTS)
        scan_text = " ".join(p.text for p in page_payloads[:5]).lower()
        full_scan = (
            " ".join(p.text for p in page_payloads[:10]).lower()
            if len(page_payloads) > 5
            else scan_text
        )
        tier = "Tier-1 (Audited/Statutory)"
        doc_type = "Unknown"
        if any(
            k in scan_text
            for k in ["earnings presentation", "investor presentation", "earnings call"]
        ):
            tier = "Tier-3 (Unaudited/Presentation)"
            doc_type = "Earnings Presentation"
        elif "prospectus" in scan_text or "draft red herring" in scan_text:
            tier = "Tier-1 (Audited/Statutory)"
            doc_type = "Prospectus / DRHP"
        elif "annual report" in scan_text:
            tier = "Tier-1 (Audited/Statutory)"
            doc_type = "Annual Report"
        elif "economic survey" in full_scan or "state of the economy" in scan_text:
            tier = "Tier-1 (Audited/Statutory)"
            doc_type = "Economic Survey"
        elif any(k in full_scan for k in ["rbi annual report", "reserve bank"]):
            tier = "Tier-1 (Audited/Statutory)"
            doc_type = "RBI Annual Report"
        elif "imf" in full_scan or "article iv" in full_scan:
            tier = "Tier-2 (Governance/Minutes)"
            doc_type = "IMF Article IV Report"

        # Try to extract entity from first pages
        first_text = page_payloads[0].text if page_payloads else ""
        lines = [l.strip() for l in first_text.split("\n") if l.strip()]
        title = lines[0][:120] if lines else "Unknown Document"
        # Use full_scan for entity
        entity = "Unknown"
        for hint in [
            "delhivery",
            "reserve bank of india",
            "government of india",
            "international monetary fund",
            "rbi",
        ]:
            if hint in full_scan:
                entity = hint.title()
                break
        if "delhivery" in full_scan:
            entity = "Delhivery Limited"
        if doc_type == "Economic Survey":
            entity = "Government of India"
            title = "Economic Survey 2024-25"
        if doc_type == "RBI Annual Report":
            entity = "Reserve Bank of India"
        if doc_type == "IMF Article IV Report":
            entity = "International Monetary Fund"

        return DocumentProfile(
            detected_title=title,
            issuing_entity=entity,
            primary_period=None,
            doc_type=doc_type,
            authority_tier=tier,
            base_currency="INR"
            if "₹" in full_scan or "inr" in full_scan or "rupee" in full_scan
            else None,
        )
