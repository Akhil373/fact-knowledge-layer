from pydantic import BaseModel, Field
from typing import Optional, Literal


class DocumentProfile(BaseModel):
    detected_title: str = Field(description="Title extracted from cover/header")
    issuing_entity: str = Field(description="Primary company/institution, e.g., 'Delhivery Limited'")
    primary_period: Optional[str] = Field(default=None, description="Main reporting period if global, e.g., 'FY 2023-24'")
    doc_type: str = Field(description="e.g., 'Prospectus / DRHP', 'Annual Report', 'Earnings Presentation'")
    authority_tier: Literal["Tier-1 (Audited/Statutory)", "Tier-2 (Governance/Minutes)", "Tier-3 (Unaudited/Presentation)"]
    base_currency: Optional[str] = Field(default=None, description="Inferred dominant currency, e.g., 'INR', 'USD'")


class RawFact(BaseModel):
    subject_entity: str = Field(description="Entity the fact is about, e.g., 'Delhivery Limited'")
    raw_metric: str = Field(description="Metric as written, e.g., 'Revenue from operations'")
    raw_value: str = Field(description="Verbatim string representation, e.g., '₹8,746.2 Mn'")
    raw_unit: Optional[str] = Field(default=None, description="Extracted unit, e.g., '₹ in Millions', 'Pin codes'")
    raw_period: Optional[str] = Field(default=None, description="Extracted period, e.g., 'Year ended March 31, 2024'")
    scope: Optional[str] = Field(default=None, description="Consolidated, Standalone, or Segment name (e.g., Express Parcel)")
    exact_quote: str = Field(description="Verbatim text sentence or table row supporting this fact")
    excerpt_page: int = Field(description="0-indexed physical page in PDF")
    printed_page: Optional[str] = Field(default=None, description="Visual/printed page number extracted from margins")


class NormalizedFact(BaseModel):
    raw: RawFact
    canonical_metric: str
    normalized_numeric_value: Optional[float] = None
    normalized_currency: Optional[str] = None
    normalized_period: str
    normalized_scope: str
    authority_tier: str
    source_doc_id: str


class ReconciliationResult(BaseModel):
    canonical_metric: str
    fact_a: NormalizedFact
    fact_b: NormalizedFact
    status: Literal["CORROBORATED", "GENUINE_CONTRADICTION", "RECONCILED_BY_CONTEXT"]
    confidence_score: float
    reconciliation_dimensions: list[str]
    explanation: str


# Internal: page payload produced by parser

class PagePayload(BaseModel):
    excerpt_page: int
    printed_page: Optional[str] = None
    markdown_tables: list[str] = Field(default_factory=list)
    text: str
    combined_payload: str  # markdown + text interleaved
