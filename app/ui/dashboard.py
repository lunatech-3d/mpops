"""Operational dashboard."""
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk

from app.date_utils import display_date_to_iso, format_display_date, format_display_datetime
from app.services.jobs_service import JobsService
from app.ui.jobs_manager import (
    EXPECTED_ERRORS, JobDetails, format_currency, job_location_parts, job_sort_key,
    technician_name,
)
from app.ui.styles import PADDING

AREAS = ("Upcoming Jobs", "Jobs Awaiting Assignment", "Technician Payments Due", "Unreconciled Payments")


class ActivityTree(ttk.Frame):
    """The dashboard's reusable sortable Job Activity tree."""

    COLUMNS = (
        "external_job_id", "address", "city", "state", "scheduled_start_at",
        "technician", "job_status", "expected_payout",
    )
    HEADINGS = (
        "Job #", "Address", "City", "State", "Scheduled Time", "Technician",
        "Status", "Expected Payout",
    )

    def __init__(self, parent, service):
        super().__init__(parent, padding=(0, PADDING, 0, 0))
        self.service = service
        self.rows = {}
        self.sort_column = "scheduled_start_at"
        self.sort_descending = False

        table = ttk.Frame(self)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=self.COLUMNS, show="headings", selectmode="browse")
        widths = (95, 210, 120, 60, 155, 150, 105, 125)
        stretches = (False, True, True, False, False, True, True, False)
        for name, heading, width, stretch in zip(self.COLUMNS, self.HEADINGS, widths, stretches):
            self.tree.heading(name, text=heading, command=lambda column=name: self.sort_by(column))
            self.tree.column(
                name, width=width, minwidth=55, stretch=stretch,
                anchor="e" if name == "expected_payout" else "w",
            )
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", self.open_selected)

    def refresh(self, start, end):
        rows = self.service.list_job_activity_range(start, end)
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        total = 0.0
        for row in rows:
            iid = f"job-{row['job_id']}"
            self.rows[iid] = row
            street, city, state = job_location_parts(row)
            try:
                total += float(row.get("expected_payout") or 0)
            except (TypeError, ValueError):
                pass
            values = (
                row.get("external_job_id") or "", street, city, state,
                format_display_datetime(row.get("scheduled_start_at")), technician_name(row),
                row.get("job_status") or "", format_currency(row.get("expected_payout")),
            )
            self.tree.insert("", "end", iid=iid, values=values)
        self._apply_sort()
        technicians = {
            row.get("primary_technician_id") for row in rows
            if row.get("primary_technician_id") is not None
        }
        return {
            "jobs": len(self.rows), "technicians": len(technicians),
            "expected_payout": total,
        }

    def sort_by(self, column):
        self.sort_descending = not self.sort_descending if column == self.sort_column else False
        self.sort_column = column
        self._apply_sort()

    def _apply_sort(self):
        children = sorted(
            self.tree.get_children(),
            key=lambda iid: job_sort_key(self.rows[iid], self.sort_column),
            reverse=self.sort_descending,
        )
        for index, iid in enumerate(children):
            self.tree.move(iid, "", index)
        for name, heading in zip(self.COLUMNS, self.HEADINGS):
            marker = (" ▼" if self.sort_descending else " ▲") if name == self.sort_column else ""
            self.tree.heading(name, text=heading + marker)

    def open_selected(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        try:
            job = self.service.get_job(int(self.rows[selection[0]]["job_id"]))
        except EXPECTED_ERRORS as exc:
            messagebox.showerror("Jobs", str(exc), parent=self)
            return
        if job:
            JobDetails(self, job)


class JobActivity(ttk.LabelFrame):
    """A single scheduled-job tree controlled by local calendar filters."""

    FILTERS = (
        ("today", "Today"), ("week", "This Week"), ("next_week", "Next Week"),
        ("month", "This Month"), ("next_month", "Next Month"),
    )

    def __init__(self, parent, service):
        super().__init__(parent, text="Job Activity", padding=PADDING)
        self.service = service
        self.active_filter = "today"
        self.custom_range = None
        filters = ttk.Frame(self)
        filters.pack(fill="x")
        self.filter_buttons = {}
        for period, label in self.FILTERS:
            button = ttk.Button(filters, text=label, command=lambda value=period: self.select_filter(value))
            button.pack(side="left", padx=(0, 6))
            self.filter_buttons[period] = button
        custom = ttk.Button(filters, text="Custom...", command=self.open_custom_range)
        custom.pack(side="left")
        self.filter_buttons["custom"] = custom

        self.range_label = ttk.Label(self, style="Status.TLabel")
        self.range_label.pack(anchor="w", pady=(8, 4))
        summary = ttk.Frame(self)
        summary.pack(fill="x", pady=(0, 2))
        self.summary_labels = {}
        for key, label in (("jobs", "Jobs"), ("technicians", "Technicians"),
                           ("revenue", "Expected Revenue"),
                           ("payout", "Expected Technician Payout")):
            ttk.Label(summary, text=f"{label}:").pack(side="left", padx=(0 if key == "jobs" else 18, 4))
            value = ttk.Label(summary, text="0", font=("Segoe UI", 10, "bold"))
            value.pack(side="left")
            self.summary_labels[key] = value
        self.tree_view = ActivityTree(self, service)
        self.tree_view.pack(fill="both", expand=True)
        self.refresh()

    @staticmethod
    def _preset_range(period, today=None):
        today = today or datetime.now().astimezone().date()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        next_month = (month_start.replace(year=today.year + 1, month=1)
                      if today.month == 12 else month_start.replace(month=today.month + 1))
        after_next = (next_month.replace(year=next_month.year + 1, month=1)
                      if next_month.month == 12 else next_month.replace(month=next_month.month + 1))
        return {
            "today": (today, today),
            "week": (week_start, week_start + timedelta(days=6)),
            "next_week": (week_start + timedelta(days=7), week_start + timedelta(days=13)),
            "month": (month_start, next_month - timedelta(days=1)),
            "next_month": (next_month, after_next - timedelta(days=1)),
        }[period]

    def select_filter(self, period):
        self.active_filter = period
        self.refresh()

    def refresh(self):
        try:
            start, end = (self.custom_range if self.active_filter == "custom"
                          else self._preset_range(self.active_filter))
            totals = self.tree_view.refresh(start, end)
            self.range_label.configure(
                text=f"{format_display_date(start)} through {format_display_date(end)}"
            )
            for name, button in self.filter_buttons.items():
                button.state(["pressed"] if name == self.active_filter else ["!pressed"])
            self.summary_labels["jobs"].configure(text=str(totals["jobs"]))
            self.summary_labels["technicians"].configure(text=str(totals["technicians"]))
            # No pre-payment billed/expected revenue field currently exists in the
            # Jobs or JobFinancials schema; displaying a guessed amount would be misleading.
            self.summary_labels["revenue"].configure(text="Not available")
            self.summary_labels["payout"].configure(text=format_currency(totals["expected_payout"]))
        except EXPECTED_ERRORS as exc:
            messagebox.showerror("Job Activity", str(exc), parent=self)

    def open_custom_range(self):
        dialog = tk.Toplevel(self)
        dialog.title("Custom Job Activity Range")
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=PADDING)
        body.pack(fill="both", expand=True)
        default_start, default_end = (self.custom_range or self._preset_range("today"))
        values = {}
        for row, (label, value) in enumerate((("From Date", default_start), ("To Date", default_end))):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            variable = tk.StringVar(value=format_display_date(value))
            ttk.Entry(body, textvariable=variable, width=14).grid(row=row, column=1, pady=4)
            values[label] = variable
        ttk.Label(body, text="Use MM/DD/YYYY", style="Status.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(2, 8))

        def accept():
            try:
                start_text = display_date_to_iso(values["From Date"].get())
                end_text = display_date_to_iso(values["To Date"].get())
                if not start_text or not end_text:
                    raise ValueError("From Date and To Date are required.")
                start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
                if start > end:
                    raise ValueError("From Date cannot be after To Date.")
            except ValueError as exc:
                messagebox.showerror("Custom Date Range", str(exc), parent=dialog)
                return
            self.custom_range = (start, end)
            self.active_filter = "custom"
            dialog.destroy()
            self.refresh()

        actions = ttk.Frame(body)
        actions.grid(row=3, column=0, columnspan=2, sticky="e")
        ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
        ttk.Button(actions, text="Apply", command=accept).pack(side="left")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)


def build_dashboard(parent, session, auth):
    frame = ttk.Frame(parent, style="App.TFrame", padding=PADDING)
    ttk.Label(frame, text="Dashboard", style="Header.TLabel").grid(row=0, column=0, sticky="w")
    for index, title in enumerate(AREAS):
        card = ttk.LabelFrame(frame, text=title, padding=PADDING)
        card.grid(row=1 + index // 2, column=index % 2, padx=6, pady=6, sticky="nsew")
        ttk.Label(card, text="0", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(card, text="Not yet available").pack(anchor="w")
    activity = JobActivity(frame, JobsService(auth))
    activity.grid(row=3, column=0, columnspan=2, padx=6, pady=12, sticky="nsew")
    ttk.Button(frame, text="Refresh", command=activity.refresh).grid(row=0, column=1, sticky="e", padx=6)
    ttk.Label(frame, text="Operational modules will be added incrementally.", style="Status.TLabel").grid(
        row=4, column=0, columnspan=2, sticky="w", padx=6)
    frame.columnconfigure((0, 1), weight=1)
    frame.rowconfigure(3, weight=1)
    return frame
