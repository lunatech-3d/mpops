"""Compact Matterport payment-batch display with visible business Job numbers.

This module customizes only the payment-item grid. Reconciliation continues to use the
signed/effective payment amounts maintained by PaymentService.
"""

from __future__ import annotations

from app.date_utils import format_display_date
from app.services.jobs_service import JobsService
from app.ui.payment_batch_manager import (
    PaymentBatchDetail as BasePaymentBatchDetail,
    PaymentBatchManager as BasePaymentBatchManager,
)
from app.ui.payment_helpers import (
    format_adjustment_cents,
    format_cents,
    payment_item_sort_key,
)


class PaymentBatchDetail(BasePaymentBatchDetail):
    """Payment batch detail with a compact, operations-focused item grid."""

    ITEM_COLUMNS = (
        "document_number",
        "job_number",
        "document_date",
        "account_name",
        "customer",
        "technician",
        "amount_received_cents",
    )
    ITEM_HEADINGS = (
        "AP Number",
        "Job #",
        "Document Date",
        "Account",
        "Customer / Project",
        "Technician",
        "Gross Amount",
    )
    ITEM_WIDTHS = {
        "document_number": 125,
        "job_number": 110,
        "document_date": 82,
        "account_name": 95,
        "customer": 225,
        "technician": 130,
        "amount_received_cents": 88,
    }

    def __init__(self, *args, **kwargs):
        self._compact_columns_ready = False
        self._job_number_cache: dict[int, str] = {}
        super().__init__(*args, **kwargs)
        self._configure_compact_item_columns()
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

    def _job_number(self, item: dict) -> str:
        job_id = item.get("job_id")
        if not job_id:
            return "—"
        job_id = int(job_id)
        if job_id not in self._job_number_cache:
            job = JobsService(self.service.auth).get_job(job_id)
            self._job_number_cache[job_id] = (
                str((job or {}).get("external_job_id") or "").strip() or "—"
            )
        return self._job_number_cache[job_id]

    def _render_items(self) -> None:
        # Base __init__ invokes refresh before the compact columns are configured.
        if not getattr(self, "_compact_columns_ready", False):
            return super()._render_items()

        selected = self.items.selection()
        selected_id = selected[0] if selected else None
        rows = []
        for item in self.item_rows:
            display_row = dict(item)
            display_row["job_number"] = self._job_number(item)
            rows.append(display_row)

        if self.item_sort_column == "job_number":
            rows.sort(
                key=lambda row: str(row.get("job_number") or "").casefold(),
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
                    item.get("account_name") or "Account allocation required",
                    target,
                    technician,
                    gross,
                ),
                tags=("adjustment",) if document_type != "Invoice" else (),
            )

        if selected_id and self.items.exists(selected_id):
            self.items.selection_set(selected_id)
            self.items.see(selected_id)


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
