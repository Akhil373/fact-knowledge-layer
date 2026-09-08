"""Step 10 — Verification tests for the 4 required cases (no LLM needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.schemas import RawFact
from core.normalization.normalizer import normalize_fact, normalize_period, normalize_numeric
from core.indexing.registry import CanonicalMetricRegistry
from core.indexing.storage import AnchorStore
from core.reconciliation.engine import classify_pair
from core.ingestion.parser import parse_pdf

T1 = "Tier-1 (Audited/Statutory)"
T3 = "Tier-3 (Unaudited/Presentation)"


def mk(metric, val, period, scope="Consolidated", tier=T1, doc="d", reg=None):
    reg = reg or CanonicalMetricRegistry()
    r = RawFact(
        subject_entity="Delhivery Limited", raw_metric=metric, raw_value=val,
        raw_period=period, scope=scope,
        exact_quote="supporting quote text long enough here", excerpt_page=0,
    )
    return normalize_fact(r, reg.resolve(metric, use_llm=False), tier, doc)


def test_normalizer_spec_examples():
    assert normalize_period("Year ended March 31, 2024") == "FY24"
    assert normalize_period("FY 23-24") == "FY24"
    assert normalize_period("FY24") == "FY24"
    assert normalize_period("Quarter ended March 31, 2024") == "Q4_FY24"
    assert normalize_period("Year ended March 31, 2022") == "FY22"
    assert normalize_numeric("₹8,746.2 Mn", "₹ in Millions") == 8746200000.0


def test_case1_corroborated():
    reg = CanonicalMetricRegistry()
    a = mk("Revenue from operations", "₹48,105.30 Mn", "Year ended March 31, 2021", doc="prospectus", reg=reg)
    b = mk("Revenue from operations", "₹48,105.30 Mn", "FY21", doc="annualreport", reg=reg)
    assert classify_pair(a, b).status == "CORROBORATED"


def test_case3_reconciled_by_context():
    reg = CanonicalMetricRegistry()
    a = mk("Revenue from operations", "₹48,105.30 Mn", "Year ended March 31, 2021", doc="prospectus", reg=reg)
    c = mk("Revenue from operations", "₹87,460.0 Mn", "Year ended March 31, 2024", doc="annualreport", reg=reg)
    r = classify_pair(a, c)
    assert r.status == "RECONCILED_BY_CONTEXT"
    assert "PERIOD_MISMATCH" in r.reconciliation_dimensions


def test_case2_genuine_contradiction():
    reg = CanonicalMetricRegistry()
    d = mk("Adjusted EBITDA", "₹500 Mn", "Year ended March 31, 2024", tier=T3, doc="presentation", reg=reg)
    e = mk("Operating profit", "₹100 Mn", "Year ended March 31, 2024", tier=T1, doc="annualreport", reg=reg)
    e.canonical_metric = d.canonical_metric  # simulate registry merge of operating metrics
    r = classify_pair(d, e)
    assert r.status == "GENUINE_CONTRADICTION"
    assert "Tier-3" in r.explanation and "Tier-1" in r.explanation


def test_case4_page_jump_provenance():
    payloads = parse_pdf("starter-datasets/delhivery/01-delhivery-prospectus-2022-excerpt.pdf")
    assert len(payloads) == 100
    printed = [p.printed_page for p in payloads]
    # Excerpt jumps (e.g. 33 -> 90 per README retained sections)
    assert "33" in printed and "90" in printed
    # Dual provenance tracked on every page
    assert all(hasattr(p, "excerpt_page") and hasattr(p, "printed_page") for p in payloads)
    # Footnote asterisks preserved in tables somewhere in prospectus
    assert any("*" in t for p in payloads for t in p.markdown_tables)


def test_incremental_no_reprocess():
    import tempfile, os
    from core.schemas import DocumentProfile
    tmp = tempfile.mktemp(suffix=".json")
    store = AnchorStore(persist_path=tmp)
    reg = CanonicalMetricRegistry()
    p = DocumentProfile(detected_title="T", issuing_entity="E", doc_type="Annual Report", authority_tier=T1)
    f1 = mk("M", "100", "FY24", doc="docA", reg=reg)
    store.add_document("docA", p, [f1])
    n_before = store.stats()["num_facts"]
    f2 = mk("M", "200", "FY24", doc="docB", reg=reg)
    store.add_document("docB", p, [f2])
    assert store.stats()["num_facts"] == n_before + 1
    assert len(store.buckets_with_multi_docs()) == 1
    os.remove(tmp)


if __name__ == "__main__":
    test_normalizer_spec_examples()
    test_case1_corroborated()
    test_case3_reconciled_by_context()
    test_case2_genuine_contradiction()
    test_case4_page_jump_provenance()
    test_incremental_no_reprocess()
    print("ALL VERIFICATION TESTS PASSED")
