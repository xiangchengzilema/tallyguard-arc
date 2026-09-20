"""Generate the public, non-sensitive PDF evidence samples used by the judge UI."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIRECTORY = ROOT / "web" / "public" / "samples" / "evidence"

SAMPLE_DOCUMENTS = {
    "invoice.pdf": (
        "TallyGuard sample invoice",
        "Invoice ID: invoice-atlas-pdf-001",
        "Vendor ID: vendor-atlas-compute",
        "Invoice Number: ATLAS-PDF-2026-001",
        "Currency: USDC",
        "Invoice Total: 1,486.25 USDC",
        "Due Date: 2026-10-08",
        "Payment Wallet: 0x3333333333333333333333333333333333333333",
    ),
    "purchase-order.pdf": (
        "TallyGuard sample purchase order",
        "Purchase Order ID: po-atlas-pdf-001",
        "Vendor ID: vendor-atlas-compute",
        "PO Number: PO-ATLAS-PDF-001",
        "Currency: USDC",
        "Authorized Amount: 1,500.00 USDC",
    ),
    "delivery.pdf": (
        "TallyGuard sample delivery acceptance",
        "Delivery ID: delivery-atlas-pdf-001",
        "Purchase Order ID: po-atlas-pdf-001",
        "Delivered Value: 1,486.25 USDC",
    ),
}


def _render_pdf(lines: tuple[str, ...]) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    operations = ["BT", "/F1 11 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            operations.append("0 -22 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        operations.append(f"({escaped}) Tj")
    operations.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(operations).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def main() -> None:
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for filename, lines in SAMPLE_DOCUMENTS.items():
        (OUTPUT_DIRECTORY / filename).write_bytes(_render_pdf(lines))


if __name__ == "__main__":
    main()
