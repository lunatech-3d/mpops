"""Matterport Ops Operations Center dispatch workspace."""

from __future__ import annotations

from collections import Counter
from datetime import date
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from app.security.user_manager import AuthorizationError
from app.services.jobs_service import JobsService
from app.ui.job_form import changed_fields, show_job_form
from app.ui.styles import PADDING

EXPECTED_ERRORS = (ValueError, LookupError, AuthorizationError, sqlite3.Error)
STATUS_VALUES = (
    "All", "Requested", "Scheduling", "Scheduled", "Assigned",
    "In Progress", "Completed", "Cancelled", "On Hold",
)


def _client(job):
    return job.get("client_name_source") or job.get("project_client_name") or ""


def _project(job):
    return job.get("project_name_source") or job.get("project_name") or ""


def _technician(job):
    name = " ".join(
        str(job.get(field) or "").strip()
        for field in ("primary_tech_first_name", "primary_tech_last_name")
        if str(job.get(field) or "").strip()
    )
    return name or job.get("primary_tech_code") or "Unassigned"


def _address(job):
    if job.get("capture_address_raw"):
        return str(job["capture_address_raw"])
    return ", ".join(
        str(value).strip()
        for value in (job.get("address_1"), job.get("city"), job.get("state"), job.get("postal_code"))
        if value
    )


def _money(value):
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _today_job(job):
    value = str(job.get("scheduled_start_at") or "")
    return value[:10] == date.today().isoformat()


class OperationsController:
    def __init__(self, service, session):
        self.service, self.session = service, session

    @property
    def can_modify(self):
        return self.session.role in {"admin", "operator"}

    def load(self, query="", status="All"):
        selected = None if status == "All" else status
        return (
            self.service.search_jobs(query, selected)
            if query.strip()
            else self.service.list_jobs(selected)
        )

    def all_jobs(self):
        return self.service.list_jobs(limit=2000)

    def create(self, data):
        data = dict(data)
        data.pop("primary_technician_id", None)
        return self.service.create_job(self.session, data)

    def update(self, job_id, original, submitted):
        changes = changed_fields(original, submitted)
        return self.service.update_job(self.session, job_id, changes) if changes else None

    def set_status(self, job_id, status):
        return self.service.update_job(self.session, job_id, {"job_status": status})


class OperationsCenter(ttk.Frame):
    COLUMNS = (
        "external_job_id", "client", "project", "scheduled", "technician",
        "status", "address", "payout",
    )
    HEADINGS = (
        "Job #", "Client", "Project", "Scheduled", "Technician",
        "Status", "Capture Address", "Expected Payout",
    )

    def __init__(self, parent, auth, session, service=None):
        super().__init__(parent, padding=PADDING, style="App.TFrame")
        self.controller = OperationsController(service or JobsService(auth), session)
        self.rows = {}
        self.sort_column = "scheduled"
        self.sort_reverse = False

        title = ttk.Frame(self)
        title.pack(fill="x", pady=(0, 8))
        ttk.Label(title, text="Operations Center", style="Header.TLabel").pack(side="left")
        self.summary_text = tk.StringVar(value="Matterport dispatch workspace")
        ttk.Label(title, textvariable=self.summary_text, style="Status.TLabel").pack(side="right")

        self.counter_frame = ttk.Frame(self)
        self.counter_frame.pack(fill="x", pady=(0, 10))
        self.counter_vars = {}
        for label in ("Requested", "Scheduling", "Assigned", "Today", "Completed", "Unassigned"):
            frame = ttk.LabelFrame(self.counter_frame, text=label, padding=(12, 6))
            frame.pack(side="left", fill="x", expand=True, padx=(0, 6))
            variable = tk.StringVar(value="0")
            self.counter_vars[label] = variable
            ttk.Label(frame, textvariable=variable, style="Header.TLabel").pack()
            frame.bind("<Button-1>", lambda _event, value=label: self.apply_counter(value))
            for child in frame.winfo_children():
                child.bind("<Button-1>", lambda _event, value=label: self.apply_counter(value))

        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=(0, 8))
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="All")
        ttk.Label(filters, text="Search:").pack(side="left")
        search = ttk.Entry(filters, textvariable=self.search_var, width=32)
        search.pack(side="left", padx=(6, 12))
        search.bind("<Return>", lambda _event: self.refresh())
        ttk.Label(filters, text="Status:").pack(side="left")
        status = ttk.Combobox(filters, textvariable=self.status_var, values=STATUS_VALUES,
                              state="readonly", width=15)
        status.pack(side="left", padx=6)
        status.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        ttk.Button(filters, text="Search", command=self.refresh).pack(side="left", padx=(6, 0))
        ttk.Button(filters, text="Clear", command=self.clear_filters).pack(side="left", padx=6)
        ttk.Button(filters, text="Refresh", command=self.refresh).pack(side="left")

        workspace = ttk.Panedwindow(self, orient="horizontal")
        workspace.pack(fill="both", expand=True)

        grid_frame = ttk.Frame(workspace)
        detail_frame = ttk.LabelFrame(workspace, text="Selected Job", padding=10)
        workspace.add(grid_frame, weight=4)
        workspace.add(detail_frame, weight=2)

        self.tree = ttk.Treeview(grid_frame, columns=self.COLUMNS, show="headings", selectmode="browse")
        widths = (90, 125, 130, 125, 125, 95, 220, 105)
        anchors = ("w", "w", "w", "w", "w", "w", "w", "e")
        for column, heading, width, anchor in zip(self.COLUMNS, self.HEADINGS, widths, anchors):
            self.tree.heading(column, text=heading, command=lambda c=column: self.sort_by(c))
            self.tree.column(column, width=width, minwidth=65, anchor=anchor)
        ybar = ttk.Scrollbar(grid_frame, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(grid_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        grid_frame.rowconfigure(0, weight=1)
        grid_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.show_selected_summary())
        self.tree.bind("<Double-1>", lambda _event: self.edit())

        self.detail_title = tk.StringVar(value="Select a job")
        ttk.Label(detail_frame, textvariable=self.detail_title, style="Header.TLabel").pack(anchor="w")
        self.detail_status = tk.StringVar(value="")
        ttk.Label(detail_frame, textvariable=self.detail_status, style="Status.TLabel").pack(anchor="w", pady=(0, 8))
        self.detail_text = tk.Text(detail_frame, width=38, wrap="word", height=20)
        self.detail_text.pack(fill="both", expand=True)
        self.detail_text.configure(state="disabled")

        quick = ttk.LabelFrame(detail_frame, text="Quick Status", padding=6)
        quick.pack(fill="x", pady=(8, 0))
        self.quick_status_var = tk.StringVar(value="Requested")
        self.quick_status = ttk.Combobox(
            quick, textvariable=self.quick_status_var,
            values=STATUS_VALUES[1:], state="readonly", width=16,
        )
        self.quick_status.pack(side="left", fill="x", expand=True)
        self.apply_status_button = ttk.Button(quick, text="Apply", command=self.apply_quick_status)
        self.apply_status_button.pack(side="left", padx=(6, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(8, 0))
        self.add_button = ttk.Button(actions, text="Add Job", command=self.add)
        self.add_button.pack(side="left", padx=(0, 6))
        self.edit_button = ttk.Button(actions, text="Edit Job", command=self.edit)
        self.edit_button.pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Refresh", command=self.refresh).pack(side="left")
        self.status_line = tk.StringVar()
        ttk.Label(actions, textvariable=self.status_line, style="Status.TLabel").pack(side="right")

        if not self.controller.can_modify:
            for widget in (self.add_button, self.edit_button, self.quick_status, self.apply_status_button):
                widget.configure(state="disabled")

        self.refresh()

    def selected(self, warn=True):
        selection = self.tree.selection()
        if not selection:
            if warn:
                messagebox.showwarning("Operations Center", "Select a job first.", parent=self)
            return None
        return self.rows.get(selection[0])

    def clear_filters(self):
        self.search_var.set("")
        self.status_var.set("All")
        self.refresh()

    def apply_counter(self, label):
        if label in STATUS_VALUES:
            self.status_var.set(label)
            self.refresh()
        elif label == "Today":
            self.status_var.set("All")
            self.refresh(today_only=True)
        elif label == "Unassigned":
            self.status_var.set("All")
            self.refresh(unassigned_only=True)

    def refresh(self, select_id=None, today_only=False, unassigned_only=False):
        try:
            rows = self.controller.load(self.search_var.get(), self.status_var.get())
            all_rows = self.controller.all_jobs()
        except EXPECTED_ERRORS as exc:
            messagebox.showerror("Operations Center", str(exc), parent=self)
            return
        if today_only:
            rows = [row for row in rows if _today_job(row)]
        if unassigned_only:
            rows = [row for row in rows if not row.get("primary_assignment_id")]
        self._update_counters(all_rows)
        self._populate(rows, select_id)

    def _update_counters(self, rows):
        counts = Counter(str(row.get("job_status") or "") for row in rows)
        for label in ("Requested", "Scheduling", "Assigned", "Completed"):
            self.counter_vars[label].set(str(counts.get(label, 0)))
        self.counter_vars["Today"].set(str(sum(1 for row in rows if _today_job(row))))
        self.counter_vars["Unassigned"].set(str(sum(1 for row in rows if not row.get("primary_assignment_id"))))
        self.summary_text.set(f"{len(rows)} total jobs")

    def _populate(self, rows, select_id=None):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for row in rows:
            job_id = int(row["job_id"])
            iid = f"job-{job_id}"
            self.rows[iid] = row
            values = (
                row.get("external_job_id") or "",
                _client(row),
                _project(row),
                row.get("scheduled_start_at") or "",
                _technician(row),
                row.get("job_status") or "",
                _address(row),
                _money(row.get("expected_payout")),
            )
            self.tree.insert("", "end", iid=iid, values=values)
        self.status_line.set(f"{len(rows)} job(s) displayed")
        iid = f"job-{select_id}" if select_id else None
        if iid and self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
        elif rows:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
        else:
            self._render_details(None)

    def sort_by(self, column):
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column, self.sort_reverse = column, False
        items = list(self.tree.get_children())
        index = self.COLUMNS.index(column)
        items.sort(key=lambda iid: str(self.tree.item(iid, "values")[index]).lower(),
                   reverse=self.sort_reverse)
        for position, iid in enumerate(items):
            self.tree.move(iid, "", position)

    def show_selected_summary(self):
        self._render_details(self.selected(warn=False))

    def _render_details(self, job):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        if not job:
            self.detail_title.set("Select a job")
            self.detail_status.set("")
            self.detail_text.insert("1.0", "Choose a row to view its operational summary.")
            self.detail_text.configure(state="disabled")
            return
        self.detail_title.set(f"Job {job.get('external_job_id') or ''}")
        self.detail_status.set(job.get("job_status") or "")
        self.quick_status_var.set(job.get("job_status") or "Requested")
        lines = [
            f"Client: {_client(job) or '—'}",
            f"Project: {_project(job) or '—'}",
            f"Scheduled: {job.get('scheduled_start_at') or '—'}",
            f"Technician: {_technician(job)}",
            f"Address: {_address(job) or '—'}",
            "",
            "On-site Contact",
            f"Name: {job.get('onsite_contact_name') or '—'}",
            f"Email: {job.get('onsite_contact_email') or '—'}",
            f"Phone: {job.get('onsite_contact_phone') or '—'}",
            "",
            f"Capture Size: {job.get('requested_capture_size') or '—'}",
            f"Expected Payout: {_money(job.get('expected_payout'))}",
            "",
            "Internal Notes",
            job.get("internal_notes") or "—",
        ]
        self.detail_text.insert("1.0", "\n".join(str(line) for line in lines))
        self.detail_text.configure(state="disabled")

    def add(self):
        data = show_job_form(self)
        if data is None:
            return
        try:
            job_id = self.controller.create(data)
        except EXPECTED_ERRORS as exc:
            messagebox.showerror("Operations Center", str(exc), parent=self)
            return
        self.refresh(job_id)

    def edit(self):
        row = self.selected()
        if not row:
            return
        job_id = int(row["job_id"])
        try:
            original = self.controller.service.get_job(job_id)
        except EXPECTED_ERRORS as exc:
            messagebox.showerror("Operations Center", str(exc), parent=self)
            return
        if not original:
            messagebox.showerror("Operations Center", "Job not found.", parent=self)
            return
        submitted = show_job_form(self, original)
        if submitted is None:
            return
        try:
            self.controller.update(job_id, original, submitted)
        except EXPECTED_ERRORS as exc:
            messagebox.showerror("Operations Center", str(exc), parent=self)
            return
        self.refresh(job_id)

    def apply_quick_status(self):
        row = self.selected()
        if not row:
            return
        new_status = self.quick_status_var.get()
        if new_status == row.get("job_status"):
            return
        try:
            self.controller.set_status(int(row["job_id"]), new_status)
        except EXPECTED_ERRORS as exc:
            messagebox.showerror("Operations Center", str(exc), parent=self)
            return
        self.refresh(int(row["job_id"]))
