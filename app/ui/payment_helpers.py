"""Pure presentation and workflow helpers for the payment UI."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from app.date_utils import format_display_datetime

NEXT_STATUS = {
    "Draft": "Imported", "Imported": "Needs Review",
    "Needs Review": "Reconciled", "Reconciled": "Approved",
    "Approved": "Closed",
}

EDITABLE_FIELDS = {
    "Draft": frozenset({"payment_date", "payment_amount_cents", "payment_method",
                        "payer_name", "source_system", "source_email_subject",
                        "source_email_received_at", "notes"}),
    "Imported": frozenset({"payment_method", "payer_name", "source_system",
                           "source_email_subject", "source_email_received_at", "notes"}),
    "Needs Review": frozenset({"notes"}),
    "Reconciled": frozenset(),
    "Approved": frozenset({"notes"}),
    "Closed": frozenset(), "Cancelled": frozenset(),
}

MONEY_FIELDS = ("payment_amount_cents", "imported_total_cents", "difference_cents",
                "effective_total_cents", "matched_total_cents", "excluded_total_cents",
                "unmatched_total_cents")


def format_cents(cents: int | None) -> str:
    """Format integer cents without passing through binary floating point."""
    value = int(cents or 0)
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}${value // 100:,}.{value % 100:02d}"


def payment_item_sort_key(row, column):
    """Return a typed sort key instead of sorting formatted grid text."""
    value = row.get(column)
    if column == "amount_received_cents":
        return (value is None, int(value or 0))
    if column in {"payment_date", "job_date"}:
        if not value:
            return (True, datetime.min)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (False, parsed.replace(tzinfo=None))
        except ValueError:
            return (False, datetime.min)
    return (value in (None, ""), str(value or "").casefold())


def technician_revenue_subtotals(rows):
    """Aggregate effective imported revenue by technician."""
    result = {}
    for row in rows:
        name = row.get("technician") or "Unassigned"
        key = ("tech", row["tech_id"]) if row.get("tech_id") else ("name", name.casefold())
        bucket = result.setdefault(key, {"technician": name, "job_count": 0,
                                          "revenue_cents": 0, "tech_id": row.get("tech_id")})
        bucket["job_count"] += 1
        bucket["revenue_cents"] += int(row.get("resolved_amount_cents")
                                       if row.get("resolved_amount_cents") is not None
                                       else row.get("amount_received_cents") or 0)
    return sorted(result.values(), key=lambda item: item["technician"].casefold())


def parse_currency(text: str) -> int:
    """Parse a non-negative US dollar amount into integer cents."""
    source = str(text).strip()
    if not source:
        raise ValueError("Payment Amount is required.")
    if not re.fullmatch(r"\$?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", source):
        raise ValueError("Payment Amount must be a valid dollar amount.")
    cleaned = source.removeprefix("$").replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Payment Amount must be a valid dollar amount.") from exc
    if not value.is_finite() or value < 0:
        raise ValueError("Payment Amount cannot be negative.")
    if value.as_tuple().exponent < -2:
        raise ValueError("Payment Amount may have no more than two decimal places.")
    return int(value * 100)


def next_batch_status(status: str) -> str | None:
    return NEXT_STATUS.get(status)


def status_permissions(status: str, can_modify: bool = True) -> dict[str, Any]:
    fields = EDITABLE_FIELDS.get(status, frozenset()) if can_modify else frozenset()
    return {"editable_fields": fields, "can_save": bool(fields),
            "can_match": can_modify and status in {"Draft", "Imported", "Needs Review"},
            "can_delete": can_modify and status == "Draft",
            "can_advance": can_modify and next_batch_status(status) is not None}


def totals_to_display(totals: dict[str, int]) -> dict[str, str]:
    result = {key: format_cents(totals.get(key, 0)) for key in MONEY_FIELDS}
    for key in ("item_count", "matched_count", "excluded_count", "exception_count",
                "unmatched_count", "missing_job_count", "ambiguous_count",
                "amount_review_count"):
        result[key] = str(totals.get(key, 0))
    return result


def import_preview_summary(batch_total_cents: int, imported_total_cents: int,
                           importable_total_cents: int) -> dict[str, Any]:
    difference = batch_total_cents - imported_total_cents - importable_total_cents
    return {"batch_amount": format_cents(batch_total_cents),
            "importable_amount": format_cents(importable_total_cents),
            "difference_after_import": format_cents(difference), "balances": difference == 0}


def workflow_summary(batch_status: str, totals: dict[str, int],
                     validation: dict[str, Any] | None = None,
                     batch: dict[str, Any] | None = None) -> list[str]:
    """Map service-derived totals to concise, presentation-only workflow lines."""
    items = totals.get("item_count", 0)
    lines = ["✓ Batch Created", f"{'✓' if items else '□'} Imported {items} Items",
             f"{'✓' if totals.get('matched_count', 0) else '□'} Matched "
             f"{totals.get('matched_count', 0)} Jobs"]
    for count, label in ((totals.get("missing_job_count", 0), "Missing Jobs"),
                         (totals.get("ambiguous_count", 0), "Ambiguous Matches"),
                         (totals.get("amount_review_count", 0), "Amount Review")):
        lines.append(f"{'⚠' if count else '✓'} {count} {label}")
    lines.append(f"{'✓' if totals.get('difference_cents') == 0 else '⚠'} Totals "
                 f"{'Balanced' if totals.get('difference_cents') == 0 else 'Not Balanced'}")
    ready = bool(items and totals.get("difference_cents") == 0 and
                 totals.get("exception_count", 0) == 0)
    lines.append(f"{'✓' if ready or batch_status in ('Reconciled', 'Approved', 'Closed') else '□'} Ready for Reconciliation")
    if validation and not validation["ready"] and batch_status not in ("Reconciled", "Approved", "Closed"):
        lines.extend(f"✗ {error}" for error in validation["errors"])
    if batch_status in ("Reconciled", "Approved", "Closed"):
        lines.append("✓ Reconciled")
        if batch:
            lines.extend((f"Reconciled By: {batch.get('reconciled_by_name') or batch.get('reconciled_by') or 'Unknown'}",
                          f"Reconciled On: {format_display_datetime(batch.get('reconciled_at'), 'Unknown')}"))
    return lines


def visible_exception_tabs(groups: dict[str, list[dict[str, Any]]]) -> tuple[str, ...]:
    """Return only non-empty exception categories in their operational order."""
    order = ("Missing Jobs", "Ambiguous Matches", "Amount Review", "Excluded")
    return tuple(name for name in order if groups.get(name))
