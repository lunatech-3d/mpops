"""Operational dashboard."""
from tkinter import messagebox, ttk

from app.date_utils import format_display_datetime
from app.services.jobs_service import JobsService
from app.ui.jobs_manager import (
    EXPECTED_ERRORS, JobDetails, format_currency, job_location_parts, job_sort_key,
    technician_name,
)
from app.ui.styles import PADDING

AREAS = ("Upcoming Jobs", "Jobs Awaiting Assignment", "Technician Payments Due", "Unreconciled Payments")


class ActivityTree(ttk.Frame):
    """One sortable date-range view within the Job Activity notebook."""

    COLUMNS = (
        "external_job_id", "address", "city", "state", "scheduled_start_at",
        "technician", "job_status", "expected_payout",
    )
    HEADINGS = (
        "Job #", "Address", "City", "State", "Scheduled Time", "Technician",
        "Status", "Expected Payout",
    )

    def __init__(self, parent, service, period):
        super().__init__(parent, padding=(0, PADDING, 0, 0))
        self.service, self.period = service, period
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

        summary = ttk.Frame(self)
        summary.pack(fill="x", pady=(8, 0))
        ttk.Label(summary, text="Expected Technician Payout").pack(side="left")
        self.payout = ttk.Label(summary, text="$0.00", font=("Segoe UI", 14, "bold"))
        self.payout.pack(side="right")

    def refresh(self):
        rows = self.service.list_job_activity(self.period)
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
        self.payout.configure(text=format_currency(total))
        self._apply_sort()

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
    """Tabbed scheduled-job lists for the current day, week, and month."""

    def __init__(self, parent, service):
        super().__init__(parent, text="Job Activity", padding=PADDING)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.tabs = {}
        for period, label in (("today", "Today"), ("week", "This Week"), ("month", "This Month")):
            tab = ActivityTree(self.notebook, service, period)
            self.notebook.add(tab, text=label)
            self.tabs[period] = tab
        self.refresh()

    def refresh(self):
        try:
            for tab in self.tabs.values():
                tab.refresh()
        except EXPECTED_ERRORS as exc:
            messagebox.showerror("Job Activity", str(exc), parent=self)


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
