"""Embedded Jobs Manager for day-to-day Matterport operations."""

import re
import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from app.security.user_manager import AuthorizationError
from app.services.jobs_service import JobsService
from app.ui.job_form import changed_fields, show_job_form
from app.ui.styles import PADDING


EXPECTED_ERRORS = (ValueError, LookupError, AuthorizationError, sqlite3.Error)
STATUS_VALUES = (
    "All", "Requested", "Scheduling", "Scheduled", "Assigned", "In Progress",
    "Completed", "Cancelled", "On Hold",
)


def technician_name(job):
    """Build the active primary technician's display name."""
    name = " ".join(
        str(job.get(field) or "").strip()
        for field in ("primary_tech_first_name", "primary_tech_last_name")
        if str(job.get(field) or "").strip()
    )
    return name or job.get("primary_tech_code") or ""


def job_address(job):
    """Return the most useful complete address available for details screens."""
    if job.get("capture_address_raw"):
        return str(job["capture_address_raw"])
    parts = [job.get("address_1"), job.get("city"), job.get("state"), job.get("postal_code")]
    return ", ".join(str(value).strip() for value in parts if value)


def job_location_parts(job):
    """Return street, city, and state, using the raw address as a fallback."""
    street = str(job.get("address_1") or "").strip()
    city = str(job.get("city") or "").strip()
    state = str(job.get("state") or "").strip().upper()
    raw = str(job.get("capture_address_raw") or "").strip()

    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not street and parts:
        street = parts[0]
    if not city and len(parts) >= 2:
        city = parts[1]
    if not state:
        for part in reversed(parts[2:]):
            match = re.search(r"\b([A-Za-z]{2})\b(?:\s+\d{5}(?:-\d{4})?)?$", part)
            if match:
                state = match.group(1).upper()
                break

    return street, city, state


def client_name(job):
    return job.get("client_name_source") or job.get("project_client_name") or ""


def project_name(job):
    return job.get("project_name_source") or job.get("project_name") or ""


def format_currency(value):
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def format_datetime(value):
    """Format an ISO database date/time for display without changing storage."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.hour == 0 and parsed.minute == 0 and "T" not in text and " " not in text:
        return parsed.strftime("%m-%d-%Y")
    hour = parsed.strftime("%I").lstrip("0") or "12"
    return f"{parsed.strftime('%m-%d-%Y')} {hour}:{parsed.strftime('%M %p')}"


def natural_sort_key(value):
    """Sort mixed text and numeric identifiers naturally."""
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(value or ""))
    )


class JobsController:
    """UI-facing JobsService adapter that can be tested without Tk widgets."""

    def __init__(self, service, session):
        self.service, self.session = service, session

    @property
    def can_modify(self):
        return self.session.role in {"admin", "operator"}

    def load(self, query="", status="All"):
        status_filter = None if status == "All" else status
        if query.strip():
            return self.service.search_jobs(query, status_filter)
        return self.service.list_jobs(status_filter)

    def create(self, data):
        data = dict(data)
        tech_id = data.pop("technician_id", None)
        return self.service.create_job(self.session, data, tech_id)

    def update(self, job_id, original, submitted):
        changes = changed_fields(original, submitted)
        tech_id = submitted.get("technician_id")
        if not changes and tech_id == original.get("technician_id"):
            return None
        return self.service.update_job(self.session, job_id, changes, tech_id)


class JobsManager(ttk.Frame):
    """Searchable operational Job grid with basic create and edit actions."""

    COLUMNS = (
        "external_job_id", "client", "project", "address", "city", "state",
        "scheduled_start_at", "technician", "job_status", "expected_payout",
    )
    HEADINGS = (
        "Job #", "Client", "Project", "Address", "City", "State", "Scheduled",
        "Primary Technician", "Status", "Expected Payout",
    )

    def __init__(self, parent, auth, session, service=None):
        super().__init__(parent, padding=PADDING, style="App.TFrame")
        self.controller = JobsController(service or JobsService(auth), session)
        self.rows = {}
        self.sort_column = None
        self.sort_descending = False

        ttk.Label(self, text="Jobs", style="Header.TLabel").pack(anchor="w", pady=(0, 10))

        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=(0, 8))
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="All")

        ttk.Label(filters, text="Search:").pack(side="left")
        search = ttk.Entry(filters, textvariable=self.search_var, width=34)
        search.pack(side="left", padx=(6, 12))
        search.bind("<Return>", lambda _event: self.refresh())

        ttk.Label(filters, text="Status:").pack(side="left")
        status = ttk.Combobox(
            filters,
            textvariable=self.status_var,
            values=STATUS_VALUES,
            state="readonly",
            width=15,
        )
        status.pack(side="left", padx=6)
        status.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Button(filters, text="Search", command=self.refresh).pack(side="left", padx=(6, 0))
        ttk.Button(filters, text="Clear", command=self.clear_filters).pack(side="left", padx=6)
        ttk.Button(filters, text="Refresh", command=self.refresh).pack(side="left")

        table = ttk.Frame(self)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table, columns=self.COLUMNS, show="headings", selectmode="browse"
        )
        widths = (105, 140, 140, 190, 120, 65, 150, 140, 100, 110)
        anchors = ("w", "w", "w", "w", "w", "w", "w", "w", "w", "e")
        for name, heading, width, anchor in zip(
            self.COLUMNS, self.HEADINGS, widths, anchors
        ):
            self.tree.heading(
                name,
                text=heading,
                command=lambda column=name: self.sort_by(column),
            )
            self.tree.column(name, width=width, minwidth=55, anchor=anchor)

        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _event: self.edit_or_view())

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(8, 0))
        self.add_button = ttk.Button(actions, text="Add Job", command=self.add)
        self.add_button.pack(side="left", padx=(0, 6))
        self.edit_button = ttk.Button(actions, text="Edit Job", command=self.edit)
        self.edit_button.pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="View Details", command=self.view_details).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(actions, text="Refresh", command=self.refresh).pack(side="left")

        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, style="Status.TLabel").pack(
            anchor="w", pady=(7, 0)
        )

        if not self.controller.can_modify:
            self.add_button.configure(state="disabled")
            self.edit_button.configure(state="disabled")

        self.refresh()

    def clear_filters(self):
        self.search_var.set("")
        self.status_var.set("All")
        self.refresh()

    def sort_by(self, column):
        """Sort the currently displayed rows, toggling direction per header click."""
        if self.sort_column == column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column = column
            self.sort_descending = False
        self._apply_sort()

    def _apply_sort(self):
        """Apply the current sort without changing its direction."""
        column = self.sort_column
        if not column:
            return

        def key_for(iid):
            row = self.rows[iid]
            if column == "scheduled_start_at":
                value = row.get("scheduled_start_at")
                try:
                    return (value in (None, ""), datetime.fromisoformat(str(value)))
                except ValueError:
                    return (True, datetime.max)
            if column == "expected_payout":
                try:
                    return float(row.get("expected_payout") or 0)
                except (TypeError, ValueError):
                    return 0.0
            street, city, state = job_location_parts(row)
            values = {
                "external_job_id": row.get("external_job_id"),
                "client": client_name(row),
                "project": project_name(row),
                "address": street,
                "city": city,
                "state": state,
                "technician": technician_name(row),
                "job_status": row.get("job_status"),
            }
            return natural_sort_key(values.get(column))

        ordered = sorted(
            self.tree.get_children(""),
            key=key_for,
            reverse=self.sort_descending,
        )
        for index, iid in enumerate(ordered):
            self.tree.move(iid, "", index)

        for name, heading in zip(self.COLUMNS, self.HEADINGS):
            indicator = ""
            if name == self.sort_column:
                indicator = " ▼" if self.sort_descending else " ▲"
            self.tree.heading(name, text=heading + indicator)

    def refresh(self, select_id=None):
        try:
            rows = self.controller.load(self.search_var.get(), self.status_var.get())
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return

        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for row in rows:
            job_id = int(row["job_id"])
            iid = f"job-{job_id}"
            self.rows[iid] = row
            street, city, state = job_location_parts(row)
            visible = {
                "external_job_id": row.get("external_job_id") or "",
                "client": client_name(row),
                "project": project_name(row),
                "address": street,
                "city": city,
                "state": state,
                "scheduled_start_at": format_datetime(row.get("scheduled_start_at")),
                "technician": technician_name(row),
                "job_status": row.get("job_status") or "",
                "expected_payout": format_currency(row.get("expected_payout")),
            }
            self.tree.insert(
                "", "end", iid=iid, values=[visible[column] for column in self.COLUMNS]
            )

        if self.sort_column:
            self._apply_sort()

        message = f"{len(rows)} job(s) found." if rows else "No jobs found."
        if len(rows) >= 500:
            message += " Showing the first 500 records."
        self.status.set(message)

        iid = f"job-{select_id}" if select_id else None
        if iid and self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)

    def selected(self, warn=True):
        selection = self.tree.selection()
        if not selection:
            if warn:
                messagebox.showwarning("Jobs", "Select a job first.", parent=self)
            return None
        return self.rows.get(selection[0])

    def _error(self, exc):
        messagebox.showerror("Jobs", str(exc), parent=self)

    def add(self):
        try:
            markets = self.controller.service.list_market_options()
            technicians = self.controller.service.list_active_technician_options()
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        data = show_job_form(self, markets=markets, technicians=technicians)
        if data is None:
            return
        try:
            job_id = self.controller.create(data)
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        self.refresh(job_id)
        self.status.set("Job added successfully.")

    def edit_or_view(self):
        if self.controller.can_modify:
            self.edit()
        else:
            self.view_details()

    def edit(self):
        row = self.selected()
        if not row:
            return
        job_id = int(row["job_id"])
        try:
            original = self.controller.service.get_job(job_id)
            markets = self.controller.service.list_market_options()
            technicians = self.controller.service.list_active_technician_options()
            assignment = self.controller.service.get_current_primary_assignment(job_id)
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        if original is None:
            self._error(LookupError("Job not found"))
            return
        original["technician_id"] = assignment["tech_id"] if assignment else None
        if assignment and assignment["status"] != "Active":
            technicians = [*technicians, assignment]

        submitted = show_job_form(self, original, markets, technicians)
        if submitted is None:
            return
        try:
            result = self.controller.update(job_id, original, submitted)
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        self.refresh(job_id)
        self.status.set("Job updated." if result else "No changes were made.")

    def view_details(self):
        row = self.selected()
        if not row:
            return
        try:
            job = self.controller.service.get_job(int(row["job_id"]))
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        if not job:
            self._error(LookupError("Job not found"))
            return
        JobDetails(self, job)


class JobDetails:
    """Read-only operational summary for one Job."""

    def __init__(self, parent, job):
        self.window = tk.Toplevel(parent)
        self.window.title(f"Job {job.get('external_job_id') or ''}")
        self.window.geometry("760x620")
        self.window.minsize(650, 500)

        body = ttk.Frame(self.window, padding=PADDING)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=f"Job {job.get('external_job_id') or ''}",
            style="Header.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            body,
            text=f"{job.get('job_status') or '—'}  •  {client_name(job) or 'No client'}",
        ).pack(anchor="w", pady=(0, 12))

        sections = (
            ("Project", (
                ("Project Code", job.get("project_code")),
                ("Project", project_name(job)),
                ("Client", client_name(job)),
            )),
            ("Schedule", (
                ("Request Received", format_datetime(job.get("request_received_at"))),
                ("Scheduled Start", format_datetime(job.get("scheduled_start_at"))),
                ("Actual Start", format_datetime(job.get("actual_start_at"))),
                ("Completed", format_datetime(job.get("completed_at"))),
            )),
            ("Location", (
                ("Capture Address", job_address(job)),
                ("County", job.get("county")),
                ("Country", job.get("country")),
                ("Capture Size", job.get("requested_capture_size")),
            )),
            ("Assignment", (
                ("Primary Technician", technician_name(job)),
                ("Assignment Status", job.get("primary_assignment_status")),
                ("Expected Payout", format_currency(job.get("expected_payout"))),
            )),
            ("On-site Contact", (
                ("Name", job.get("onsite_contact_name")),
                ("Email", job.get("onsite_contact_email")),
                ("Phone", job.get("onsite_contact_phone")),
            )),
        )

        grid = ttk.Frame(body)
        grid.pack(fill="x")
        for index, (title, values) in enumerate(sections):
            frame = ttk.LabelFrame(grid, text=title, padding=8)
            frame.grid(
                row=index // 2, column=index % 2, sticky="nsew", padx=4, pady=4
            )
            for label, value in values:
                ttk.Label(
                    frame,
                    text=f"{label}: {value if value not in (None, '') else '—'}",
                    wraplength=315,
                ).pack(anchor="w", pady=1)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        notes_frame = ttk.LabelFrame(body, text="Internal Notes", padding=8)
        notes_frame.pack(fill="both", expand=True, padx=4, pady=(8, 4))
        notes = tk.Text(notes_frame, height=7, wrap="word")
        notes.pack(fill="both", expand=True)
        notes.insert("1.0", job.get("internal_notes") or "")
        notes.configure(state="disabled")

        ttk.Button(body, text="Close", command=self.window.destroy).pack(
            anchor="e", pady=(8, 0)
        )
        self.window.transient(parent.winfo_toplevel())
        self.window.grab_set()
        self.window.focus_set()
