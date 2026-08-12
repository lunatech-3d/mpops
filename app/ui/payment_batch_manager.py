"""Matterport payment batch manager and workflow detail window."""

from __future__ import annotations

import logging
import sqlite3
import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk
from typing import Any, Callable

from app.date_utils import (display_date_to_iso, display_datetime_to_iso,
                            format_display_date, format_display_datetime)
from app.security.user_manager import AuthorizationError
from app.services.payment_service import BATCH_STATUSES, PaymentService
from app.services.compensation_service import CompensationService
from app.ui.payment_helpers import (format_adjustment_cents, format_cents, parse_currency,
                                    payment_item_sort_key, status_permissions,
                                    technician_revenue_subtotals, totals_to_display,
                                    workflow_summary)
from app.ui.payment_exception_center import PaymentExceptionCenter
from app.ui.styles import PADDING
from app.ui.scrollable_frame import ScrollableFrame
from app.ui.matterport_email_import_dialog import MatterportEmailImportDialog
from app.ui.tipalti_import_dialog import TipaltiImportDialog

LOGGER = logging.getLogger(__name__)
EXPECTED_ERRORS = (ValueError, LookupError, AuthorizationError, sqlite3.Error)
STATUS_FILTERS = ("All",) + BATCH_STATUSES
FIELDS = ("payment_date", "payment_amount_cents", "payment_method", "payer_name",
          "source_system", "source_email_subject", "source_email_received_at", "notes")


def _show_error(parent: tk.Misc, exc: Exception) -> None:
    if not isinstance(exc, EXPECTED_ERRORS):
        LOGGER.exception("Unexpected payment UI error", exc_info=exc)
    messagebox.showerror("Matterport Payments", str(exc) or "The operation failed.", parent=parent)


class PaymentBatchManager(ttk.Frame):
    """Embedded list of payment batches with aggregate reconciliation totals."""

    COLUMNS = ("payment_date", "payment_amount_cents", "imported_total_cents",
               "difference_cents", "item_count", "matched_count", "excluded_count",
               "exception_count", "batch_status")

    def __init__(self, parent, auth, session):
        super().__init__(parent, padding=PADDING, style="App.TFrame")
        self.service, self.session = PaymentService(auth), session
        self.can_modify = session.role in {"admin", "operator"}
        self.rows: dict[str, dict[str, Any]] = {}
        self.detail_windows: dict[int, PaymentBatchDetail] = {}
        ttk.Label(self, text="Matterport Payment Batches", style="Header.TLabel").pack(anchor="w", pady=(0, 10))
        filters = ttk.Frame(self); filters.pack(fill="x", pady=(0, 8))
        ttk.Label(filters, text="Status:").pack(side="left")
        self.status_var = tk.StringVar(value="All")
        combo = ttk.Combobox(filters, textvariable=self.status_var, values=STATUS_FILTERS,
                             state="readonly", width=18)
        combo.pack(side="left", padx=6); combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        table = ttk.Frame(self); table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=self.COLUMNS, show="headings", selectmode="browse")
        headings = ("Payment Date", "Payment Amount", "Imported Total", "Difference", "Items",
                    "Matched", "Excluded", "Exceptions", "Status")
        widths = (115, 125, 125, 115, 65, 75, 75, 85, 110)
        for key, heading, width in zip(self.COLUMNS, headings, widths):
            self.tree.heading(key, text=heading); self.tree.column(key, width=width, anchor="e" if "cents" in key else "center")
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ybar.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); ybar.grid(row=0, column=1, sticky="ns")
        table.rowconfigure(0, weight=1); table.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _e: self.open_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_actions())
        actions = ttk.Frame(self); actions.pack(fill="x", pady=(8, 0))
        self.new_button = ttk.Button(actions, text="New Batch", command=self.new_batch)
        self.open_button = ttk.Button(actions, text="Open", command=self.open_selected)
        self.delete_button = ttk.Button(actions, text="Delete Draft", command=self.delete_selected)
        for button in (self.new_button, self.open_button, self.delete_button): button.pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Refresh", command=self.refresh).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Close", command=self.destroy).pack(side="right")
        self.message_var = tk.StringVar()
        ttk.Label(self, textvariable=self.message_var, style="Status.TLabel").pack(anchor="w", pady=(7, 0))
        self.refresh()

    def refresh(self, select_id: int | None = None) -> None:
        selection = self.selected_id(silent=True) if select_id is None else select_id
        try:
            status = None if self.status_var.get() == "All" else self.status_var.get()
            rows = self.service.list_payment_batches_with_totals(status)
        except Exception as exc:
            _show_error(self, exc); return
        self.tree.delete(*self.tree.get_children()); self.rows.clear()
        for row in rows:
            batch_id = int(row["payment_batch_id"]); iid = f"batch-{batch_id}"; self.rows[iid] = row
            self.tree.insert("", "end", iid=iid, values=(format_display_date(row["payment_date"]), format_cents(row["payment_amount_cents"]),
                format_cents(row["imported_total_cents"]), format_cents(row["difference_cents"]), row["item_count"],
                row["matched_count"], row["excluded_count"], row["exception_count"], row["batch_status"]))
        iid = f"batch-{selection}" if selection else ""
        if iid and self.tree.exists(iid): self.tree.selection_set(iid); self.tree.see(iid)
        self.message_var.set(f"{len(rows)} payment batch(es)." if rows else "No payment batches found.")
        self._update_actions()

    def selected_id(self, silent: bool = False) -> int | None:
        selected = self.tree.selection()
        if not selected:
            if not silent: messagebox.showwarning("Matterport Payments", "Select a payment batch first.", parent=self)
            return None
        return int(self.rows[selected[0]]["payment_batch_id"])

    def _update_actions(self) -> None:
        selected = self.tree.selection(); row = self.rows.get(selected[0]) if selected else None
        self.new_button.configure(state="normal" if self.can_modify else "disabled")
        self.open_button.configure(state="normal" if row else "disabled")
        self.delete_button.configure(state="normal" if self.can_modify and row and row["batch_status"] == "Draft" else "disabled")

    def new_batch(self) -> None: self._open_detail(None)
    def open_selected(self) -> None:
        batch_id = self.selected_id()
        if batch_id: self._open_detail(batch_id)

    def _open_detail(self, batch_id: int | None) -> None:
        if batch_id is not None and batch_id in self.detail_windows and self.detail_windows[batch_id].winfo_exists():
            self.detail_windows[batch_id].lift(); self.detail_windows[batch_id].focus_force(); return
        detail = PaymentBatchDetail(self, self.service, self.session, batch_id, self.refresh)
        if batch_id is not None: self.detail_windows[batch_id] = detail

    def delete_selected(self) -> None:
        batch_id = self.selected_id()
        if not batch_id: return
        row = self.rows[f"batch-{batch_id}"]
        if row["batch_status"] != "Draft":
            messagebox.showwarning("Matterport Payments", "Only Draft payment batches may be deleted.", parent=self); return
        if not messagebox.askyesno("Delete Draft", f"Delete Draft payment batch #{batch_id}?", parent=self): return
        try: self.service.delete_payment_batch(self.session, batch_id)
        except Exception as exc: _show_error(self, exc); return
        self.refresh(); self.message_var.set("Draft payment batch deleted.")


class PaymentBatchDetail(tk.Toplevel):
    """Create, inspect, edit, match, and advance one payment batch."""

    def __init__(self, parent, service: PaymentService, session, batch_id: int | None,
                 on_changed: Callable[[int | None], None]):
        super().__init__(parent); self.service, self.session = service, session
        self.batch_id, self.on_changed = batch_id, on_changed
        self.can_modify = session.role in {"admin", "operator"}; self.batch: dict[str, Any] = {}
        self.title("Matterport Payment Batch"); self.geometry("1180x760"); self.minsize(1000, 650)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.vars = {field: tk.StringVar() for field in FIELDS if field != "notes"}
        self.status_var = tk.StringVar(value="Draft"); self.total_vars: dict[str, tk.StringVar] = {}
        self.item_rows: list[dict[str, Any]] = []
        self.compensation_preview: dict[str, Any] | None = None
        self.technician_breakdowns: dict[str, dict[str, Any]] = {}
        self.history_rows: list[dict[str, Any]] = []
        self.item_sort_column, self.item_sort_descending = "document_date", False
        # Keep workflow actions outside the scrolling form so they remain
        # reachable as the window shrinks or more detail sections are added.
        outer = ttk.Frame(self)
        outer.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1); self.columnconfigure(0, weight=1)
        scrollable = ScrollableFrame(outer)
        scrollable.grid(row=0, column=0, sticky="nsew")
        outer.rowconfigure(0, weight=1); outer.columnconfigure(0, weight=1)
        content = scrollable.content
        content.configure(padding=PADDING)
        self.scrollable_content = scrollable
        ttk.Label(content, text="Matterport Payment Batch", style="Header.TLabel").pack(anchor="w", pady=(0, 4))
        header = ttk.LabelFrame(content, text="Payment Summary", padding=6); header.pack(fill="x")
        labels = (("Payment Date", "payment_date"), ("Payment Amount", "payment_amount_cents"),
                  ("Payment Method", "payment_method"), ("Payer", "payer_name"),
                  ("Status", "status"))
        self.entries = {}
        for pos, (label, field) in enumerate(labels):
            row, pair = divmod(pos, 5); col = pair * 2
            ttk.Label(header, text=label + ":").grid(row=row, column=col, sticky="w", padx=(0, 4), pady=2)
            if field == "status": widget = ttk.Label(header, textvariable=self.status_var, style="Section.TLabel")
            else:
                widget = ttk.Entry(header, textvariable=self.vars[field], width=27); self.entries[field] = widget
            widget.grid(row=row, column=col + 1, sticky="ew", padx=(0, 8), pady=2)
        for col in (1, 3, 5, 7, 9): header.columnconfigure(col, weight=1)
        self.source_details_button = ttk.Button(content, text="Payment Source Details ▸",
                                                command=self.toggle_source_details)
        self.source_details_button.pack(anchor="w", pady=(4, 0))
        self.source_details = ttk.LabelFrame(content, text="Payment Source Details", padding=6)
        source_fields = (("Source System", "source_system"),
                         ("Email Subject", "source_email_subject"),
                         ("Email Received", "source_email_received_at"))
        for index, (label, field) in enumerate(source_fields):
            ttk.Label(self.source_details, text=label + ":").grid(row=0, column=index * 2,
                sticky="w", padx=(0, 4), pady=2)
            entry = ttk.Entry(self.source_details, textvariable=self.vars[field], width=28)
            entry.grid(row=0, column=index * 2 + 1, sticky="ew", padx=(0, 8), pady=2)
            self.entries[field] = entry
            self.source_details.columnconfigure(index * 2 + 1, weight=1)
        ttk.Label(self.source_details, text="Notes:").grid(row=1, column=0, sticky="nw", pady=2)
        self.notes = tk.Text(self.source_details, height=2, wrap="word")
        self.notes.grid(row=1, column=1, columnspan=5, sticky="ew", pady=2)
        items_frame = ttk.LabelFrame(content, text="Payment Items", padding=6); items_frame.pack(fill="both", expand=True, pady=8)
        columns = ("document_number", "document_date", "account_name",
                   "customer", "amount_received_cents", "signed_effect_cents", "allocation_status")
        headings = ("Document Number", "Document Date", "Account",
                    "Job / Invoice", "Gross Amount", "Net Effect", "Allocation Status")
        self.items = ttk.Treeview(items_frame, columns=columns, show="headings", selectmode="browse")
        self.item_headings = dict(zip(columns, headings))
        for key, heading in zip(columns, headings):
            self.items.heading(key, text=heading, command=lambda column=key: self.sort_items(column))
            widths = {"document_number": 135, "document_date": 95, "account_name": 125,
                      "customer": 310, "amount_received_cents": 100,
                      "signed_effect_cents": 95, "allocation_status": 115}
            self.items.column(key, width=widths[key], minwidth=75,
                              anchor="e" if key in {"amount_received_cents", "signed_effect_cents"} else "w")
        self.items.tag_configure("adjustment", foreground="#8a4b08")
        ybar = ttk.Scrollbar(items_frame, orient="vertical", command=self.items.yview); xbar = ttk.Scrollbar(items_frame, orient="horizontal", command=self.items.xview)
        self.items.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.items.grid(row=0, column=0, sticky="nsew"); ybar.grid(row=0, column=1, sticky="ns"); xbar.grid(row=1, column=0, sticky="ew")
        items_frame.rowconfigure(0, weight=1); items_frame.columnconfigure(0, weight=1)
        summaries = ttk.Frame(content); summaries.pack(fill="x", pady=(0, 6))
        distribution = ttk.LabelFrame(summaries, text="Distribution", padding=6)
        distribution.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        summaries.columnconfigure((0, 1), weight=1)
        self.distribution_unavailable_var = tk.StringVar()
        ttk.Label(distribution, textvariable=self.distribution_unavailable_var,
                  style="Section.TLabel").grid(row=0, column=0, columnspan=6, sticky="w")
        self.distribution_vars = {key: tk.StringVar(value="—") for key in
                                  ("gross", "technicians", "east", "lunatech", "unallocated")}
        distribution_specs = (
            ("Matterport Gross Payment", "gross", None),
            ("Technician Transfers", "technicians", None),
            ("Transfer to LunaTech-East", "east", "Section.TLabel"),
            ("Retained by LunaTech 3D", "lunatech", "Section.TLabel"),
            ("Unallocated / Exceptions", "unallocated", None),
        )
        for index, (label, key, style) in enumerate(distribution_specs):
            row, pair = divmod(index, 2); column = pair * 2
            ttk.Label(distribution, text=label + ":").grid(
                row=row + 1, column=column, sticky="e", padx=(0, 5), pady=2)
            ttk.Label(distribution, textvariable=self.distribution_vars[key],
                      style=style or "TLabel").grid(
                row=row + 1, column=column + 1, sticky="w", padx=(0, 18), pady=2)
        self.distribution_status_var = tk.StringVar()
        ttk.Label(distribution, textvariable=self.distribution_status_var, wraplength=500).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(2, 0))

        compensation = ttk.LabelFrame(content, text="Technician Transfers", padding=6)
        compensation.pack(fill="x", pady=(0, 8))
        summary_columns = ("technician", "jobs", "revenue", "rate", "capture", "travel",
                           "adjustments", "payout", "status")
        transfer_table = ttk.Frame(compensation); transfer_table.pack(fill="x")
        self.technician_summary = ttk.Treeview(transfer_table, columns=summary_columns,
                                               show="headings", height=4)
        for key, heading, width in (("technician", "Technician", 180), ("jobs", "Jobs", 55),
                                    ("revenue", "Gross Revenue", 110), ("rate", "Rate / Rule", 105),
                                    ("capture", "Capture", 90), ("travel", "Travel", 85),
                                    ("adjustments", "Adjustments", 90),
                                    ("payout", "Proposed Total", 110), ("status", "Status", 85)):
            self.technician_summary.heading(key, text=heading)
            self.technician_summary.column(key, width=width,
                anchor="e" if key in {"jobs", "revenue", "capture", "travel", "adjustments", "payout"} else "w")
        transfer_ybar = ttk.Scrollbar(transfer_table, orient="vertical",
                                      command=self.technician_summary.yview)
        self.technician_summary.configure(yscrollcommand=transfer_ybar.set)
        self.technician_summary.grid(row=0, column=0, sticky="ew")
        transfer_ybar.grid(row=0, column=1, sticky="ns")
        transfer_table.columnconfigure(0, weight=1)
        self.technician_summary.bind("<Double-1>", lambda _event: self.view_technician_breakdown())
        self.technician_summary.bind("<<TreeviewSelect>>", lambda _event: self._update_calculation_action())
        transfer_actions = ttk.Frame(compensation); transfer_actions.pack(fill="x", pady=(5, 0))
        self.allocation_totals_var = tk.StringVar(value="Total Technician Transfers: —")
        ttk.Label(compensation, textvariable=self.allocation_totals_var,
                  style="Section.TLabel").pack(in_=transfer_actions, side="left")
        self.calculation_button = ttk.Button(transfer_actions,
            text="View Selected Technician Calculation", command=self.view_technician_breakdown,
            state="disabled")
        self.calculation_button.pack(side="right")
        totals = ttk.LabelFrame(summaries, text="Reconciliation", padding=6)
        totals.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        specs = (("Gross Invoices", "gross_invoice_total_cents"),
                 ("Positive Adjustments", "positive_adjustments_cents"),
                 ("Vendor Credits", "vendor_credits_cents"),
                 ("Fees / Deductions", "fees_and_deductions_cents"),
                 ("Expected Net Payment", "expected_net_payment_cents"),
                 ("Actual ACH Received", "payment_amount_cents"),
                 ("Difference", "difference_cents"), ("Matched", "matched_count"),
                 ("Exceptions", "exception_count"))
        for index, (label, key) in enumerate(specs):
            row, col = divmod(index, 2); col *= 2; var = tk.StringVar(value="$0.00" if "cents" in key else "0"); self.total_vars[key] = var
            ttk.Label(totals, text=label + ":").grid(row=row, column=col, sticky="e", padx=(5, 2), pady=2)
            style = "Section.TLabel" if key == "difference_cents" else "TLabel"
            ttk.Label(totals, textvariable=var, style=style).grid(row=row, column=col + 1, sticky="w", padx=(0, 8))
        self.history_var = tk.StringVar()
        history = ttk.Frame(content)
        history.pack(fill="x", pady=(0, 2))
        ttk.Label(history, textvariable=self.history_var).pack(side="left", anchor="w")
        ttk.Button(history, text="View Financial History",
                   command=self.show_financial_history).pack(side="right")
        actions = ttk.Frame(outer, padding=(PADDING, 8, PADDING, PADDING))
        actions.grid(row=1, column=0, sticky="ew")
        guidance = ttk.Frame(actions)
        guidance.pack(side="left", fill="x", expand=True)
        ttk.Label(guidance, text="Next Step", style="Section.TLabel").pack(anchor="w")
        self.next_step_var = tk.StringVar()
        ttk.Label(guidance, textvariable=self.next_step_var, justify="left",
                  wraplength=520).pack(anchor="w", pady=(2, 7))
        action_buttons = ttk.Frame(actions); action_buttons.pack(side="right", anchor="s")
        self.save_button = ttk.Button(actions, text="Save", command=self.save)
        self.primary_button = ttk.Button(action_buttons, text="Import Payment",
                                         command=self.run_primary_action, style="Accent.TButton")
        self.more_button = ttk.Menubutton(action_buttons, text="More Actions")
        self.more_menu = tk.Menu(self.more_button, tearoff=False)
        self.more_menu.add_command(label="Save Changes", command=self.save)
        self.more_menu.add_command(label="Import Tipalti Metadata", command=self.open_metadata_importer)
        self.more_menu.add_command(label="Refresh", command=self.refresh)
        self.more_menu.add_separator()
        self.more_menu.add_command(label="Delete Draft", command=self.delete)
        self.more_button.configure(menu=self.more_menu)
        self.more_button.pack(side="left", padx=(0, 6))
        ttk.Button(action_buttons, text="Close", command=self.close).pack(side="left", padx=(0, 6))
        self.primary_button.pack(side="left")
        self.primary_action = "import"
        self.snapshot: dict[str, Any] = {}
        for var in self.vars.values():
            var.trace_add("write", lambda *_args: self._mark_dirty())
        self.notes.bind("<KeyRelease>", lambda _event: self._mark_dirty())
        if batch_id is None: self._load_new()
        else: self.refresh()

    def toggle_source_details(self) -> None:
        """Reveal infrequently used import metadata without consuming routine space."""
        if self.source_details.winfo_manager():
            self.source_details.pack_forget()
            self.source_details_button.configure(text="Payment Source Details ▸")
        else:
            self.source_details.pack(fill="x", pady=(2, 0), after=self.source_details_button)
            self.source_details_button.configure(text="Payment Source Details ▾")

    def _mark_dirty(self) -> None:
        if hasattr(self, "save_button"):
            permissions = status_permissions(self.status_var.get(), self.can_modify)
            editable = self.batch_id is None or permissions["can_save"]
            state = "normal" if editable and self._form_values() != self.snapshot else "disabled"
            self.save_button.configure(state=state)
            self.more_menu.entryconfigure("Save Changes", state=state)

    def _load_new(self) -> None:
        defaults = {"payment_date": format_display_date(date.today()), "payment_amount_cents": "0.00",
                    "payment_method": "ACH", "payer_name": "Matterport", "source_system": "Matterport Email",
                    "source_email_subject": "", "source_email_received_at": ""}
        for field, value in defaults.items(): self.vars[field].set(value)
        self.status_var.set("Draft"); self.snapshot = self._form_values(); self.apply_status_permissions()

    def _form_values(self) -> dict[str, Any]:
        values = {key: var.get().strip() for key, var in self.vars.items()}
        values["notes"] = self.notes.get("1.0", "end-1c").strip(); return values

    def apply_status_permissions(self) -> None:
        permissions = status_permissions(self.status_var.get(), self.can_modify)
        editable = permissions["editable_fields"]
        for field, entry in self.entries.items(): entry.configure(state="normal" if field in editable else "disabled")
        self.notes.configure(state="normal" if "notes" in editable else "disabled")
        changed = self._form_values() != self.snapshot
        self.save_button.configure(state="normal" if self.can_modify and
                                   (self.batch_id is None or (permissions["can_save"] and changed)) else "disabled")
        self.more_menu.entryconfigure("Save Changes", state=str(self.save_button.cget("state")))
        self.more_menu.entryconfigure("Import Tipalti Metadata", state="normal" if
            self.batch_id and self.status_var.get() == "Draft" and self.can_modify else "disabled")
        self.more_menu.entryconfigure("Delete Draft", state="normal" if
            self.batch_id and permissions["can_delete"] else "disabled")
        exception_count = int(self.total_vars.get("exception_count", tk.StringVar(value="0")).get() or 0)
        excluded_count = int(self.total_vars.get("excluded_count", tk.StringVar(value="0")).get() or 0)
        self._update_primary_action(exception_count, excluded_count)

    def refresh(self) -> None:
        if self.batch_id is None: return
        try:
            batch = self.service.get_payment_batch(self.batch_id)
            if batch is None: raise LookupError("Payment batch not found")
            items = self.service.list_payment_items(self.batch_id); totals = self.service.calculate_batch_totals(self.batch_id)
            self.reconciliation = self.service.validate_batch_reconciliation(self.batch_id)
            history = self.service.get_batch_history(self.batch_id)
        except Exception as exc: _show_error(self, exc); return
        self.batch = batch
        for field, var in self.vars.items():
            value = batch.get(field) or ""
            if field == "payment_amount_cents": value = format_cents(value).replace("$", "")
            elif field == "payment_date": value = format_display_date(value)
            elif field == "source_email_received_at": value = format_display_datetime(value)
            var.set(value)
        self.notes.configure(state="normal"); self.notes.delete("1.0", "end"); self.notes.insert("1.0", batch.get("notes") or "")
        self.status_var.set(batch["batch_status"]); self.item_rows = items
        self.items.configure(height=max(3, min(6, len(items))))
        self._render_items(); self._render_technician_summary()
        display = totals_to_display(totals)
        self.workflow_details = workflow_summary(batch["batch_status"], totals,
                                                 self.reconciliation, batch)
        self.history_rows = history
        if history:
            latest = history[-1]
            self.history_var.set(
                f"Last activity: {format_display_datetime(latest['timestamp'])} — "
                f"{latest['event']} by {latest['user']}")
        else:
            self.history_var.set("No financial history available.")
        for key, var in self.total_vars.items(): var.set(display.get(key, "0"))
        if "vendor_credits_cents" in self.total_vars:
            self.total_vars["vendor_credits_cents"].set(
                format_adjustment_cents(totals.get("vendor_credits_cents")))
        if "fees_and_deductions_cents" in self.total_vars:
            self.total_vars["fees_and_deductions_cents"].set(
                format_adjustment_cents(totals.get("fees_and_deductions_cents")))
        self.snapshot = self._form_values(); self.apply_status_permissions(); self.title(f"Matterport Payment Batch #{self.batch_id}")

    def _update_primary_action(self, exception_count: int, excluded_count: int) -> None:
        """Expose only the next meaningful workflow operation and explain blockers."""
        items = len(self.item_rows)
        posted = [row for row in getattr(self, "posted_earnings", [])
                  if row["earning_status"] != "Voided"]
        preview = self.compensation_preview or {}
        calculation_errors = [entry.get("message", "Calculation exception")
                              for entry in preview.get("exceptions", [])]
        reconciliation_errors = list(getattr(self, "reconciliation", {}).get("errors", []))
        if posted:
            action, label = "review", "Review Earnings"
            guidance = ("The payment has been finalized and Pending technician earnings were created.\n"
                        "Review the earnings before approval or payment.")
        elif not items:
            action, label = "import", "Import Payment"
            guidance = "Import the Matterport payment details to begin."
        elif exception_count or excluded_count:
            action, label = "exceptions", "Review Exceptions"
            guidance = (f"{exception_count + excluded_count} payment item(s) require attention.\n"
                        "Resolve the listed matching or amount exceptions, then match again.")
        elif preview.get("ready") and getattr(self, "reconciliation", {}).get("ready"):
            action, label = "finalize", "Finalize Payment & Generate Earnings"
            guidance = (f"All {preview['summary']['eligible_item_count']} jobs are matched.\n"
                        "Technician earnings have been calculated. Review the summary and finalize when ready.")
        elif calculation_errors:
            action, label = "calculation_exceptions", "Review Exceptions"
            guidance = "Technician calculation is blocked:\n" + "\n".join(
                f"• {reason}" for reason in calculation_errors[:4])
        else:
            action, label = "match", "Match Jobs"
            guidance = (f"The payment contains {items} invoice item(s).\n"
                        "Match them to jobs before calculating technician earnings.")
            if reconciliation_errors and all("status" not in reason.lower()
                                             for reason in reconciliation_errors):
                guidance += "\n\nBlocked: " + " ".join(reconciliation_errors)
        self.primary_action = action
        self.primary_button.configure(text=label, state="normal" if
            (action == "review" or self.can_modify) else "disabled")
        self.next_step_var.set(guidance)

    def run_primary_action(self) -> None:
        actions = {"import": self.open_importer, "match": self.match_jobs,
                   "exceptions": self.resolve_exceptions, "finalize": self.finalize_payment,
                   "calculation_exceptions": self.show_workflow_details,
                   "review": self.review_earnings}
        actions[self.primary_action]()

    def sort_items(self, column: str) -> None:
        """Toggle typed sorting while retaining the selected payment item."""
        self.item_sort_descending = (not self.item_sort_descending
                                     if column == self.item_sort_column else False)
        self.item_sort_column = column
        self._render_items()

    def _render_items(self) -> None:
        selected = self.items.selection()
        selected_id = selected[0] if selected else None
        rows = sorted(self.item_rows,
                      key=lambda row: payment_item_sort_key(row, self.item_sort_column),
                      reverse=self.item_sort_descending)
        self.items.delete(*self.items.get_children())
        for key, heading in self.item_headings.items():
            marker = (" ▼" if self.item_sort_descending else " ▲") if key == self.item_sort_column else ""
            self.items.heading(key, text=heading + marker)
        for item in rows:
            iid = f"item-{item['payment_item_id']}"
            signed = int(item.get("signed_effect_cents")
                         if item.get("signed_effect_cents") is not None
                         else item.get("amount_received_cents") or 0)
            gross = (format_cents(item.get("amount_received_cents"))
                     if item.get("document_type") == "Invoice" else "—")
            effect = format_adjustment_cents(signed) if signed < 0 else format_cents(signed)
            target = item.get("customer") or (f"Job #{item['job_id']}" if item.get("job_id") else "Unassigned")
            document_type = item.get("document_type") or "Invoice"
            if document_type != "Invoice":
                target = f"{document_type} — {target}"
            self.items.insert("", "end", iid=iid, values=(item.get("document_number") or "",
                format_display_date(item.get("document_date")),
                item.get("account_name") or "Account allocation required", target, gross, effect,
                item.get("allocation_status") or "Not Required"),
                tags=("adjustment",) if document_type != "Invoice" else ())
        if selected_id and self.items.exists(selected_id):
            self.items.selection_set(selected_id); self.items.see(selected_id)

    def _render_technician_summary(self) -> None:
        self.technician_summary.delete(*self.technician_summary.get_children())
        self.technician_breakdowns.clear()
        self.compensation_preview = None
        self.posted_earnings = []
        self.allocation_totals_var.set("Total Technician Transfers: —")
        self.distribution_unavailable_var.set("Match invoice items to calculate a non-posting preview.")
        self.distribution_status_var.set("")
        for var in self.distribution_vars.values(): var.set("—")
        earnings = {}
        paid_status = {}
        if self.batch_id and self.item_rows:
            compensation = CompensationService(self.service.auth)
            preview = compensation.preview_technician_earnings(self.batch_id)
            self.compensation_preview = preview
            totals = preview["summary"]
            posted_rows = compensation.list_technician_earnings(payment_batch_id=self.batch_id)
            self.posted_earnings = posted_rows
            self.distribution_unavailable_var.set("")
            self.distribution_vars["gross"].set(format_cents(totals["gross_revenue_total_cents"]))
            self.distribution_vars["technicians"].set(format_cents(totals["technician_total_cents"]))
            self.distribution_vars["east"].set(format_cents(totals["lunatech_east_total_cents"]))
            self.distribution_vars["lunatech"].set(format_cents(totals["lunatech_total_cents"]))
            unallocated = int(totals["unallocated_total_cents"])
            self.distribution_vars["unallocated"].set(format_cents(unallocated))
            self.distribution_status_var.set(
                "✓ Distribution balances to the Matterport payment" if unallocated == 0 and not preview["exceptions"]
                else f"⚠ {format_cents(unallocated)} remains unallocated; "
                     f"{len(preview['exceptions'])} exception(s)")
            self.allocation_totals_var.set(
                f"Total Technician Transfers: {format_cents(totals['technician_total_cents'])}")
            for posted in posted_rows:
                paid_status.setdefault(posted["tech_id"], []).append(posted["earning_status"])
            for entry in preview["proposed_entries"]:
                bucket = earnings.setdefault(entry["technician_id"],
                    {"amount": 0, "capture": 0, "travel": 0, "adjustments": 0,
                     "rates": set(), "entries": []})
                bucket["amount"] += entry["calculated_amount_cents"]
                for component in entry.get("components", []):
                    name = component.get("component", "").casefold()
                    value = int(component.get("calculated_amount_cents", component.get("amount_cents", 0)) or 0)
                    if name in {"base", "overall"}: bucket["capture"] += value
                    elif name == "travel": bucket["travel"] += value
                    else: bucket["adjustments"] += value
                bucket["rates"].add(entry.get("effective_rate_display") or
                    (f"{entry['rule_value'] / 100:.2f}%" if entry["rule_type"] == "Percentage" else "Flat"))
                bucket["entries"].append(entry)
        for subtotal in technician_revenue_subtotals(self.item_rows):
            earning = earnings.get(subtotal["tech_id"])
            statuses = paid_status.get(subtotal["tech_id"], [])
            paid = "Yes" if statuses and all(status == "Paid" for status in statuses) else (
                "Pending" if statuses else "No")
            iid = f"tech-{subtotal['tech_id']}" if subtotal["tech_id"] else f"name-{len(self.technician_breakdowns)}"
            self.technician_summary.insert("", "end", iid=iid, values=(subtotal["technician"], subtotal["job_count"],
                format_cents(subtotal["revenue_cents"]), ", ".join(sorted(earning["rates"])) if earning else "—",
                format_cents(earning["capture"]) if earning else "—",
                format_cents(earning["travel"]) if earning else "—",
                format_cents(earning["adjustments"]) if earning else "—",
                format_cents(earning["amount"]) if earning else "—", paid if earning else "Blocked"))
            self.technician_breakdowns[iid] = {"subtotal": subtotal,
                                                "entries": earning["entries"] if earning else []}
        self.technician_summary.configure(
            height=max(1, min(6, len(self.technician_summary.get_children()))))
        self._update_calculation_action()

    def _update_calculation_action(self) -> None:
        selected = self.technician_summary.selection()
        detail = self.technician_breakdowns.get(selected[0], {}) if selected else {}
        state = "normal" if detail.get("entries") else "disabled"
        self.calculation_button.configure(state=state)

    def view_technician_breakdown(self) -> None:
        """Show the selected technician's non-posting calculation preview."""
        selected = self.technician_summary.selection()
        if not selected:
            messagebox.showinfo("Technician Calculation",
                                "Select a technician first.", parent=self)
            return
        detail = self.technician_breakdowns.get(selected[0], {})
        entries = detail.get("entries", [])
        if not entries:
            preview = self.compensation_preview or {}
            unmatched = sum(1 for exception in preview.get("exceptions", [])
                            if exception.get("reason_code") in {"ITEM_NOT_MATCHED", "MISSING_JOB"})
            reason = (f"Technician calculations are not available because {unmatched} job(s) "
                      "still require matching." if unmatched else
                      "No calculation breakdown is available for the selected technician.")
            messagebox.showinfo("Technician Calculation", reason, parent=self)
            return
        name = detail["subtotal"]["technician"]
        dialog = tk.Toplevel(self); dialog.title(f"Technician Calculation — {name}")
        dialog.geometry("1160x420"); dialog.minsize(900, 300)
        dialog.transient(self); dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=name, style="Header.TLabel").pack(anchor="w")
        ttk.Label(frame, text=f"Payment batch #{self.batch_id} · Non-posting calculation preview").pack(
            anchor="w", pady=(1, 8))
        columns = ("job", "date", "gross", "rule", "capture", "travel", "adjustments", "total", "exceptions")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=min(8, len(entries)))
        for key, heading, width in (("job", "Job / Invoice", 130), ("date", "Job Date", 95),
                                    ("gross", "Gross Revenue", 105), ("rule", "Rule / Rate", 200),
                                    ("capture", "Capture", 90), ("travel", "Travel", 85),
                                    ("adjustments", "Adjustments", 90), ("total", "Proposed Total", 105),
                                    ("exceptions", "Calculation Exceptions", 210)):
            tree.heading(key, text=heading); tree.column(key, width=width, anchor="w")
        for entry in entries:
            amounts = {"capture": 0, "travel": 0, "adjustments": 0}
            for component in entry.get("components", []):
                name_key = component.get("component", "").casefold()
                destination = "capture" if name_key in {"base", "overall"} else (
                    "travel" if name_key == "travel" else "adjustments")
                amounts[destination] += int(component.get("calculated_amount_cents", 0) or 0)
            warnings = entry.get("component_reconciliation_warning") or "None"
            rule = f"{entry.get('effective_rate_display') or '—'} · {entry.get('rule_source') or 'Rule'}"
            tree.insert("", "end", values=(entry.get("external_job_id") or
                entry.get("document_number") or entry.get("job_id") or "",
                format_display_date(entry.get("job_date")), format_cents(entry["gross_revenue_cents"]),
                rule, format_cents(amounts["capture"]), format_cents(amounts["travel"]),
                format_cents(amounts["adjustments"]), format_cents(entry["calculated_amount_cents"]), warnings))
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=xbar.set); tree.pack(fill="both", expand=True); xbar.pack(fill="x")
        ttk.Button(frame, text="Close", command=dialog.destroy).pack(anchor="e", pady=(8, 0))

    def show_workflow_details(self) -> None:
        """Open the complete workflow checklist without reserving form height."""
        dialog = tk.Toplevel(self); dialog.title("Payment Batch Workflow Details")
        dialog.transient(self); dialog.grab_set(); dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=14); frame.pack(fill="both", expand=True)
        lines = list(getattr(self, "workflow_details", []))
        preview = self.compensation_preview or {}
        lines.extend(f"⚠ {warning}" for warning in
                     (entry.get("component_reconciliation_warning")
                      for entry in preview.get("proposed_entries", [])) if warning)
        lines.extend(f"✗ {entry.get('message', 'Allocation exception')}"
                     for entry in preview.get("exceptions", []))
        ttk.Label(frame, text="\n".join(lines) or "No workflow details available.",
                  justify="left", wraplength=720).pack(anchor="w")
        ttk.Button(frame, text="Close", command=dialog.destroy).pack(anchor="e", pady=(10, 0))

    def show_financial_history(self) -> None:
        """Open the complete audit history in a compact scrolling table."""
        dialog = tk.Toplevel(self); dialog.title("Payment Batch Financial History")
        dialog.geometry("850x400"); dialog.minsize(650, 280)
        dialog.transient(self); dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12); frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=("timestamp", "user", "event"),
                            show="headings", height=10)
        for key, heading, width in (("timestamp", "Date/Time", 175),
                                    ("user", "User", 150), ("event", "Event", 460)):
            tree.heading(key, text=heading); tree.column(key, width=width, anchor="w")
        for entry in self.history_rows:
            tree.insert("", "end", values=(format_display_datetime(entry["timestamp"]),
                                             entry["user"], entry["event"]))
        ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ybar.set)
        tree.grid(row=0, column=0, sticky="nsew"); ybar.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        ttk.Button(frame, text="Close", command=dialog.destroy).grid(
            row=1, column=0, columnspan=2, sticky="e", pady=(8, 0))

    def _submitted(self) -> dict[str, Any]:
        values = self._form_values()
        if not values["payment_date"]: raise ValueError("Payment Date is required.")
        values["payment_date"] = display_date_to_iso(values["payment_date"])
        values["source_email_received_at"] = display_datetime_to_iso(values["source_email_received_at"])
        values["payment_amount_cents"] = parse_currency(values["payment_amount_cents"])
        return {key: (value or None) for key, value in values.items()}

    def save(self) -> None:
        try:
            submitted = self._submitted()
            if self.batch_id is None:
                submitted["batch_status"] = "Draft"; self.batch_id = self.service.create_payment_batch(self.session, submitted)
            else:
                permissions = status_permissions(self.status_var.get(), self.can_modify)
                changed = {key: value for key, value in submitted.items() if key in permissions["editable_fields"] and
                           value != (parse_currency(self.snapshot[key]) if key == "payment_amount_cents" else (self.snapshot[key] or None))}
                if not changed: return
                self.service.update_payment_batch(self.session, self.batch_id, changed)
        except Exception as exc: _show_error(self, exc); return
        self.on_changed(self.batch_id); self.refresh(); messagebox.showinfo("Matterport Payments", "Payment batch saved.", parent=self)

    def open_importer(self) -> None:
        if self.batch_id is None:
            try:
                submitted = self._submitted(); submitted["batch_status"] = "Draft"
                self.batch_id = self.service.create_payment_batch(self.session, submitted)
                self.on_changed(self.batch_id); self.refresh()
            except Exception as exc:
                _show_error(self, exc); return
        if self.status_var.get() != "Draft": return
        try:
            totals = self.service.calculate_batch_totals(self.batch_id)
            MatterportEmailImportDialog(self, self.service, self.session, self.batch_id,
                                        self.batch, totals, self._after_import)
        except Exception as exc: _show_error(self, exc)

    def open_metadata_importer(self) -> None:
        """Retain the legacy Tipalti clipboard importer as an optional path."""
        if self.batch_id is None:
            messagebox.showwarning("Tipalti Metadata Import",
                                   "Save the payment batch before importing Tipalti metadata.",
                                   parent=self)
            return
        if self.status_var.get() != "Draft":
            return
        try:
            totals = self.service.calculate_batch_totals(self.batch_id)
            TipaltiImportDialog(self, self.service, self.session, self.batch_id,
                                self.batch, totals, self._after_import)
        except Exception as exc:
            _show_error(self, exc)

    def _after_import(self) -> None:
        self.refresh(); self.on_changed(self.batch_id)

    def match_jobs(self) -> None:
        try: result = self.service.match_payment_items(self.session, self.batch_id)
        except Exception as exc: _show_error(self, exc); return
        remaining = int(result["missing_job_count"]) + int(result["ambiguous_count"])
        totals = self.service.calculate_batch_totals(self.batch_id)
        remaining += int(totals.get("amount_review_count", 0))
        if not remaining and self.status_var.get() == "Draft":
            self.service.update_payment_batch(self.session, self.batch_id,
                                              {"batch_status": "Imported"})
        self.refresh(); self.on_changed(self.batch_id)
        if remaining:
            message = (f"Matched: {result['matched_count']}\nMissing Jobs: {result['missing_job_count']}\n"
                       f"Ambiguous: {result['ambiguous_count']}\nAmount Review: {totals.get('amount_review_count', 0)}")
        else:
            message = (f"All {result['matched_count']} jobs were matched.\n\n"
                       "Technician earnings have been calculated for review.\n"
                       "The payment is ready to finalize.")
        messagebox.showinfo("Job Matching", message, parent=self)

    def resolve_exceptions(self) -> None:
        if self.batch_id:
            PaymentExceptionCenter(self, self.service, self.session, self.batch_id,
                                   lambda: (self.refresh(), self.on_changed(self.batch_id)))

    def finalize_payment(self) -> None:
        """Revalidate, reconcile, and post Pending earnings through one user action."""
        if not self.batch_id:
            return
        validation = self.service.validate_batch_reconciliation(self.batch_id)
        preview = CompensationService(self.service.auth).preview_technician_earnings(self.batch_id)
        if not validation["ready"] or not preview["ready"]:
            reasons = validation["errors"] + [entry["message"] for entry in preview["exceptions"]]
            messagebox.showerror("Finalize Payment", "Finalization is blocked:\n\n" + "\n".join(reasons), parent=self)
            self.refresh(); return
        summary, calculation = validation["summary"], preview["summary"]
        prompt = ("Finalize this Matterport payment?\n\n"
                  f"Jobs matched: {summary['matched_count']}\n"
                  f"Gross invoices: {self.total_vars['gross_invoice_total_cents'].get()}\n"
                  f"Credits/deductions: {self.total_vars['vendor_credits_cents'].get()}\n"
                  f"Net payment: {format_cents(summary['payment_amount_cents'])}\n"
                  f"Technician earnings to create: {format_cents(calculation['proposed_earnings_total_cents'])}\n\n"
                  "This will reconcile the payment and create Pending technician earnings.\n"
                  "It will not approve or pay the earnings.")
        if not messagebox.askyesno("Finalize Payment", prompt, parent=self):
            return
        try:
            result = self.service.finalize_payment(self.session, self.batch_id)
        except Exception as exc:
            _show_error(self, exc)
            return
        self.refresh(); self.on_changed(self.batch_id)
        messagebox.showinfo("Payment Finalized",
            f"Created {result['generated_count']} Pending technician earning(s).\n\n"
            "Review Earnings to inspect them before approval or payment.", parent=self)

    def review_earnings(self) -> None:
        """Open the existing earnings review workflow filtered to this batch."""
        if not self.batch_id:
            return
        from app.ui.technician_earnings_manager import TechnicianEarningsManager
        dialog = tk.Toplevel(self); dialog.title(f"Technician Earnings — Batch #{self.batch_id}")
        dialog.geometry("1400x650"); dialog.minsize(1000, 500); dialog.transient(self)
        TechnicianEarningsManager(dialog, self.service.auth, self.session,
                                  payment_batch_id=self.batch_id).pack(fill="both", expand=True)

    def delete(self) -> None:
        if not messagebox.askyesno("Delete Draft", f"Delete Draft payment batch #{self.batch_id}?", parent=self): return
        try: self.service.delete_payment_batch(self.session, self.batch_id)
        except Exception as exc: _show_error(self, exc); return
        self.on_changed(None); self.destroy()

    def close(self) -> None:
        if self._form_values() != self.snapshot and not messagebox.askyesno("Unsaved Changes", "Close without saving your changes?", parent=self): return
        self.destroy()
