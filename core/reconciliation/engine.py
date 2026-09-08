"""Step 8 — Reconciliation Engine (tri-state NLI, deterministic).

Per pipeline.md § Stage 7:
1. Equivalence: |VA-VB|/max(VA,VB) <= 0.015 ; semantic fallback for non-numeric.
2. Dimensions: period_match, scope_match, unit_match.
3. Decision:
   - CORROBORATED: equiv AND period_match
   - RECONCILED_BY_CONTEXT: not equiv AND (not period/scope/unit match)
   - GENUINE_CONTRADICTION: not equiv AND all match (+ tier note)
"""

from itertools import combinations
from typing import List

from core.schemas import NormalizedFact, ReconciliationResult

TOLERANCE = 0.015


def numeric_equivalent(a: NormalizedFact, b: NormalizedFact) -> bool:
    va, vb = a.normalized_numeric_value, b.normalized_numeric_value
    if va is None or vb is None:
        return False
    if va == 0 and vb == 0:
        return True
    denom = max(abs(va), abs(vb))
    if denom == 0:
        return False
    return abs(va - vb) / denom <= TOLERANCE


def semantic_equivalent(a: NormalizedFact, b: NormalizedFact) -> bool:
    """Fallback for non-numeric/categorical claims: normalized raw_value match."""
    va = (a.raw.raw_value or "").strip().lower()
    vb = (b.raw.raw_value or "").strip().lower()
    if not va or not vb:
        return False
    return va == vb


def equivalent(a: NormalizedFact, b: NormalizedFact) -> bool:
    if (
        a.normalized_numeric_value is not None
        and b.normalized_numeric_value is not None
    ):
        return numeric_equivalent(a, b)
    return semantic_equivalent(a, b)


def classify_pair(a: NormalizedFact, b: NormalizedFact) -> ReconciliationResult:
    equiv = equivalent(a, b)
    period_match = a.normalized_period == b.normalized_period
    scope_match = a.normalized_scope == b.normalized_scope
    # unit = currency; UNKNOWN currency on both sides counts as match
    unit_match = a.normalized_currency == b.normalized_currency

    dims: List[str] = []
    if not period_match:
        dims.append("PERIOD_MISMATCH")
    if not scope_match:
        dims.append("SCOPE_MISMATCH")
    if not unit_match:
        dims.append("UNIT_MISMATCH")
    if a.authority_tier != b.authority_tier:
        dims.append("TIER_DISCREPANCY")

    if equiv and period_match:
        status = "CORROBORATED"
        conf = 0.9
        expl = (
            f"Values match within {TOLERANCE * 100:.1f}% tolerance "
            f"({a.normalized_numeric_value} vs {b.normalized_numeric_value}) "
            f"for same period {a.normalized_period}. Confirmed across documents."
        )
    elif not equiv and (not period_match or not scope_match or not unit_match):
        status = "RECONCILED_BY_CONTEXT"
        conf = 0.8
        expl = (
            f"Values differ ({a.normalized_numeric_value} vs {b.normalized_numeric_value}) "
            f"but explained by {', '.join(dims) if dims else 'context'}: "
            f"period {a.normalized_period} vs {b.normalized_period}, "
            f"scope {a.normalized_scope} vs {b.normalized_scope}, "
            f"currency {a.normalized_currency} vs {b.normalized_currency}."
        )
    else:
        status = "GENUINE_CONTRADICTION"
        conf = 0.85
        expl = (
            f"Direct conflict under identical parameters "
            f"(period {a.normalized_period}, scope {a.normalized_scope}, "
            f"currency {a.normalized_currency}): "
            f"{a.normalized_numeric_value} vs {b.normalized_numeric_value}."
        )
        if a.authority_tier != b.authority_tier:
            expl += f" Authority divergence: {a.authority_tier} vs {b.authority_tier}."
            conf = 0.75

    return ReconciliationResult(
        canonical_metric=a.canonical_metric,
        fact_a=a,
        fact_b=b,
        status=status,  # type: ignore
        confidence_score=conf,
        reconciliation_dimensions=dims,
        explanation=expl,
    )


def reconcile_bucket(facts: List[NormalizedFact]) -> List[ReconciliationResult]:
    """Pairwise over facts from distinct docs only."""
    out = []
    for a, b in combinations(facts, 2):
        if a.source_doc_id == b.source_doc_id:
            continue
        out.append(classify_pair(a, b))
    return out


def reconcile_all(buckets: dict) -> List[ReconciliationResult]:
    out = []
    for facts in buckets.values():
        out.extend(reconcile_bucket(facts))
    return out
