"""Matterport Job Intake Center workflow for OpenTable CSV exports."""

import sqlite3
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.date_utils import format_display_datetime
from app.security.user_manager import AuthorizationError
from app.services.opentable_import_service import OpenTableImportService
from app.ui.styles import PADDING
from app.ui.treeview_utils import natural_sort_key, ordered_tree_items


EXPECTED_ERRORS = (ValueError, OSError, AuthorizationError, sqlite3.Error)

PROTECTED_FIELD_LABELS = {
    "address_1": "Address 1",
    "address_2": "Address 2",
    "city": "City",
    "state": "State",
    "postal_code": "ZIP",
    "county": "County",
    "country": "Country",
}


def protected_fields_display(item):
    """Return human-readable locally protected fields that differ from this import."""
    fields = item.get("protected_job_fields") or ()
    return ", ".join(PROTECTED_FIELD_LABELS.get(field, field) for field in fields)


def preview_summary(preview):
    """Return compact Matterport intake preview totals for display and testing."""
    counts = preview.get("counts", {})
    items = preview.get("items", [])
    return {
        "jobs": len(items),
        "created": int(counts.get("created", 0)),
        "updated": int(counts.get("updated", 0)),
        "skipped": int(counts.get("skipped", 0)),
        "source_rows": sum(int(item.get("source_row_count", 0)) for item in items),
        "changed_source_rows": sum(int(item.get("changed_source_rows", 0)) for item in items),
        "protected_jobs": sum(bool(item.get("protected_job_fields")) for item in items),
        "protected_fields": sum(len(item.get("protected_job_fields") or ()) for item in items),
        "missing_parent": sum(int(item.get("parent_record_count", 0)) == 0 for item in items),
        "multiple_parents": sum(int(item.get("parent_record_count", 0)) > 1 for item in items),
    }


class OpenTableImportWindow(tk.Toplevel):
    """Preview and import Matterport jobs from an OpenTable CSV export."""

    COLUMNS = (
        "action", "external_job_id", "client_name", "project_name", "job_status",
        "scheduled_start_at", "source_row_count", "changed_source_rows", "protected_fields",
        "parent_status",
    )
    HEADINGS = (
        "Action", "Job #", "Client", "Project", "Status", "Scheduled",
        "Source Rows", "Changed Rows", "Protected Local Values", "Parent Record",
    )

    def __init__(self, parent, auth, session, on_imported=None, service=None):
        super().__init__(parent)
        self.auth = auth
        self.session = session
        self.on_imported = on_imported
        self.service = service or OpenTableImportService(auth)
        self.preview_data = None
        self.preview_rows = {}
        self.sort_column = None
        self.sort_descending = False

        self.title("Matterport Job Intake Center")
        self.geometry("1120x700")
        self.minsize(900, 560)
        self.transient(parent.winfo_toplevel())

        body = ttk.Frame(self, padding=PADDING)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Matterport Job Intake Center", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text=(
                "Bring Matterport jobs into Matterport Ops from an OpenTable CSV export. "
                "Review every proposed change before anything is written to the database."
            ),
            style="Status.TLabel",
            wraplength=950,
        ).pack(anchor="w", pady=(0, 12))

        source_frame = ttk.LabelFrame(body, text="Matterport Job Source: OpenTable CSV", padding=8)
        source_frame.pack(fill="x", pady=(0, 10))
        self.path_var = tk.StringVar()
        ttk.Entry(source_frame, textvariable=self.path_var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(source_frame, text="Browse...", command=self.browse).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(source_frame, text="Analyze", command=self.analyze).pack(
            side="left", padx=(6, 0)
        )

        self.summary_var = tk.StringVar(value="No OpenTable CSV analyzed.")
        ttk.Label(body, textvariable=self.summary_var, style="Status.TLabel").pack(
            anchor="w", pady=(0, 8)
        )

        table = ttk.Frame(body)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=self.COLUMNS, show="headings")
        widths = (80, 105, 140, 160, 100, 130, 80, 85, 190, 105)
        anchors = ("w", "w", "w", "w", "w", "w", "e", "e", "w", "w")
        for name, heading, width, anchor in zip(self.COLUMNS, self.HEADINGS, widths, anchors):
            self.tree.heading(name, text=heading, command=lambda column=name: self.sort_by(column))
            self.tree.column(name, width=width, minwidth=65, anchor=anchor)
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(10, 0))
        self.import_button = ttk.Button(
            actions,
            text="Import Matterport Jobs",
            command=self.run_import,
            state="disabled",
        )
        self.import_button.pack(side="right")
        ttk.Button(actions, text="Close", command=self.destroy).pack(side="right", padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def browse(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Select OpenTable Matterport Jobs CSV",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if path:
            self.path_var.set(path)
            self.analyze()

    def analyze(self):
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning(
                "Matterport Job Intake Center", "Select an OpenTable CSV file first.", parent=self
            )
            return
        try:
            preview = self.service.preview(path)
        except EXPECTED_ERRORS as exc:
            self.preview_data = None
            self.import_button.configure(state="disabled")
            messagebox.showerror("Matterport Job Intake Center", str(exc), parent=self)
            return

        self.preview_data = preview
        self.tree.delete(*self.tree.get_children())
        self.preview_rows.clear()
        for index, item in enumerate(preview.get("items", [])):
            parent_count = int(item.get("parent_record_count", 0))
            parent_status = "OK" if parent_count == 1 else (
                "Missing" if parent_count == 0 else f"Multiple ({parent_count})"
            )
            values = (
                item.get("action", ""),
                item.get("external_job_id", ""),
                item.get("client_name", ""),
                item.get("project_name", ""),
                item.get("job_status", ""),
                format_display_datetime(item.get("scheduled_start_at")),
                item.get("source_row_count", 0),
                item.get("changed_source_rows", 0),
                protected_fields_display(item),
                parent_status,
            )
            iid = f"preview-{index}"
            self.preview_rows[iid] = item
            self.tree.insert("", "end", iid=iid, values=values)

        summary = preview_summary(preview)
        warning_parts = []
        if summary["missing_parent"]:
            warning_parts.append(f'{summary["missing_parent"]} missing parent')
        if summary["multiple_parents"]:
            warning_parts.append(f'{summary["multiple_parents"]} multiple parents')
        warnings = "; ".join(warning_parts) if warning_parts else "no parent-record warnings"
        self.summary_var.set(
            f'{summary["jobs"]} Matterport jobs: {summary["created"]} create, '
            f'{summary["updated"]} update, {summary["skipped"]} skip; '
            f'{summary["source_rows"]} source rows, {summary["changed_source_rows"]} changed; '
            f'{summary["protected_fields"]} protected local value(s) on '
            f'{summary["protected_jobs"]} job(s); '
            f'{warnings}.'
        )
        self.import_button.configure(state="normal" if preview.get("items") else "disabled")

    def sort_by(self, column):
        """Sort the preview by a heading while keeping blank values last."""
        if self.sort_column == column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column = column
            self.sort_descending = False

        numeric_columns = {"source_row_count", "changed_source_rows"}

        def value_for(iid):
            item = self.preview_rows[iid]
            if column in numeric_columns:
                value = int(item.get(column, 0))
                return value, value
            if column == "scheduled_start_at":
                value = item.get(column)
                return value, str(value or "")
            value = self.tree.set(iid, column)
            return value, natural_sort_key(value)

        ordered = ordered_tree_items(
            self.tree.get_children(""), value_for, self.sort_descending
        )
        for index, iid in enumerate(ordered):
            self.tree.move(iid, "", index)
        for name, heading in zip(self.COLUMNS, self.HEADINGS):
            indicator = ""
            if name == self.sort_column:
                indicator = " ▼" if self.sort_descending else " ▲"
            self.tree.heading(name, text=heading + indicator)

    def run_import(self):
        if self.preview_data is None:
            return
        counts = self.preview_data.get("counts", {})
        proposed = int(counts.get("created", 0)) + int(counts.get("updated", 0))
        if proposed == 0:
            messagebox.showinfo(
                "Matterport Job Intake Center",
                "All Matterport jobs in this file are already imported and unchanged.",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "Confirm Matterport Job Intake",
            f"Import {proposed} new or changed Matterport job(s)?",
            parent=self,
        ):
            return
        protected = [item for item in self.preview_data.get("items", [])
                     if item.get("action") == "Decision Required"]
        update_protected = False
        if protected:
            labels = "\n".join(
                f"- {item['external_job_id']} ({item.get('existing_job_status')})"
                for item in protected
            )
            choice = messagebox.askyesnocancel(
                "Cancelled or Archived Jobs",
                "These external Job IDs already exist and will not be duplicated:\n\n"
                + labels + "\n\nYes: update source details while preserving lifecycle status.\n"
                "No: leave these Jobs unchanged.\nCancel: stop the import.", parent=self)
            if choice is None:
                return
            update_protected = choice
        try:
            result = self.service.import_csv(
                self.session, self.path_var.get().strip(), update_protected=update_protected
            )
        except EXPECTED_ERRORS as exc:
            messagebox.showerror("Matterport Job Intake Center", str(exc), parent=self)
            return

        messagebox.showinfo(
            "Matterport Job Intake Complete",
            (
                f'Created: {result["created"]}\n'
                f'Updated: {result["updated"]}\n'
                f'Skipped: {result["skipped"]}\n'
                f'Source rows added: {result["source_rows_added"]}\n'
                f'Source rows updated: {result["source_rows_updated"]}'
            ),
            parent=self,
        )
        if self.on_imported:
            self.on_imported()
        self.destroy()


def open_opentable_import(parent, auth, session, on_imported=None):
    """Open and return the modal Matterport Job Intake Center workflow."""
    return OpenTableImportWindow(parent, auth, session, on_imported=on_imported)
