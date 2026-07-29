"""Matterport payment batch manager and workflow detail window."""

from __future__ import annotations

import logging
import sqlite3
import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk
from typing import Any, Callable

from app.security.user_manager import AuthorizationError
from app.services.payment_service import BATCH_STATUSES, PaymentService
from app.ui.payment_helpers import (format_cents, next_batch_status, parse_currency,
                                    status_permissions, totals_to_display, workflow_summary)
from app.ui.payment_exception_center import PaymentExceptionCenter
from app.ui.styles import PADDING
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
            self.tree.insert("", "end", iid=iid, values=(row["payment_date"], format_cents(row["payment_amount_cents"]),
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
        self.title("Matterport Payment Batch"); self.geometry("1180x780"); self.minsize(900, 650)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.vars = {field: tk.StringVar() for field in FIELDS if field != "notes"}
        self.status_var = tk.StringVar(value="Draft"); self.total_vars: dict[str, tk.StringVar] = {}
        outer = ttk.Frame(self, padding=PADDING); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Matterport Payment Batch", style="Header.TLabel").pack(anchor="w", pady=(0, 8))
        header = ttk.LabelFrame(outer, text="Batch", padding=8); header.pack(fill="x")
        labels = (("Payment Date", "payment_date"), ("Payment Amount", "payment_amount_cents"),
                  ("Payment Method", "payment_method"), ("Payer", "payer_name"),
                  ("Source System", "source_system"), ("Status", "status"),
                  ("Source Email Subject", "source_email_subject"),
                  ("Source Email Received", "source_email_received_at"))
        self.entries = {}
        for pos, (label, field) in enumerate(labels):
            row, pair = divmod(pos, 4); col = pair * 2
            ttk.Label(header, text=label + ":").grid(row=row, column=col, sticky="w", padx=(0, 5), pady=4)
            if field == "status": widget = ttk.Label(header, textvariable=self.status_var, style="Section.TLabel")
            else:
                widget = ttk.Entry(header, textvariable=self.vars[field], width=27); self.entries[field] = widget
            widget.grid(row=row, column=col + 1, sticky="ew", padx=(0, 10), pady=4)
        ttk.Label(header, text="Notes:").grid(row=2, column=0, sticky="nw", pady=4)
        self.notes = tk.Text(header, height=3, wrap="word"); self.notes.grid(row=2, column=1, columnspan=7, sticky="ew", pady=4)
        for col in (1, 3, 5, 7): header.columnconfigure(col, weight=1)
        items_frame = ttk.LabelFrame(outer, text="Payment Items", padding=6); items_frame.pack(fill="both", expand=True, pady=8)
        columns = ("document_number", "document_date", "description_raw", "amount", "job", "technician", "match_status", "match_notes")
        headings = ("Document Number", "Document Date", "Description", "Amount", "Job", "Technician", "Match Status", "Match Notes")
        self.items = ttk.Treeview(items_frame, columns=columns, show="headings", selectmode="browse")
        for key, heading in zip(columns, headings): self.items.heading(key, text=heading); self.items.column(key, width=125, anchor="e" if key == "amount" else "w")
        ybar = ttk.Scrollbar(items_frame, orient="vertical", command=self.items.yview); xbar = ttk.Scrollbar(items_frame, orient="horizontal", command=self.items.xview)
        self.items.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.items.grid(row=0, column=0, sticky="nsew"); ybar.grid(row=0, column=1, sticky="ns"); xbar.grid(row=1, column=0, sticky="ew")
        items_frame.rowconfigure(0, weight=1); items_frame.columnconfigure(0, weight=1)
        totals = ttk.LabelFrame(outer, text="Reconciliation", padding=6); totals.pack(fill="x")
        specs = (("Payment Amount", "payment_amount_cents"), ("Imported Total", "imported_total_cents"),
                 ("Difference", "difference_cents"), ("Matched Total", "matched_total_cents"),
                 ("Excluded Total", "excluded_total_cents"), ("Exception Total", "unmatched_total_cents"),
                 ("Item Count", "item_count"), ("Matched Count", "matched_count"),
                 ("Excluded Count", "excluded_count"), ("Exception Count", "exception_count"),
                 ("Unmatched", "unmatched_count"), ("Missing Job", "missing_job_count"),
                 ("Ambiguous", "ambiguous_count"), ("Amount Review", "amount_review_count"))
        for index, (label, key) in enumerate(specs):
            row, col = divmod(index, 5); col *= 2; var = tk.StringVar(value="$0.00" if "cents" in key else "0"); self.total_vars[key] = var
            ttk.Label(totals, text=label + ":").grid(row=row, column=col, sticky="e", padx=(5, 2), pady=2)
            style = "Section.TLabel" if key == "difference_cents" else "TLabel"
            ttk.Label(totals, textvariable=var, style=style).grid(row=row, column=col + 1, sticky="w", padx=(0, 8))
        self.workflow_var = tk.StringVar()
        workflow = ttk.LabelFrame(outer, text="Workflow", padding=6); workflow.pack(fill="x", pady=(0, 4))
        ttk.Label(workflow, textvariable=self.workflow_var, justify="left").pack(anchor="w")
        self.lock_var = tk.StringVar()
        ttk.Label(workflow, textvariable=self.lock_var, justify="left",
                  style="Section.TLabel").pack(anchor="w", pady=(4, 0))
        self.history_var = tk.StringVar()
        history = ttk.LabelFrame(outer, text="Financial History", padding=6)
        history.pack(fill="x", pady=(0, 4))
        ttk.Label(history, textvariable=self.history_var, justify="left").pack(anchor="w")
        actions = ttk.Frame(outer); actions.pack(fill="x", pady=(8, 0))
        self.save_button = ttk.Button(actions, text="Save", command=self.save)
        self.import_button = ttk.Button(actions, text="Import Tipalti Data", command=self.open_importer)
        self.match_button = ttk.Button(actions, text="Match Jobs", command=self.match_jobs)
        self.resolve_button = ttk.Button(actions, text="Resolve Exceptions", command=self.resolve_exceptions)
        self.reconcile_button = ttk.Button(actions, text="Reconcile Batch", command=self.open_reconciliation)
        self.advance_button = ttk.Button(actions, text="Advance Status", command=self.advance)
        self.delete_button = ttk.Button(actions, text="Delete Draft", command=self.delete)
        for button in (self.save_button, self.import_button, self.match_button, self.resolve_button, self.reconcile_button, self.advance_button, self.delete_button): button.pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(actions, text="Close", command=self.close).pack(side="right")
        self.snapshot: dict[str, Any] = {}
        if batch_id is None: self._load_new()
        else: self.refresh()

    def _load_new(self) -> None:
        defaults = {"payment_date": date.today().isoformat(), "payment_amount_cents": "0.00",
                    "payment_method": "ACH", "payer_name": "Matterport", "source_system": "Tipalti",
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
        self.save_button.configure(state="normal" if (self.batch_id is None and self.can_modify) or permissions["can_save"] else "disabled")
        self.import_button.configure(state="normal" if self.status_var.get() == "Draft" and self.can_modify else "disabled")
        self.match_button.configure(state="normal" if self.batch_id and permissions["can_match"] else "disabled")
        self.delete_button.configure(state="normal" if self.batch_id and permissions["can_delete"] else "disabled")
        self.advance_button.configure(state="normal" if self.batch_id and permissions["can_advance"] else "disabled")
        exception_count = int(self.total_vars.get("exception_count", tk.StringVar(value="0")).get() or 0)
        self.resolve_button.configure(state="normal" if self.batch_id and exception_count > 0 else "disabled")
        ready = bool(getattr(self, "reconciliation", {}).get("ready"))
        self.reconcile_button.configure(state="normal" if self.batch_id and self.can_modify and ready else "disabled")
        next_status = next_batch_status(self.status_var.get())
        self.advance_button.configure(text={"Imported":"Send to Review", "Reconciled":"Approve", "Approved":"Close Batch"}.get(self.status_var.get(), "Mark Imported") if next_status else "Advance Status")
        if self.status_var.get() == "Needs Review": self.advance_button.configure(state="disabled")

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
            var.set(format_cents(value).replace("$", "") if field == "payment_amount_cents" else value)
        self.notes.configure(state="normal"); self.notes.delete("1.0", "end"); self.notes.insert("1.0", batch.get("notes") or "")
        self.status_var.set(batch["batch_status"]); self.items.delete(*self.items.get_children())
        for item in items:
            technician = ""; job_id = item.get("job_id")
            if job_id:
                result = self.service.get_primary_technician_result(int(job_id))
                if result["status"] == "Found":
                    tech = result["technician"]; technician = " ".join(filter(None, (tech.get("first_name"), tech.get("last_name"))))
                elif result["status"] == "Missing": technician = "No primary technician"
                else: technician = "Multiple primary technicians"
            self.items.insert("", "end", values=(item.get("document_number") or "", item.get("document_date") or "",
                item.get("description_raw") or "", format_cents(item.get("amount_received_cents")), f"Job #{job_id}" if job_id else "",
                technician, item.get("match_status") or "", item.get("match_notes") or ""))
        display = totals_to_display(totals)
        self.workflow_var.set("\n".join(workflow_summary(batch["batch_status"], totals,
                                                         self.reconciliation, batch)))
        locked = batch["batch_status"] in ("Reconciled", "Approved", "Closed")
        self.lock_var.set("This payment batch has been reconciled.\nFinancial data is locked.\n"
                          "To make changes a supervisor must perform an unreconcile operation (future feature)."
                          if locked else "")
        self.history_var.set("\n".join(
            f"{entry['timestamp']}  |  {entry['user']}  |  {entry['event']}" for entry in history
        ) or "No financial history available.")
        for key, var in self.total_vars.items(): var.set(display.get(key, "0"))
        self.snapshot = self._form_values(); self.apply_status_permissions(); self.title(f"Matterport Payment Batch #{self.batch_id}")

    def _submitted(self) -> dict[str, Any]:
        values = self._form_values()
        if not values["payment_date"]: raise ValueError("Payment Date is required.")
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
            messagebox.showwarning("Tipalti Import", "Save the payment batch before importing Tipalti data.", parent=self); return
        if self.status_var.get() != "Draft": return
        try:
            totals = self.service.calculate_batch_totals(self.batch_id)
            TipaltiImportDialog(self, self.service, self.session, self.batch_id, self.batch, totals, self._after_import)
        except Exception as exc: _show_error(self, exc)

    def _after_import(self) -> None:
        self.refresh(); self.on_changed(self.batch_id)

    def match_jobs(self) -> None:
        try: result = self.service.match_payment_items(self.session, self.batch_id)
        except Exception as exc: _show_error(self, exc); return
        self.refresh(); self.on_changed(self.batch_id)
        messagebox.showinfo("Job Matching", f"Matched: {result['matched_count']}\nMissing Jobs: {result['missing_job_count']}\nAmbiguous: {result['ambiguous_count']}", parent=self)

    def resolve_exceptions(self) -> None:
        if self.batch_id:
            PaymentExceptionCenter(self, self.service, self.session, self.batch_id,
                                   lambda: (self.refresh(), self.on_changed(self.batch_id)))

    def open_reconciliation(self) -> None:
        if self.batch_id:
            PaymentBatchReconciliationDialog(
                self, self.service, self.session, self.batch_id,
                lambda: (self.refresh(), self.on_changed(self.batch_id)))

    def advance(self) -> None:
        current = self.status_var.get(); requested = next_batch_status(current)
        if not requested: return
        if not messagebox.askyesno("Advance Status", f"Advance this batch from {current} to {requested}?", parent=self): return
        try: self.service.update_payment_batch(self.session, self.batch_id, {"batch_status": requested})
        except Exception as exc: _show_error(self, exc); return
        self.refresh(); self.on_changed(self.batch_id)

    def delete(self) -> None:
        if not messagebox.askyesno("Delete Draft", f"Delete Draft payment batch #{self.batch_id}?", parent=self): return
        try: self.service.delete_payment_batch(self.session, self.batch_id)
        except Exception as exc: _show_error(self, exc); return
        self.on_changed(None); self.destroy()

    def close(self) -> None:
        if self._form_values() != self.snapshot and not messagebox.askyesno("Unsaved Changes", "Close without saving your changes?", parent=self): return
        self.destroy()


class PaymentBatchReconciliationDialog(tk.Toplevel):
    """Require an explicit operator certification before reconciliation."""

    def __init__(self, parent, service, session, batch_id, on_reconciled):
        super().__init__(parent)
        self.service, self.session, self.batch_id = service, session, batch_id
        self.on_reconciled = on_reconciled
        self.title("Payment Batch Reconciliation")
        self.transient(parent); self.grab_set(); self.resizable(False, False)
        frame = ttk.Frame(self, padding=16); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Payment Batch Reconciliation", style="Header.TLabel").pack(anchor="w")
        result = service.validate_batch_reconciliation(batch_id)
        summary = result["summary"]
        batch = service.get_payment_batch(batch_id) or {}
        operator = session.display_name or session.username
        values = (("Batch ID", batch_id), ("Payment Date", summary["payment_date"]),
                  ("Tipalti Payment Amount", format_cents(summary["payment_amount_cents"])),
                  ("Imported Total", format_cents(summary["imported_total_cents"])),
                  ("Effective Total", format_cents(summary["effective_total_cents"])),
                  ("Difference", format_cents(summary["difference_cents"])),
                  ("Matched Items", summary["matched_count"]),
                  ("Excluded Items", summary["excluded_count"]),
                  ("Imported Items", summary["item_count"]), ("Operator", operator))
        details = ttk.Frame(frame); details.pack(fill="x", pady=10)
        for row, (label, value) in enumerate(values):
            ttk.Label(details, text=label + ":").grid(row=row, column=0, sticky="e", padx=5)
            ttk.Label(details, text=str(value), style="Section.TLabel").grid(row=row, column=1, sticky="w")
        if result["warnings"]:
            ttk.Label(frame, text="⚠ Warning\n" + "\n".join(result["warnings"]),
                      justify="left").pack(anchor="w", pady=5)
        if result["errors"]:
            ttk.Label(frame, text="❌ Cannot Reconcile\n" + "\n".join(result["errors"]),
                      justify="left").pack(anchor="w", pady=5)
        self.certified = tk.BooleanVar()
        ttk.Checkbutton(frame, variable=self.certified, command=self._toggle,
                        text="I have reviewed this payment batch and certify that it accurately\n"
                             "represents the customer payment received.").pack(anchor="w", pady=10)
        actions = ttk.Frame(frame); actions.pack(fill="x")
        self.confirm = ttk.Button(actions, text="Confirm Reconciliation", command=self._confirm,
                                  state="disabled")
        self.confirm.pack(side="left")
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        self.ready = result["ready"]

    def _toggle(self):
        self.confirm.configure(state="normal" if self.ready and self.certified.get() else "disabled")

    def _confirm(self):
        try:
            self.service.reconcile_batch(self.session, self.batch_id)
        except Exception as exc:
            _show_error(self, exc); return
        self.on_reconciled(); self.destroy()
        messagebox.showinfo("Payment Batch Reconciliation", "Payment batch reconciled.", parent=self.master)
