"""Modal paste-and-preview wizard for Matterport payment emails."""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from app.date_utils import format_display_date
from app.services.matterport_email_parser import parse_matterport_payment_email
from app.services.tipalti_parser import mark_imported_duplicates
from app.ui.payment_helpers import format_cents, import_preview_summary
from app.ui.styles import PADDING

LOGGER = logging.getLogger(__name__)


class MatterportEmailImportDialog(tk.Toplevel):
    def __init__(self, parent, service, session, batch_id: int, batch: dict, totals: dict,
                 on_imported):
        super().__init__(parent)
        self.service, self.session, self.batch_id = service, session, batch_id
        self.batch, self.totals, self.on_imported = batch, totals, on_imported
        self.result = None
        self.title("Import Matterport Payment Email")
        self.geometry("1100x700"); self.minsize(850, 550); self.transient(parent); self.grab_set()
        outer = ttk.Frame(self, padding=PADDING); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Import Matterport Payment Email", style="Header.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Copy the complete Matterport payment notification email and paste it below.\nEmail headers are optional; include them to capture the payment date and subject.").pack(anchor="w", pady=(4, 8))
        self.editor = ttk.Frame(outer); self.editor.pack(fill="both", expand=True)
        self.text = tk.Text(self.editor, height=18, wrap="none", undo=True); self.text.pack(fill="both", expand=True)
        edit_actions = ttk.Frame(self.editor); edit_actions.pack(fill="x", pady=6)
        ttk.Button(edit_actions, text="Paste from Clipboard", command=self.paste).pack(side="left", padx=(0, 6))
        ttk.Button(edit_actions, text="Parse", command=self.parse).pack(side="left", padx=(0, 6))
        ttk.Button(edit_actions, text="Clear", command=lambda: self.text.delete("1.0", "end")).pack(side="left")
        ttk.Button(edit_actions, text="Cancel", command=self.destroy).pack(side="right")
        self.preview = ttk.Frame(outer)
        columns = ("row", "document", "type", "date", "description", "amount", "status", "message")
        self.tree = ttk.Treeview(self.preview, columns=columns, show="headings")
        headings = ("Row", "Document Number", "Document Type", "Document Date", "Description", "Amount", "Status", "Message")
        for key, heading in zip(columns, headings):
            self.tree.heading(key, text=heading); self.tree.column(key, width=125, anchor="e" if key in ("row", "amount") else "w")
        ybar = ttk.Scrollbar(self.preview, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ybar.set); self.tree.pack(side="left", fill="both", expand=True); ybar.pack(side="right", fill="y")
        self.header_var = tk.StringVar(); self.summary_var = tk.StringVar(); self.balance_var = tk.StringVar()
        self.header_label = ttk.Label(outer, textvariable=self.header_var)
        self.summary_label = ttk.Label(outer, textvariable=self.summary_var)
        self.balance_label = ttk.Label(outer, textvariable=self.balance_var)
        self.final = ttk.Frame(outer)
        self.import_button = ttk.Button(self.final, text="Import Valid Rows", command=self.import_rows)
        self.import_button.pack(side="left", padx=(0, 6)); ttk.Button(self.final, text="Back to Edit", command=self.back).pack(side="left")
        ttk.Button(self.final, text="Cancel", command=self.destroy).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.destroy); self.text.focus_set()

    def paste(self):
        try: value = self.clipboard_get()
        except tk.TclError: messagebox.showwarning("Matterport Email Import", "The clipboard does not contain plain text.", parent=self); return
        self.text.insert("insert", value)

    def parse(self):
        try:
            result = parse_matterport_payment_email(self.text.get("1.0", "end-1c"))
            documents = [r["document_number"] for r in result["rows"] if r["document_number"]]
            self.result = mark_imported_duplicates(result, self.service.find_duplicate_documents(documents))
        except Exception as exc:
            if not isinstance(exc, ValueError): LOGGER.exception("Unexpected Matterport email parser failure")
            messagebox.showerror("Matterport Email Import", str(exc) or "The pasted data could not be parsed.", parent=self); return
        self.tree.delete(*self.tree.get_children())
        for row in self.result["rows"]:
            self.tree.insert("", "end", values=(row["source_row_number"], row["document_number"], row["document_type"] or "",
                format_display_date(row["document_date"]), row["description_raw"] or "", format_cents(row["amount_received_cents"]), row["status"],
                ("This document number has already been imported and cannot be imported again."
                 if row["status"] == "Duplicate" else row["message"] or "")))
        summary = self.result["summary"]
        header = self.result["header"]
        self.header_var.set(
            f"Payment: {format_cents(header['payment_amount_cents'])}    "
            f"Method: {header['payment_method']}    Payer: {header['payer_name']}    "
            f"Date: {format_display_date(header['payment_date'], 'not available')}"
        )
        self.summary_var.set(f"Rows detected: {summary['row_count']}    Valid rows: {summary['valid_count']}    Duplicate rows: {summary['duplicate_count']}    Invalid rows: {summary['invalid_count']}    Importable amount: {format_cents(summary['importable_total_cents'])}")
        proposal = import_preview_summary(header["payment_amount_cents"], self.totals["imported_total_cents"], summary["importable_total_cents"])
        warning = "" if proposal["balances"] else "  ⚠ Proposed total does not equal the batch payment amount."
        self.balance_var.set(f"Current batch payment amount: {proposal['batch_amount']}    Importable pasted amount: {proposal['importable_amount']}    Difference after import: {proposal['difference_after_import']}{warning}")
        self.editor.pack_forget(); self.preview.pack(fill="both", expand=True, pady=8); self.header_label.pack(anchor="w"); self.summary_label.pack(anchor="w"); self.balance_label.pack(anchor="w", pady=(3, 0)); self.final.pack(fill="x", pady=(8, 0))
        self.import_button.configure(state="normal" if summary["valid_count"] else "disabled")

    def back(self):
        self.preview.pack_forget(); self.header_label.pack_forget(); self.summary_label.pack_forget(); self.balance_label.pack_forget(); self.final.pack_forget(); self.editor.pack(fill="both", expand=True); self.text.focus_set()

    def import_rows(self):
        summary = self.result["summary"]
        skipped = f"{summary['duplicate_count']} duplicate rows and {summary['invalid_count']} invalid rows will be skipped."
        if not messagebox.askyesno("Confirm Matterport Email Import", f"Import {summary['valid_count']} valid rows totaling {format_cents(summary['importable_total_cents'])} into this payment batch?\n\n{skipped}", parent=self): return
        items = [{key: row[key] for key in ("document_number", "document_type", "document_date", "description_raw", "amount_received_cents")} for row in self.result["rows"] if row["status"] == "Valid"]
        header_changes = {key: value for key, value in self.result["header"].items()
                          if value is not None}
        try:
            self.service.update_payment_batch(self.session, self.batch_id, header_changes)
            imported = self.service.import_payment_items(self.session, self.batch_id, items)
        except Exception as exc:
            if not isinstance(exc, (ValueError, LookupError)): LOGGER.exception("Unexpected Matterport email import failure")
            messagebox.showerror("Matterport Email Import", str(exc) or "The import failed.", parent=self); return
        messagebox.showinfo("Matterport Email Import", f"Imported {imported['imported_count']} rows totaling {format_cents(imported['imported_total_cents'])}.", parent=self)
        self.destroy(); self.on_imported()
