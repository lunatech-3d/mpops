"""Parser for Matterport payment-notification email text."""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from app.services.tipalti_parser import _amount, _date, _summary


_PAYMENT = re.compile(
    r"\b(?:A\s+)?USD\s+(?P<amount>[\d,]+(?:\.\d{1,2})?)\s+payment\s+was\s+sent"
    r"(?:\s+to\s+you)?\s+(?P<when>today|on\s+[^\n]+?)\s+by\s+"
    r"(?P<method>[A-Za-z][A-Za-z0-9 -]*?)(?:\s+and\s+covers\b|[.\r\n])",
    re.IGNORECASE,
)
_ITEM = re.compile(
    r"^\s*(?P<amount>(?:\(?\s*(?:-\s*)?(?:USD\s*)?(?:-\s*)?"
    r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?\s*\)?|USD))\s*(?:\|\s*)?"
    r"(?P<type>Invoice|Vendor\s+credit|Positive\s+adjustment|Fee(?:\s+or\s+deduction)?|"
    r"Deduction|Other\s+adjustment)\s*(?:\|\s*)?(?:(?P<document>\S+)\s*)?(?:\|\s*)?"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s*$", re.IGNORECASE,
)
_TABLE_HEADER = re.compile(
    r"^\s*Amount\s+Type\s+Document\s+number\s+Document\s+date\s*$", re.IGNORECASE,
)


def _email_headers(text: str) -> tuple[str | None, str | None]:
    """Return optional Subject and RFC-style Date headers from pasted source."""
    subject = re.search(r"(?mi)^Subject:\s*(.+?)\s*$", text)
    date_header = re.search(r"(?mi)^(?:Date|Sent):\s*(.+?)\s*$", text)
    received_at = None
    if date_header:
        value = date_header.group(1).strip()
        try:
            parsed = parsedate_to_datetime(value)
            received_at = parsed.isoformat()
        except (TypeError, ValueError, OverflowError):
            try:
                received_at = datetime.fromisoformat(value).isoformat()
            except ValueError:
                pass
    return (subject.group(1).strip() if subject else None), received_at


def _payment_date(when: str, received_at: str | None) -> str | None:
    if when.casefold() == "today":
        return received_at[:10] if received_at else None
    candidate = re.sub(r"^on\s+", "", when, flags=re.IGNORECASE).strip(" .")
    try:
        return _date(candidate)
    except ValueError:
        return None


def parse_signed_usd_amount(value: str) -> int:
    """Parse supported Matterport USD representations into signed integer cents."""
    source = str(value).strip()
    parenthesized = source.startswith("(") and source.endswith(")")
    if source.startswith("(") != source.endswith(")"):
        raise ValueError("Amount has unmatched parentheses")
    inner = source[1:-1].strip() if parenthesized else source
    inner = re.sub(r"^\s*-\s*USD\s*", "-", inner, flags=re.IGNORECASE)
    inner = re.sub(r"^\s*USD\s*", "", inner, flags=re.IGNORECASE).strip()
    negative = inner.startswith("-")
    if negative:
        inner = inner[1:].strip()
    cents = _amount(inner)
    if parenthesized and negative:
        raise ValueError("Amount contains conflicting negative notation")
    return -cents if parenthesized or negative else cents


def _document_type(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip()).casefold()
    return {"invoice": "Invoice", "vendor credit": "Vendor Credit",
            "positive adjustment": "Positive Adjustment", "fee": "Fee or Deduction",
            "fee or deduction": "Fee or Deduction", "deduction": "Fee or Deduction",
            "other adjustment": "Other Adjustment"}[normalized]


def parse_matterport_payment_email(raw_text: str) -> dict[str, Any]:
    """Parse a Matterport payment email into batch header data and import rows.

    Row-level omissions are returned as invalid preview rows; an unrecognizable
    notification is rejected before it can be mistaken for an empty payment.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("Paste the Matterport payment email before parsing.")
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    payment = _PAYMENT.search(text)
    if not payment:
        raise ValueError("The Matterport payment amount and payment method could not be identified.")
    subject, received_at = _email_headers(text)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    header_seen = False
    for number, line in enumerate(text.split("\n"), 1):
        if _TABLE_HEADER.match(line):
            header_seen = True
            continue
        match = _ITEM.match(line)
        if not match:
            continue
        document = (match.group("document") or "").strip()
        amount_text = (match.group("amount") or "").strip()
        document_type = _document_type(match.group("type"))
        errors: list[str] = []
        amount = None
        signed = None
        # Treat the invoice as an opaque source identifier. On-Demand invoice
        # numbers need not already exist in MPOPS or use an Airtable prefix.
        if not document:
            errors.append("Invoice number is required")
        try:
            signed = parse_signed_usd_amount(amount_text)
            if document_type in {"Vendor Credit", "Fee or Deduction"} and signed > 0:
                errors.append(f"{document_type} must use a negative amount")
            elif document_type in {"Invoice", "Positive Adjustment"} and signed < 0:
                errors.append(f"{document_type} must use a positive amount")
            amount = abs(signed)
        except ValueError as exc:
            errors.append(str(exc))
        document_date = _date(match.group("date"))
        duplicate = bool(document and document.casefold() in seen)
        if document:
            seen.add(document.casefold())
        status = "Invalid" if errors else ("Duplicate" if duplicate else "Valid")
        rows.append({
            "source_row_number": number, "document_number": document,
            "document_type": document_type, "document_date": document_date,
            "description_raw": None, "amount_received_cents": amount,
            "signed_effect_cents": signed if amount is not None else None,
            "allocation_status": ("Account Allocation Required" if document_type == "Vendor Credit"
                                  else "Not Required"),
            "direction_status": "Invalid" if errors else "Valid",
            "original_source_text": line, "status": status,
            "message": "; ".join(errors) if errors else
                       ("Duplicate invoice number in pasted email" if duplicate else None),
            "raw_fields": [value for value in match.groups()],
        })
    if not header_seen:
        raise ValueError("The payment email invoice table header could not be identified.")
    if not rows:
        raise ValueError("No invoice rows were detected in the Matterport payment email.")
    header = {
        "payment_amount_cents": _amount(payment.group("amount")),
        "payment_method": payment.group("method").strip(),
        "payment_date": _payment_date(payment.group("when"), received_at),
        "payer_name": "Matterport", "source_system": "Matterport Email",
        "source_email_subject": subject, "source_email_received_at": received_at,
    }
    summary = _summary(rows)
    summary["importable_total_cents"] = sum(
        int(row["signed_effect_cents"]) for row in rows if row["status"] == "Valid")
    summary["gross_invoice_total_cents"] = sum(
        int(row["amount_received_cents"]) for row in rows
        if row["status"] == "Valid" and row["document_type"] == "Invoice")
    return {"format": "matterport-payment-email", "headers_detected": True,
            "header": header, "rows": rows, "summary": summary}
