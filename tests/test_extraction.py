from decimal import Decimal
from hashlib import sha256
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from tallyguard.api import create_app
from tallyguard.evidence import EvidenceType, ExtractionMethod
from tallyguard.extraction import DocumentExtractionError, extract_pdf_text_fields


def pdf_with_text(lines: list[str]) -> bytes:
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
            operations.append("0 -18 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        operations.append(f"({escaped}) Tj")
    operations.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(operations).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def invoice_pdf(*extra_lines: str) -> bytes:
    return pdf_with_text(
        [
            "Invoice ID: invoice-pdf-1",
            "Vendor ID: vendor-pdf",
            "Invoice Number: PDF-2026-001",
            "Currency: USDC",
            "Invoice Total: $1,486.25 USDC",
            "Due Date: 10/08/2026",
            "Payment Wallet: 0x3333333333333333333333333333333333333333",
            *extra_lines,
        ]
    )


def test_text_pdf_extraction_is_complete_normalized_and_page_bound():
    fields = extract_pdf_text_fields(
        document_id="document-pdf",
        evidence_type=EvidenceType.INVOICE,
        content=invoice_pdf(),
    )

    values = {field.name: field.normalized_value for field in fields}
    assert values == {
        "invoice_id": "invoice-pdf-1",
        "vendor_id": "vendor-pdf",
        "invoice_number": "PDF-2026-001",
        "currency": "USDC",
        "amount": "1486.25",
        "due_date": "2026-10-08",
        "payment_wallet_address": "0x3333333333333333333333333333333333333333",
    }
    assert all(field.method == ExtractionMethod.PDF_TEXT for field in fields)
    assert all(field.confidence == Decimal("0.98") for field in fields)
    assert all(field.source.page_number == 1 for field in fields)
    assert all(field.source.document_id == "document-pdf" for field in fields)


@pytest.mark.parametrize(
    ("evidence_type", "lines", "expected"),
    (
        (
            EvidenceType.PURCHASE_ORDER,
            [
                "Purchase Order ID: po-pdf-1",
                "Supplier ID: vendor-pdf",
                "PO Number: PO-2026-001",
                "Currency: USDC",
                "Approved Amount: 1,500.00 USDC",
            ],
            {
                "purchase_order_id": "po-pdf-1",
                "vendor_id": "vendor-pdf",
                "po_number": "PO-2026-001",
                "currency": "USDC",
                "authorized_amount": "1500.00",
            },
        ),
        (
            EvidenceType.DELIVERY,
            [
                "Acceptance ID: delivery-pdf-1",
                "PO ID: po-pdf-1",
                "Accepted Value: $1,486.25",
            ],
            {
                "delivery_id": "delivery-pdf-1",
                "purchase_order_id": "po-pdf-1",
                "delivered_value": "1486.25",
            },
        ),
    ),
)
def test_text_pdf_extraction_supports_all_evidence_types(
    evidence_type: EvidenceType,
    lines: list[str],
    expected: dict[str, str],
):
    fields = extract_pdf_text_fields(
        document_id=f"document-{evidence_type.value.lower()}",
        evidence_type=evidence_type,
        content=pdf_with_text(lines),
    )

    assert {field.name: field.normalized_value for field in fields} == expected


def test_text_pdf_extraction_rejects_missing_and_conflicting_fields():
    blank = pdf_with_text(["This document has no labelled accounts-payable fields."])
    with pytest.raises(DocumentExtractionError, match="missing required labelled fields"):
        extract_pdf_text_fields(
            document_id="blank",
            evidence_type=EvidenceType.INVOICE,
            content=blank,
        )

    with pytest.raises(DocumentExtractionError, match="conflicting values for amount"):
        extract_pdf_text_fields(
            document_id="conflict",
            evidence_type=EvidenceType.INVOICE,
            content=invoice_pdf("Amount: 999.00 USDC"),
        )


def test_authenticated_pdf_preview_returns_hash_without_persisting(tmp_path):
    app = create_app(database_path=tmp_path / "pdf-preview.sqlite3", testing=True)
    client = app.test_client()
    token = client.post("/api/demo/session", json={"role": "operator"}).get_json()[
        "access_token"
    ]
    content = invoice_pdf()

    response = client.post(
        "/api/evidence/extract",
        data={
            "evidence_type": "INVOICE",
            "file": (BytesIO(content), "invoice.pdf", "application/pdf"),
        },
        content_type="multipart/form-data",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Correlation-ID": "pdf-preview",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["persisted"] is False
    assert payload["preview"]["content_sha256"] == sha256(content).hexdigest()
    assert payload["preview"]["mime_type"] == "application/pdf"
    assert {field["name"] for field in payload["preview"]["fields"]} == {
        "invoice_id",
        "vendor_id",
        "invoice_number",
        "currency",
        "amount",
        "due_date",
        "payment_wallet_address",
    }
    assert all(
        field["method"] == "PDF_TEXT" and field["source"]["page_number"] == 1
        for field in payload["preview"]["fields"]
    )
    assert app.extensions["tallyguard_repository"].list_invoice_evidence(
        organization_id="demo-org",
        invoice_id="invoice-pdf-1",
    ) == ()
