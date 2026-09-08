"""Step 4 — Deterministic Normalizer (no LLM).

Pure Python regex/math per pipeline.md § Stage 4:
- Temporal: "Year ended March 31, 2024" / "FY 23-24" / "FY24" -> "FY24"
            "Quarter ended March 31, 2024" / "Q4 FY24" -> "Q4_FY24"
- Numerical: Cr x1e7, Mn x1e6, Lakh x1e5, Bn x1e9; currency symbols
- Scope: CONSOLIDATED / STANDALONE / segment token
"""
import re
from typing import Optional, Tuple

# ---------- Temporal ----------

_MONTH_TO_Q = {
    "january": "Q4", "february": "Q4", "march": "Q4",
    "april": "Q1", "may": "Q1", "june": "Q1",
    "july": "Q2", "august": "Q2", "september": "Q2",
    "october": "Q3", "november": "Q3", "december": "Q3",
}
# India FY: year ending March Y => FY(Y-1 - Y), token FY + last2(Y)
# e.g. March 31, 2024 -> FY24 ; March 31, 2022 -> FY22

_FY_TOKEN_RE = re.compile(r"\bFY\s?(\d{2})(?:\s?[-–/]\s?(\d{2,4}))?\b", re.I)
_FY_LONG_RE = re.compile(r"\bFY\s?(\d{4})\s?[-–/]\s?(\d{2,4})\b", re.I)
_YEAR_ENDED_RE = re.compile(
    r"(year|financial year|fy).*?ended.*?march\s+31[,\s]+(\d{4})", re.I
)
_QUARTER_ENDED_RE = re.compile(
    r"(quarter|three months|q[1-4]).*?ended.*?(\bjanuary\b|\bfebruary\b|\bmarch\b|\bapril\b|\bmay\b|\bjune\b|\bjuly\b|\baugust\b|\bseptember\b|\boctober\b|\bnovember\b|\bdecember\b)[\s,]+.*?(\d{4})",
    re.I,
)
_QTOKEN_RE = re.compile(r"\bQ([1-4])\s*FY\s?(\d{2,4})\b", re.I)
_NINE_MONTHS_RE = re.compile(r"nine\s+months.*?ended.*?december\s+31[,\s]+(\d{4})", re.I)
_HALF_YEAR_RE = re.compile(r"half\s*year.*?ended.*?september\s+30[,\s]+(\d{4})", re.I)
_CALENDAR_YEAR_RE = re.compile(r"\bcalendar\s+year\s+(\d{4})\b", re.I)
_YEAR_ONLY_RE = re.compile(r"\b(19|20)\d{2}\b")


def _fy_token(year_ending_march: int) -> str:
    return f"FY{str(year_ending_march)[-2:]}"


def normalize_period(raw_period: Optional[str]) -> str:
    """Deterministic period -> token. Returns 'UNKNOWN' if unparseable."""
    if not raw_period or not raw_period.strip():
        return "UNKNOWN"
    s = raw_period.strip()

    # Explicit Q token: "Q4 FY24" / "Q4 FY2024"
    m = _QTOKEN_RE.search(s)
    if m:
        q, yy = m.group(1), m.group(2)
        yy = yy[-2:] if len(yy) == 4 else yy.zfill(2)
        return f"Q{q}_FY{yy}"

    # Quarter ended March 31, 2024 -> Q4_FY24 (India Q mapping)
    m = _QUARTER_ENDED_RE.search(s)
    if m:
        month = m.group(2).lower()
        year = int(m.group(3))
        q = _MONTH_TO_Q.get(month, "Q4")
        # FY year: if quarter is Q4 (Jan-Mar), FY ending that calendar year
        # Q1-Q3 belong to FY ending next calendar year
        fy_year = year if q == "Q4" else year + 1
        return f"{q}_{_fy_token(fy_year)}"

    # Nine months ended Dec 31, 2021 -> 9M_FY22
    m = _NINE_MONTHS_RE.search(s)
    if m:
        year = int(m.group(1))
        return f"9M_{_fy_token(year + 1)}"

    # Half year ended Sep 30 -> H1_FYxx
    m = _HALF_YEAR_RE.search(s)
    if m:
        year = int(m.group(1))
        return f"H1_{_fy_token(year + 1)}"

    # Year ended March 31, 2024 -> FY24
    m = _YEAR_ENDED_RE.search(s)
    if m:
        return _fy_token(int(m.group(2)))

    # FY 2023-24 / FY23-24 / FY 23-24 (long form first)
    m = _FY_LONG_RE.search(s)
    if m:
        second = m.group(2)
        yy = second[-2:]
        return f"FY{yy}"
    m = _FY_TOKEN_RE.search(s)
    if m:
        first, second = m.group(1), m.group(2)
        if second:
            yy = second[-2:]
            return f"FY{yy}"
        return f"FY{first}"

    # Calendar year 2024 -> CY2024
    m = _CALENDAR_YEAR_RE.search(s)
    if m:
        return f"CY{m.group(1)}"

    # Fallback: bare "March 31, 2024" without "year ended" — assume FY
    m = re.search(r"march\s+31[,\s]+(\d{4})", s, re.I)
    if m:
        return _fy_token(int(m.group(1)))

    return "UNKNOWN"


# ---------- Numerical & Unit ----------

_CURRENCY_MAP = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupee": "INR", "rupees": "INR",
    "$": "USD", "usd": "USD", "dollar": "USD",
    "€": "EUR", "eur": "EUR",
    "£": "GBP", "gbp": "GBP",
}

_MULTIPLIERS = [
    (re.compile(r"\bcrores?\b", re.I), 1e7),
    (re.compile(r"\bcr\b", re.I), 1e7),
    (re.compile(r"\bbillions?\b", re.I), 1e9),
    (re.compile(r"\bbn\b", re.I), 1e9),
    (re.compile(r"\bmillions?\b", re.I), 1e6),
    (re.compile(r"\bmn\b", re.I), 1e6),
    (re.compile(r"\blakhs?\b", re.I), 1e5),
    (re.compile(r"\blacs?\b", re.I), 1e5),
    (re.compile(r"\bthousands?\b", re.I), 1e3),
    (re.compile(r"\bk\b", re.I), 1e3),  # careful: matches bare 'k' — checked last
]

_NUMBER_RE = re.compile(r"\(?([+-]?[\d,]+(?:\.\d+)?)\)?")


def normalize_currency(raw_value: str = "", raw_unit: Optional[str] = None) -> Optional[str]:
    blob = f"{raw_value or ''} {raw_unit or ''}".lower()
    # Symbol check first (₹, $, €, £ survive .lower())
    raw_blob = f"{raw_value or ''} {raw_unit or ''}"
    for sym in ["₹", "$", "€", "£"]:
        if sym in raw_blob:
            return _CURRENCY_MAP[sym]
    for key, iso in _CURRENCY_MAP.items():
        if len(key) > 1 and key in blob:
            return iso
    return None


def _detect_multiplier(raw_value: str = "", raw_unit: Optional[str] = None) -> float:
    blob = f"{raw_value or ''} {raw_unit or ''}"
    for rx, mult in _MULTIPLIERS:
        if rx.search(blob):
            return mult
    return 1.0


def normalize_numeric(raw_value: str = "", raw_unit: Optional[str] = None) -> Optional[float]:
    """Parse number x multiplier. Handles (1,234.5) negatives, commas."""
    if not raw_value:
        return None
    # Non-numeric values (addresses, names) -> None
    m = _NUMBER_RE.search(raw_value.replace("₹", "").replace(",", ""))
    # re-run on original with commas for precision
    m = _NUMBER_RE.search(raw_value)
    if not m:
        return None
    num_s = m.group(1).replace(",", "")
    try:
        num = float(num_s)
    except ValueError:
        return None
    # Parentheses => negative (accounting)
    if raw_value.strip().startswith("(") and raw_value.strip().endswith(")"):
        num = -abs(num)
    return num * _detect_multiplier(raw_value, raw_unit)


# ---------- Scope ----------

def normalize_scope(scope: Optional[str]) -> str:
    if not scope or not scope.strip():
        return "UNKNOWN"
    s = scope.strip().lower()
    if "consol" in s:
        return "CONSOLIDATED"
    if "standalone" in s or "stand-alone" in s:
        return "STANDALONE"
    # Segment token: uppercase, spaces->underscores
    tok = re.sub(r"[^a-z0-9]+", "_", s).strip("_").upper()
    return tok if tok else "UNKNOWN"


# ---------- Combined ----------

def normalize_fact(
    raw,  # RawFact
    canonical_metric: str,
    authority_tier: str,
    source_doc_id: str,
):
    """Build NormalizedFact from RawFact + registry/authority context."""
    from core.schemas import NormalizedFact

    return NormalizedFact(
        raw=raw,
        canonical_metric=canonical_metric,
        normalized_numeric_value=normalize_numeric(raw.raw_value, raw.raw_unit),
        normalized_currency=normalize_currency(raw.raw_value, raw.raw_unit),
        normalized_period=normalize_period(raw.raw_period),
        normalized_scope=normalize_scope(raw.scope),
        authority_tier=authority_tier,
        source_doc_id=source_doc_id,
    )
