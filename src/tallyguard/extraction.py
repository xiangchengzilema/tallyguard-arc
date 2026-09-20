"""Fail-closed extraction for finance documents with an embedded PDF text layer.

This is intentionally not OCR.  It accepts only labelled fields recovered by
``pypdf`` from the original bytes, records the source page, rejects conflicting
values, and leaves scanned/image documents for a separately configured OCR
adapter or human provenance entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .evidence import EvidenceType, ExtractedField, ExtractionMethod, SourceLocation


class DocumentExtractionError(ValueError):
    """Raised when source bytes cannot yield a complete, unambiguous field set."""


@dataclass(frozen=True, slots=True)
class FieldRule:
    name: str
    labels: tuple[str, ...]
    value_kind: str = "text"


FIELD_RULES: dict[EvidenceType, tuple[FieldRule, ...]] = {
    EvidenceType.INVOICE: (
        FieldRule("invoice_id", ("invoice id", "invoice record id")),
        FieldRule("vendor_id", ("vendor id", "supplier id")),
        FieldRule("invoice_number", ("invoice number", "invoice no", "invoice #")),
        FieldRule("currency", ("currency",), "currency"),
        FieldRule("amount", ("amount", "invoice total", "total due"), "amount"),
        FieldRule("due_date", ("due date", "payment due"), "date"),
        FieldRule(
            "payment_wallet_address",
            ("payment wallet address", "payment wallet", "wallet address"),
            "wallet",
        ),
    ),
    EvidenceType.PURCHASE_ORDER: (
        FieldRule("purchase_order_id", ("purchase order id", "po id")),
        FieldRule("vendor_id", ("vendor id", "supplier id")),
        FieldRule("po_number", ("purchase order number", "po number", "po no", "po #")),
        FieldRule("currency", ("currency",), "currency"),
        FieldRule(
            "authorized_amount",
            ("authorized amount", "approved amount", "po total"),
            "amount",
        ),
    ),
    EvidenceType.DELIVERY: (
        FieldRule("delivery_id", ("delivery id", "receipt id", "acceptance id")),
        FieldRule("purchase_order_id", ("purchase order id", "po id")),
        FieldRule(
            "delivered_value",
            ("delivered value", "accepted value", "delivery total"),
            "amount",
        ),
    ),
}

MAX_PDF_PAGES = 20
MAX_EXTRACTED_CHARACTERS = 200_000


def _normalized_label_pattern(label: str) -> str:
    words = [re.escape(word) for word in re.split(r"[\s_-]+", label.strip()) if word]
    return r"[\s_-]*".join(words)


def _normalize_value(raw: str, kind: str) -> str:
    value = raw.strip().strip("|;")
    if kind == "text":
        if not value:
            raise DocumentExtractionError("Extracted text field is empty.")
        return value
    if kind == "currency":
        currency = value.upper().strip()
        if not re.fullmatch(r"[A-Z]{3,10}", currency):
            raise DocumentExtractionError(f"Unsupported currency value: {value}")
        return currency
    if kind == "wallet":
        wallet = value.lower()
        if not re.fullmatch(r"0x[a-f0-9]{40}", wallet):
            raise DocumentExtractionError("Payment wallet must be a 20-byte EVM address.")
        return wallet
    if kind == "amount":
        compact = re.sub(r"(?i)\b(?:USDC|USD|EUR|GBP)\b", "", value)
        compact = compact.replace(",", "").replace("$", "").strip()
        try:
            amount = Decimal(compact)
        except InvalidOperation as exc:
            raise DocumentExtractionError(f"Invalid extracted amount: {value}") from exc
        if amount <= 0:
            raise DocumentExtractionError("Extracted amount must be positive.")
        return format(amount, "f")
    if kind == "date":
        candidate = value.strip()
        for date_format in (None, "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                parsed = (
                    date.fromisoformat(candidate)
                    if date_format is None
                    else datetime.strptime(candidate, date_format).date()
                )
                return parsed.isoformat()
            except ValueError:
                continue
        raise DocumentExtractionError(f"Invalid extracted date: {value}")
    raise DocumentExtractionError(f"Unsupported extraction value kind: {kind}")


def extract_pdf_text_fields(
    *,
    document_id: str,
    evidence_type: EvidenceType,
    content: bytes,
) -> tuple[ExtractedField, ...]:
    """Extract a complete labelled field set from an embedded PDF text layer."""

    try:
        reader = PdfReader(BytesIO(content), strict=True)
    except (PdfReadError, OSError, ValueError) as exc:
        raise DocumentExtractionError("PDF could not be parsed safely.") from exc
    if reader.is_encrypted:
        raise DocumentExtractionError("Encrypted PDF evidence is not supported.")
    if not reader.pages:
        raise DocumentExtractionError("PDF evidence contains no pages.")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise DocumentExtractionError(
            f"PDF evidence exceeds the {MAX_PDF_PAGES}-page extraction limit."
        )

    pages: list[str] = []
    extracted_characters = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf raises format-specific parser exceptions
            raise DocumentExtractionError("PDF text extraction failed.") from exc
        extracted_characters += len(text)
        if extracted_characters > MAX_EXTRACTED_CHARACTERS:
            raise DocumentExtractionError("PDF text layer exceeds the extraction size limit.")
        pages.append(text.replace("\r\n", "\n").replace("\r", "\n"))
    if not any(page.strip() for page in pages):
        raise DocumentExtractionError(
            "PDF has no embedded text layer; provide provenance-bound fields or OCR output."
        )

    results: list[ExtractedField] = []
    missing: list[str] = []
    for rule in FIELD_RULES[evidence_type]:
        candidates: list[tuple[str, int, str]] = []
        label_pattern = "|".join(_normalized_label_pattern(label) for label in rule.labels)
        pattern = re.compile(
            rf"(?im)^\s*(?:{label_pattern})\s*(?::|#|=)\s*(?P<value>[^\n]+?)\s*$"
        )
        for page_number, page_text in enumerate(pages, start=1):
            for match in pattern.finditer(page_text):
                raw_value = match.group("value").strip()
                normalized = _normalize_value(raw_value, rule.value_kind)
                candidates.append((normalized, page_number, raw_value))
        if not candidates:
            missing.append(rule.name)
            continue
        distinct = {candidate[0] for candidate in candidates}
        if len(distinct) != 1:
            raise DocumentExtractionError(
                f"PDF contains conflicting values for {rule.name}."
            )
        normalized, page_number, raw_value = candidates[0]
        results.append(
            ExtractedField(
                name=rule.name,
                raw_value=raw_value,
                normalized_value=normalized,
                confidence=Decimal("0.98"),
                method=ExtractionMethod.PDF_TEXT,
                source=SourceLocation(
                    document_id=document_id,
                    page_number=page_number,
                ),
            )
        )
    if missing:
        raise DocumentExtractionError(
            "PDF text layer is missing required labelled fields: " + ", ".join(missing)
        )
    return tuple(results)
