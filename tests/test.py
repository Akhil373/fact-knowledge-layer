import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ingestion.parser import parse_pdf


payloads = parse_pdf(
    "/home/axle/code/python/superjoin/starter-datasets/delhivery/01-delhivery-prospectus-2022-excerpt.pdf"
)

with open("./logs.md", "w", encoding="utf-8") as f:
    # Write header
    f.write("# Payload Log\n\n")
    f.write("**Total Payloads:** {len(payloads)}\n\n")
    f.write("---\n\n")

    for idx, payload in enumerate(payloads, 1):
        f.write("## Payload #{idx} - Page {payload.excerpt_page}\n\n")

        # Metadata
        f.write(f"**Printed Page:** {payload.printed_page or 'N/A'}\n\n")
        f.write(f"**Tables Count:** {len(payload.markdown_tables)}\n\n")

        # Tables
        if payload.markdown_tables:
            f.write("### Tables\n\n")
            for table_idx, table in enumerate(payload.markdown_tables, 1):
                f.write(f"**Table {table_idx}:**\n\n")
                f.write(f"{table}\n\n")

        # Text
        f.write("### Text\n\n")
        f.write("{payload.text}\n\n")

        # Combined payload
        f.write("### Combined Payload\n\n")
        f.write("```\n{payload.combined_payload}\n```\n\n")

        f.write("---\n\n")
