"""Pure parser for plain-text payment-detail rows copied from Tipalti."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


ALIASES = {
    "document_number": ("Document Number", "Document #", "Document No", "Invoice Number",
                        "Invoice #", "Reference Number", "Reference"),
    "document_type": ("Document Type", "Type", "Payment Type"),
    "document_date": ("Document Date", "Invoice Date", "Date"),
    "description_raw": ("Description", "Payment Description", "Details", "Memo"),
    "amount_received_cents": ("Amount", "Payment Amount", "Amount Paid", "Net Amount",
                              "Amount Received"),
}


def _header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


HEADER_NAMES = {_header(alias): field for field, aliases in ALIASES.items() for alias in aliases}


def _date(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Document date is invalid")


def _amount(value: str) -> int:
    original = value.strip()
    if not original:
        raise ValueError("Amount is required")
    negative = (original.startswith("(") and original.endswith(")")) or original.startswith("-")
    if negative:
        raise ValueError("Negative amounts are not allowed")
    if original.startswith("(") or original.endswith(")"):
        raise ValueError("Amount is not valid currency")
    cleaned = original
    if cleaned.startswith("$"):
        cleaned = cleaned[1:]
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", cleaned):
        if re.fullmatch(r"[\d,]+\.\d{3,}", cleaned):
            raise ValueError("Amount may have no more than two decimal places")
        raise ValueError("Amount is not valid currency")
    try:
        amount = Decimal(cleaned.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("Amount is not valid currency") from exc
    return int(amount * 100)


def parse_tipalti_text(raw_text: str) -> dict[str, Any]:
    """Parse tab- or safely quoted comma-delimited plain text without database access."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("Paste Tipalti payment-detail rows before parsing.")
    delimiter = "\t" if "\t" in raw_text else ","
    source = list(csv.reader(io.StringIO(raw_text), delimiter=delimiter))
    nonblank = [(number, fields) for number, fields in enumerate(source, 1)
                if any(field.strip() for field in fields)]
    if not nonblank:
        raise ValueError("No payment-detail rows were detected.")
    first_number, first = nonblank[0]
    recognized = {index: HEADER_NAMES[_header(value)] for index, value in enumerate(first)
                  if _header(value) in HEADER_NAMES}
    headers = "document_number" in recognized.values() and "amount_received_cents" in recognized.values()
    if headers:
        indexes = {field: index for index, field in recognized.items()}
        data_rows = nonblank[1:]
    else:
        if delimiter != "\t" or any(len(fields) != 5 for _, fields in nonblank):
            raise ValueError("The pasted rows are ambiguous. Include the Tipalti header row.")
        indexes = {field: index for index, field in enumerate(
            ("document_number", "document_type", "document_date", "description_raw",
             "amount_received_cents"))}
        data_rows = nonblank
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_number, fields in data_rows:
        def field(name: str) -> str:
            index = indexes.get(name)
            return fields[index].strip() if index is not None and index < len(fields) else ""
        document = field("document_number")
        errors = []
        document_date = None
        amount = None
        if not document:
            errors.append("Document number is required")
        try:
            document_date = _date(field("document_date"))
        except ValueError as exc:
            errors.append(str(exc))
        try:
            amount = _amount(field("amount_received_cents"))
        except ValueError as exc:
            errors.append(str(exc))
        duplicate = bool(document and document.casefold() in seen)
        if document:
            seen.add(document.casefold())
        status = "Invalid" if errors else ("Duplicate" if duplicate else "Valid")
        message = "; ".join(errors) if errors else (
            "Duplicate document number in pasted data" if duplicate else None)
        rows.append({"source_row_number": source_number, "document_number": document,
                     "document_type": field("document_type") or None,
                     "document_date": document_date,
                     "description_raw": field("description_raw") or None,
                     "amount_received_cents": amount, "status": status, "message": message,
                     "raw_fields": fields})
    return _result(rows, headers)


def mark_imported_duplicates(result: dict[str, Any], duplicate_documents: set[str]) -> dict[str, Any]:
    """Mark otherwise-valid rows using document numbers supplied by PaymentService."""
    duplicates = {value.casefold() for value in duplicate_documents}
    for row in result["rows"]:
        if row["status"] == "Valid" and row["document_number"].casefold() in duplicates:
            row["status"] = "Duplicate"
            row["message"] = "Document number has already been imported"
    result["summary"] = _summary(result["rows"])
    return result


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"row_count": len(rows), "valid_count": sum(r["status"] == "Valid" for r in rows),
            "duplicate_count": sum(r["status"] == "Duplicate" for r in rows),
            "invalid_count": sum(r["status"] == "Invalid" for r in rows),
            "importable_total_cents": sum((r["amount_received_cents"] or 0) for r in rows
                                           if r["status"] == "Valid")}


def _result(rows: list[dict[str, Any]], headers: bool) -> dict[str, Any]:
    return {"format": "tabular", "headers_detected": headers, "rows": rows,
            "summary": _summary(rows)}
