"""Matterport payment batch review UI with preview-backed earnings details."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from app.services.compensation_service import CompensationService
from app.ui.payment_batch_compact import (
    PaymentBatchDetail as CompactPaymentBatchDetail,
    PaymentBatchManager as CompactPaymentBatchManager,
)
from app.ui.payment_helpers import format_cents


class PaymentBatchDetail(CompactPaymentBatchDetail):
    """Populate proposed earnings directly from the compensation preview payload."""

    @staticmethod
    def _earnings_review_rows(preview: dict) -> list[tuple]:
        grouped: dict[int, dict] = {}
        for entry in preview.get("proposed_entries", []):
            tech_id = int(entry.get("technician_id") or 0)
            bucket = grouped.setdefault(tech_id, {
                "technician": entry.get("technician_name") or "Unassigned",
                "jobs": 0,
                "gross": 0,
                "capture": 0,
                "travel": 0,
                "adjustments": 0,
                "total": 0,
                "rates": set(),
            })
            bucket["jobs"] += 1
            bucket["gross"] += int(entry.get("gross_revenue_cents") or 0)
            bucket["total"] += int(entry.get("calculated_amount_cents") or 0)
            rate = entry.get("effective_rate_display") or entry.get("rule_source") or "—"
            bucket["rates"].add(str(rate))
            for component in entry.get("components", []):
                name = str(component.get("component") or "").casefold()
                amount = int(component.get("calculated_amount_cents") or 0)
                if name in {"base", "overall"}:
                    bucket["capture"] += amount
                elif name == "travel":
                    bucket["travel"] += amount
                else:
                    bucket["adjustments"] += amount

        return [
            (
                bucket["technician"],
                bucket["jobs"],
                format_cents(bucket["gross"]),
                ", ".join(sorted(bucket["rates"])),
                format_cents(bucket["capture"]),
                format_cents(bucket["travel"]),
                format_cents(bucket["adjustments"]),
                format_cents(bucket["total"]),
                "Ready",
            )
            for bucket in sorted(
                grouped.values(),
                key=lambda value: str(value["technician"]).casefold(),
            )
        ]

    def _ensure_post_match_status(self) -> bool:
        """Move a fully matched legacy Draft batch into the Imported workflow state.

        Earlier versions advanced Draft -> Imported only inside match_jobs(). Batches that
        were already completely matched before that transition logic ran could therefore
        remain stuck in Draft forever. Earnings review/finalization are safe places to
        repair that stale lifecycle state because we can verify the payment items directly.
        """
        if not self.batch_id or self.status_var.get() != "Draft":
            return False
        counts = self._invoice_match_counts()
        if not counts["invoice"] or counts["pending"] != 0:
            return False
        if not self.can_modify:
            return False
        self.service.update_payment_batch(
            self.session,
            self.batch_id,
            {"batch_status": "Imported"},
        )
        self.refresh()
        self.on_changed(self.batch_id)
        return True

    def review_earnings(self) -> None:
        posted = [
            row for row in getattr(self, "posted_earnings", [])
            if row["earning_status"] != "Voided"
        ]
        if posted:
            return super().review_earnings()

        try:
            self._ensure_post_match_status()
        except Exception as exc:
            messagebox.showerror("Review Earnings", str(exc), parent=self)
            return

        # Re-read the calculation after any automatic lifecycle repair so the dialog
        # always reflects the status that reconciliation will validate.
        if self.batch_id:
            self.compensation_preview = CompensationService(
                self.service.auth
            ).preview_technician_earnings(self.batch_id)
            self.reconciliation = self.service.validate_batch_reconciliation(self.batch_id)

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
        ttk.Label(frame, text="Proposed Technician Earnings", style="Header.TLabel").pack(anchor="w")
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
        table = ttk.Frame(frame)
        table.pack(fill="both", expand=True)
        tree = ttk.Treeview(table, columns=columns, show="headings", height=10)
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

        rows = self._earnings_review_rows(preview)
        for values in rows:
            tree.insert("", "end", values=values)

        ybar = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        xbar.pack(fill="x")

        summary = preview.get("summary", {})
        if rows:
            summary_text = (
                f"{len(rows)} technician(s) · "
                f"{summary.get('eligible_item_count', 0)} job(s) · "
                f"Proposed technician earnings: "
                f"{format_cents(summary.get('proposed_earnings_total_cents', 0))}"
            )
        else:
            summary_text = "No proposed technician earnings could be calculated."
        ttk.Label(frame, text=summary_text, style="Section.TLabel").pack(anchor="w", pady=(10, 4))

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

        ttk.Button(
            buttons,
            text="Finalize Payment & Generate Earnings",
            command=finalize_from_review,
            state="normal" if validation_ready and preview_ready and self.can_modify else "disabled",
            style="Accent.TButton",
        ).pack(side="right", padx=(0, 8))

    def finalize_payment(self) -> None:
        """Repair stale Draft status before invoking the normal finalization workflow."""
        try:
            self._ensure_post_match_status()
        except Exception as exc:
            messagebox.showerror("Finalize Payment", str(exc), parent=self)
            return
        return super().finalize_payment()


class PaymentBatchManager(CompactPaymentBatchManager):
    """Open the preview-backed payment batch detail screen."""

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
