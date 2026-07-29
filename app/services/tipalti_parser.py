"""Pure parser for plain-text payment-detail rows copied from Tipalti."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


ALIASES = {
    "document_number": ("Document Number", "Document #", "Document No", "Invoice Number",
                        "Invoice #", "Reference Number", "Reference"),
    "document_type": ("Document Type", "Type", "Payment Type"),
    "document_date": ("Document Date", "Invoice Date", "Date"),
    "description_raw": ("Description", "Payment Description", "Details", "Memo",
                        "Invoice Subject"),
    "amount_received_cents": ("Amount", "Payment Amount", "Amount Paid", "Net Amount",
                              "Amount Received", "Invoice Amount", "Amount Submitted"),
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
    if cleaned.upper().startswith("USD"):
        cleaned = cleaned[3:].lstrip()
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


def _split_row(value: str, delimiter: str) -> list[str]:
    if delimiter == "spaces":
        return re.split(r"\s{2,}", value.strip())
    return next(csv.reader([value], delimiter=delimiter))


def _horizontal_header(lines: list[tuple[int, str]]) -> tuple[int, str, list[str]] | None:
    """Return a recognized header and its browser-dependent column delimiter."""
    for offset, (_, line) in enumerate(lines):
        delimiters = (["\t"] if "\t" in line else []) + \
                     (["spaces"] if re.search(r"\s{2,}", line) else []) + [","]
        for delimiter in delimiters:
            fields = _split_row(line, delimiter)
            names = [HEADER_NAMES.get(_header(value)) for value in fields]
            if "document_number" in names and "amount_received_cents" in names:
                return offset, delimiter, fields
    return None


def _payment_details_lines(raw_text: str) -> tuple[list[tuple[int, str]], bool]:
    """Normalize browser text and isolate a Payment Details invoice section."""
    lines = [(number, line.strip(" ")) for number, line in enumerate(
        raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1) if line.strip()]
    payment = next((index for index, (_, line) in enumerate(lines)
                    if _header(line) == "payment details"), None)
    if payment is None:
        return lines, False
    related = next((index for index, (_, line) in enumerate(lines[payment + 1:], payment + 1)
                    if _header(line) == "related invoices"), None)
    if related is None:
        raise ValueError("The Tipalti Payment Details page does not contain a Related Invoices section.")
    return lines[related + 1:], True


def parse_tipalti_text(raw_text: str) -> dict[str, Any]:
    """Parse table text or the Related Invoices portion of a Payment Details page."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("Paste Tipalti payment-detail rows before parsing.")
    lines, payment_page = _payment_details_lines(raw_text)
    if not lines:
        raise ValueError("No payment-detail rows were detected.")

    horizontal = _horizontal_header(lines)
    if horizontal:
        header_offset, delimiter, header_fields = horizontal
        recognized = {index: HEADER_NAMES[_header(value)] for index, value in enumerate(header_fields)
                      if _header(value) in HEADER_NAMES}
        indexes = {field: index for index, field in recognized.items()}
        data_rows = [(number, _split_row(line, delimiter))
                     for number, line in lines[header_offset + 1:]]
        headers = True
    elif payment_page:
        # Chromium may serialize every table cell on its own line.  In that case the
        # consecutive recognized labels define both the schema and the row width.
        header_fields = []
        for _, line in lines:
            if _header(line) not in HEADER_NAMES:
                break
            header_fields.append(line)
        names = [HEADER_NAMES[_header(value)] for value in header_fields]
        if "document_number" not in names or "amount_received_cents" not in names:
            raise ValueError("The Related Invoices header could not be identified.")
        indexes = {field: index for index, field in enumerate(names)}
        cells = lines[len(header_fields):]
        width = len(header_fields)
        data_rows = [(cells[offset][0], [value for _, value in cells[offset:offset + width]])
                     for offset in range(0, len(cells), width)]
        headers = True
    else:
        data_rows = [(number, _split_row(line, "\t")) for number, line in lines]
        if any(len(fields) != 5 for _, fields in data_rows):
            raise ValueError("The pasted rows are ambiguous. Include the Tipalti header row.")
        indexes = {field: index for index, field in enumerate(
            ("document_number", "document_type", "document_date", "description_raw",
             "amount_received_cents"))}
        headers = False
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
