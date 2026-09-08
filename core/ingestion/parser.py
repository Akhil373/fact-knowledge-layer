import re
from typing import List, Optional
import pdfplumber

from core.schemas import PagePayload

# Regex for printed page numbers in header/footer margins
# Matches "Page 216", "216", "iv", "xii", etc.
PRINTED_PAGE_RE = re.compile(r"(?:Page\s*)?(\d+|[ivxlcdmIVXLCDM]+)\b")

# Common header/footer noise to ignore for printed page
IGNORE_NUMBERS = set()  # reserved


def extract_printed_page(page, width: float, height: float) -> Optional[str]:
    """
    Crop top 8% and bottom 8% margins, extract printed page string.
    Returns the last plausible page number found (right-most in footer is usually the real one).
    """
    candidates = []

    for region in ["top", "bottom"]:
        try:
            if region == "top":
                # top 8%
                cropped = page.crop((0, 0, width, height * 0.08))
            else:
                # bottom 8%
                cropped = page.crop((0, height * 0.92, width, height))
            text = cropped.extract_text() or ""
            # Find all matches, take last one (most right / bottom)
            for m in PRINTED_PAGE_RE.finditer(text):
                raw = m.group(1).strip()
                # Heuristic: ignore numbers that look like years (2022, 2024) in header
                # Printed page numbers in these filings are typically small or sequential,
                # but excerpt jumps from 37->94, 120->216 etc so we can't strictly bound.
                # Instead ignore 4-digit years 19xx/20xx when they appear in header context
                if re.fullmatch(r"(19|20)\d{2}", raw):
                    # If text contains "2022" etc as year, skip unless it's the only token
                    # Check if surrounding text has e.g. "March 31, 2024" — then skip
                    continue
                candidates.append(raw)
        except Exception:
            continue

    if candidates:
        # Prefer bottom margin candidates if any, otherwise top
        return candidates[-1]
    return None


def tables_to_markdown(table: list[list]) -> str:
    """
    Convert 2D list from pdfplumber to Markdown table.
    Preserves header rows and handles None/empty cells, footnotes (*).
    """
    if not table or not any(table):
        return ""

    # Clean cells
    cleaned = []
    for row in table:
        cleaned_row = []
        for cell in row:
            if cell is None:
                cleaned_row.append("")
            else:
                # Preserve footnote asterisks, normalize whitespace but keep * markers
                c = str(cell).replace("\n", " ").strip()
                # Collapse multiple spaces but keep markdown pipe safety
                c = re.sub(r"\s+", " ", c)
                c = c.replace("|", "\\|")
                cleaned_row.append(c)
        cleaned.append(cleaned_row)

    # Remove fully empty rows
    cleaned = [r for r in cleaned if any(c.strip() for c in r)]
    if not cleaned:
        return ""

    # Determine header: first row is header
    header = cleaned[0]
    # Pad rows to header width
    max_cols = max(len(r) for r in cleaned)
    # Normalize header length
    if len(header) < max_cols:
        header = header + [""] * (max_cols - len(header))

    md = []
    md.append("| " + " | ".join(header) + " |")
    md.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in cleaned[1:]:
        # Pad row
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        elif len(row) > len(header):
            row = row[: len(header)]
        md.append("| " + " | ".join(row) + " |")

    return "\n".join(md)


def parse_pdf(pdf_path: str) -> List[PagePayload]:
    """
    Table-aware ingestion with dual-page provenance.
    Returns one PagePayload per physical PDF page.
    """
    payloads: List[PagePayload] = []

    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages):
            width, height = page.width, page.height

            # Dual-page provenance
            printed = extract_printed_page(page, width, height)

            # Table extraction
            markdown_tables: list[str] = []
            try:
                tables = page.extract_tables() or []
                for t in tables:
                    md = tables_to_markdown(t)
                    if md:
                        markdown_tables.append(md)
            except Exception:
                pass

            # Non-table text
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""

            # Combined payload: tables first, then text (preserves row/col associations)
            parts = []
            if markdown_tables:
                parts.append(
                    "<!-- TABLES_ON_PAGE {} (printed: {}) -->".format(
                        idx, printed or "unknown"
                    )
                )
                for i, md in enumerate(markdown_tables):
                    parts.append(f"[TABLE {i + 1}]\n{md}")
            if text.strip():
                # Remove table text duplication: keep full text but tables already captured
                parts.append(f"[TEXT]\n{text.strip()}")

            combined = "\n\n".join(parts) if parts else ""

            payloads.append(
                PagePayload(
                    excerpt_page=idx,
                    printed_page=printed,
                    markdown_tables=markdown_tables,
                    text=text.strip(),
                    combined_payload=combined,
                )
            )

    return payloads


def get_page_text_preview(
    payloads: List[PagePayload], page_idx: int, limit: int = 1000
) -> str:
    if 0 <= page_idx < len(payloads):
        return payloads[page_idx].combined_payload[:limit]
    return ""
