"""Compact Matterport payment-batch display with visible Job and Market data.

This module customizes only the payment-item grid. Reconciliation continues to use the
signed/effective payment amounts maintained by PaymentService.
"""

from __future__ import annotations

import sqlite3
from tkinter import messagebox

from app.date_utils import format_display_date
from app.security.user_manager import AuthorizationError
from app.services.jobs_service import JobsService
from app.ui.job_form import changed_fields, show_job_form
from app.ui.payment_batch_manager import (
    PaymentBatchDetail as BasePaymentBatchDetail,
    PaymentBatchManager as BasePaymentBatchManager,
)
from app.ui.payment_helpers import (
    format_adjustment_cents,
    format_cents,
    payment_item_sort_key,
)


EXPECTED_JOB_ERRORS = (ValueError, LookupError, AuthorizationError, sqlite3.Error)


class PaymentBatchDetail(BasePaymentBatchDetail):
    """Payment batch detail with a compact, operations-focused item grid."""

    ITEM_COLUMNS = (
        "document_number",
        "job_number",
        "document_date",
        "market",
        "customer",
        "technician",
        "amount_received_cents",
    )
    ITEM_HEADINGS = (
        "AP Number",
        "Job #",
        "Document Date",
        "Market",
        "Customer / Project",
        "Technician",
        "Gross Amount",
    )
    ITEM_WIDTHS = {
        "document_number": 125,
        "job_number": 110,
        "document_date": 82,
        "market": 125,
        "customer": 215,
        "technician": 130,
        "amount_received_cents": 88,
    }

    def __init__(self, *args, **kwargs):
        self._compact_columns_ready = False
        self._job_cache: dict[int, dict] = {}
        super().__init__(*args, **kwargs)
        self._configure_compact_item_columns()
        self.items.bind("<Double-1>", self.open_job_from_item, add="+")
        self._compact_columns_ready = True
        self._render_items()

    def _configure_compact_item_columns(self) -> None:
        self.items.configure(columns=self.ITEM_COLUMNS)
        self.item_headings = dict(zip(self.ITEM_COLUMNS, self.ITEM_HEADINGS))
        for key, heading in self.item_headings.items():
            self.items.heading(
                key,
                text=heading,
                command=lambda column=key: self.sort_items(column),
            )
            self.items.column(
                key,
                width=self.ITEM_WIDTHS[key],
                minwidth=65,
                anchor="e" if key == "amount_received_cents" else "w",
            )

    def _job(self, item: dict) -> dict | None:
        job_id = item.get("job_id")
        if not job_id:
            return None
        job_id = int(job_id)
        if job_id not in self._job_cache:
            job = JobsService(self.service.auth).get_job(job_id)
            self._job_cache[job_id] = job or {}
        return self._job_cache[job_id] or None

    def _job_number(self, item: dict) -> str:
        job = self._job(item)
        return str((job or {}).get("external_job_id") or "").strip() or "—"

    def _market(self, item: dict) -> str:
        job = self._job(item)
        if not job:
            return "—"
        market_name = str(job.get("market_name") or "").strip()
        market_state = str(job.get("market_state") or "").strip()
        if market_name and market_state:
            return f"{market_state} - {market_name}"
        if market_name:
            return market_name
        if market_state:
            return market_state
        return "UNASSIGNED"

    def _display_rows(self) -> list[dict]:
        rows = []
        for item in self.item_rows:
            display_row = dict(item)
            display_row["job_number"] = self._job_number(item)
            display_row["market"] = self._market(item)
            rows.append(display_row)
        return rows

    def _render_items(self) -> None:
        # Base __init__ invokes refresh before the compact columns are configured.
        if not getattr(self, "_compact_columns_ready", False):
            return super()._render_items()

        selected = self.items.selection()
        selected_id = selected[0] if selected else None
        rows = self._display_rows()

        if self.item_sort_column in {"job_number", "market"}:
            rows.sort(
                key=lambda row: str(row.get(self.item_sort_column) or "").casefold(),
                reverse=self.item_sort_descending,
            )
        else:
            rows.sort(
                key=lambda row: payment_item_sort_key(row, self.item_sort_column),
                reverse=self.item_sort_descending,
            )

        self.items.delete(*self.items.get_children())
        for key, heading in self.item_headings.items():
            marker = (
                " ▼" if self.item_sort_descending else " ▲"
            ) if key == self.item_sort_column else ""
            self.items.heading(key, text=heading + marker)

        for item in rows:
            iid = f"item-{item['payment_item_id']}"
            document_type = item.get("document_type") or "Invoice"
            signed = int(
                item.get("signed_effect_cents")
                if item.get("signed_effect_cents") is not None
                else item.get("amount_received_cents") or 0
            )
            if document_type == "Invoice":
                gross = format_cents(item.get("amount_received_cents"))
            else:
                # Net Effect is intentionally removed from the visible grid. Preserve
                # adjustment/credit visibility by showing the signed value here instead.
                gross = (
                    format_adjustment_cents(signed)
                    if signed < 0
                    else format_cents(signed)
                )

            target = item.get("customer") or "Unassigned"
            if document_type != "Invoice":
                target = f"{document_type} — {target}"
            technician = (
                item.get("technician") or "Unassigned"
                if document_type == "Invoice"
                else "—"
            )
            self.items.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.get("document_number") or "",
                    item.get("job_number") or "—",
                    format_display_date(item.get("document_date")),
                    item.get("market") or "UNASSIGNED",
                    target,
                    technician,
                    gross,
                ),
                tags=("adjustment",) if document_type != "Invoice" else (),
            )

        if selected_id and self.items.exists(selected_id):
            self.items.selection_set(selected_id)
            self.items.see(selected_id)

    def open_job_from_item(self, event=None) -> str:
        """Open the matched Job editor from a payment-item double click."""
        iid = self.items.identify_row(event.y) if event is not None else ""
        if not iid:
            selected = self.items.selection()
            iid = selected[0] if selected else ""
        if not iid:
            return "break"

        item = next(
            (row for row in self.item_rows if f"item-{row['payment_item_id']}" == iid),
            None,
        )
        if not item or not item.get("job_id"):
            messagebox.showinfo(
                "Open Job",
                "This payment item is not currently matched to a Job.",
                parent=self,
            )
            return "break"

        job_id = int(item["job_id"])
        service = JobsService(self.service.auth)
        try:
            original = service.get_job(job_id)
            if original is None:
                raise LookupError("Job not found")
            markets = service.list_market_options()
            technicians = service.list_active_technician_options()
            assignment = service.get_current_primary_assignment(job_id)
        except EXPECTED_JOB_ERRORS as exc:
            messagebox.showerror("Open Job", str(exc), parent=self)
            return "break"

        if assignment and assignment.get("status") != "Active":
            technicians = [*technicians, assignment]

        can_modify = self.session.role in {"admin", "operator"}
        if not can_modify:
            messagebox.showinfo(
                "Open Job",
                "Your account does not have permission to edit Jobs.",
                parent=self,
            )
            return "break"

        submitted = show_job_form(
            self,
            original,
            markets,
            technicians,
            lifecycle_permissions={
                "cancel": False,
                "archive": False,
                "delete_visible": False,
                "delete": False,
            },
        )
        if submitted is None:
            return "break"
        if submitted.get("__lifecycle_action"):
            return "break"

        changes = changed_fields(original, submitted)
        primary_technician_id = submitted.get("primary_technician_id")
        if not changes and primary_technician_id == original.get("primary_technician_id"):
            return "break"

        try:
            service.update_job(
                self.session,
                job_id,
                changes,
                primary_technician_id,
            )
        except EXPECTED_JOB_ERRORS as exc:
            messagebox.showerror("Update Job", str(exc), parent=self)
            return "break"

        # Job # and Market are cached for display speed. Clear the edited record and
        # redraw immediately so the operator can see that the correction took effect.
        self._job_cache.pop(job_id, None)
        self.refresh()
        return "break"


class PaymentBatchManager(BasePaymentBatchManager):
    """Use the compact detail screen while retaining the existing batch workflow."""

    def _open_detail(self, batch_id: int | None) -> None:
        if (
            batch_id is not None
            and batch_id in self.detail_windows
            and self.detail_windows[batch_id].winfo_exists()
        ):
            self.detail_windows[batch_id].lift()
            self.detail_windows[batch_id].focus_force()
            return
        detail = PaymentBatchDetail(
            self,
            self.service,
            self.session,
            batch_id,
            self.refresh,
        )
        if batch_id is not None:
            self.detail_windows[batch_id] = detail
