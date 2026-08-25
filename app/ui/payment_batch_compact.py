"""Compact Matterport payment-batch display with visible Job and Market data.

This module customizes the payment-item grid and makes the post-match workflow explicit.
Reconciliation continues to use the signed/effective payment amounts maintained by
PaymentService.
"""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

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

    def _update_primary_action(self, exception_count: int, excluded_count: int) -> None:
        """Drive the bottom action from the actual workflow state.

        Once every invoice item is matched, matching is complete. The operator moves
        forward to earnings review; the UI must never fall back to Match Jobs merely
        because a later financial calculation or reconciliation step is not yet ready.
        """
        items = len(self.item_rows)
        match_counts = self._invoice_match_counts()
        posted = [
            row for row in getattr(self, "posted_earnings", [])
            if row["earning_status"] != "Voided"
        ]
        preview = self.compensation_preview or {}
        calculation_errors = [
            entry.get("message", "Calculation exception")
            for entry in preview.get("exceptions", [])
        ]
        reconciliation_errors = list(
            getattr(self, "reconciliation", {}).get("errors", [])
        )

        matching_complete = bool(
            match_counts["invoice"] and match_counts["pending"] == 0
        )

        # The secondary Match Jobs button is useful only while matching remains.
        if matching_complete or not match_counts["invoice"]:
            if self.match_jobs_button.winfo_manager():
                self.match_jobs_button.pack_forget()
        else:
            if not self.match_jobs_button.winfo_manager():
                self.match_jobs_button.pack(
                    side="left", padx=(0, 6), before=self.more_button
                )

        if posted:
            action, label = "review", "Review Earnings"
            guidance = (
                "This payment has been finalized and technician earnings were created.\n"
                "Review the posted earnings before recording technician payments."
            )
        elif not items:
            action, label = "import", "Import Payment"
            guidance = "Import the Matterport payment details to begin."
        elif exception_count or excluded_count:
            action, label = "exceptions", "Review Exceptions"
            guidance = (
                f"{exception_count + excluded_count} payment item(s) still require attention.\n"
                "Resolve the payment matching or amount exceptions before continuing."
            )
        elif not matching_complete:
            action, label = "match", "Match Jobs"
            guidance = (
                f"{match_counts['pending']} invoice item(s) still require matching.\n"
                "Complete job matching before reviewing technician earnings."
            )
        elif calculation_errors:
            count = len(calculation_errors)
            action = "calculation_exceptions"
            label = f"Resolve {count} Financial Exception{'s' if count != 1 else ''}"
            guidance = (
                "Job matching is complete. Financial setup must be corrected before "
                "earnings can be finalized:\n"
                + "\n".join(f"• {reason}" for reason in calculation_errors[:4])
            )
        else:
            # Matching is complete. Even when reconciliation has a non-matching blocker,
            # the next workflow is earnings review, never another pass through matching.
            action, label = "review", "Review Earnings"
            guidance = (
                f"All {match_counts['matched']} invoice job(s) are matched.\n"
                "Review the proposed technician earnings before finalizing this payment."
            )
            if reconciliation_errors:
                guidance += "\n\nFinalization currently blocked: " + " ".join(
                    reconciliation_errors
                )
            elif preview.get("ready") and getattr(self, "reconciliation", {}).get("ready"):
                guidance += (
                    "\n\nThe earnings preview and reconciliation are valid. "
                    "Finalization is available from the earnings review."
                )

        self.primary_action = action
        self.primary_button.configure(
            text=label,
            state="normal" if (action == "review" or self.can_modify) else "disabled",
        )
        self.next_step_var.set(guidance)

    def review_earnings(self) -> None:
        """Review proposed earnings before finalization, or posted earnings afterward."""
        posted = [
            row for row in getattr(self, "posted_earnings", [])
            if row["earning_status"] != "Voided"
        ]
        if posted:
            return super().review_earnings()

        preview = self.compensation_preview or {}
        if not preview:
            messagebox.showinfo(
                "Review Earnings",
                "A technician earnings preview is not available yet.",
                parent=self,
            )
            return

        dialog = tk.Toplevel(self)
        dialog.title(f"Review Proposed Earnings — Batch #{self.batch_id}")
        dialog.geometry("1180x560")
        dialog.minsize(900, 420)
        dialog.transient(self)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Proposed Technician Earnings", style="Header.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text=(
                "These amounts are a non-posting preview. Nothing is approved or paid until "
                "the Matterport payment is finalized."
            ),
        ).pack(anchor="w", pady=(2, 10))

        columns = (
            "technician", "jobs", "gross", "rate", "capture", "travel",
            "adjustments", "total", "status",
        )
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=10)
        specs = (
            ("technician", "Technician", 180),
            ("jobs", "Jobs", 55),
            ("gross", "Gross Revenue", 105),
            ("rate", "Rate / Rule", 130),
            ("capture", "Capture", 90),
            ("travel", "Travel", 85),
            ("adjustments", "Adjustments", 90),
            ("total", "Proposed Total", 110),
            ("status", "Status", 90),
        )
        for key, heading, width in specs:
            tree.heading(key, text=heading)
            tree.column(
                key,
                width=width,
                anchor="e" if key in {
                    "jobs", "gross", "capture", "travel", "adjustments", "total"
                } else "w",
            )

        for iid in self.technician_summary.get_children():
            values = self.technician_summary.item(iid, "values")
            if values:
                tree.insert("", "end", values=values)

        ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        table = ttk.Frame(frame)
        table.pack(fill="both", expand=True)
        tree.pack(in_=table, side="left", fill="both", expand=True)
        ybar.pack(in_=table, side="right", fill="y")
        xbar.pack(fill="x")

        summary = preview.get("summary", {})
        ttk.Label(
            frame,
            text=(
                f"Proposed technician earnings: "
                f"{format_cents(summary.get('proposed_earnings_total_cents', 0))}"
            ),
            style="Section.TLabel",
        ).pack(anchor="w", pady=(10, 4))

        validation_ready = bool(getattr(self, "reconciliation", {}).get("ready"))
        preview_ready = bool(preview.get("ready"))
        blockers = list(getattr(self, "reconciliation", {}).get("errors", []))
        blockers.extend(
            entry.get("message", "Financial calculation exception")
            for entry in preview.get("exceptions", [])
        )
        status_text = (
            "Ready to finalize. Review the amounts above, then finalize when satisfied."
            if validation_ready and preview_ready
            else "Finalization is blocked: " + " ".join(blockers)
        )
        ttk.Label(frame, text=status_text, wraplength=1080).pack(anchor="w", pady=(0, 8))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side="right")

        def finalize_from_review() -> None:
            dialog.destroy()
            self.finalize_payment()

        finalize_button = ttk.Button(
            buttons,
            text="Finalize Payment & Generate Earnings",
            command=finalize_from_review,
            state="normal" if validation_ready and preview_ready and self.can_modify else "disabled",
            style="Accent.TButton",
        )
        finalize_button.pack(side="right", padx=(0, 8))

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
