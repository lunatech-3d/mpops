"""Operational dashboard."""
from tkinter import ttk

from app.services.jobs_service import JobsService
from app.ui.styles import PADDING

AREAS = ("Upcoming Jobs", "Jobs Awaiting Assignment", "Technician Payments Due", "Unreconciled Payments")


class JobActivity(ttk.LabelFrame):
    """Compact, refreshable scheduled-job totals."""

    def __init__(self, parent, service):
        super().__init__(parent, text="Job Activity", padding=PADDING)
        self.service = service
        self.values = {}
        for row, (key, label) in enumerate((
            ("today", "Today"), ("week", "This Week"), ("month", "This Month"),
        )):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=3)
            value = ttk.Label(self, text="0", font=("Segoe UI", 18, "bold"))
            value.grid(row=row, column=1, sticky="e", padx=(36, 0), pady=3)
            self.values[key] = value
        self.columnconfigure(0, weight=1)
        self.refresh()

    def refresh(self):
        counts = self.service.get_job_activity_counts()
        for key, label in self.values.items():
            label.configure(text=str(counts[key]))


def build_dashboard(parent, session, auth):
    frame = ttk.Frame(parent, style="App.TFrame", padding=PADDING)
    ttk.Label(frame, text="Dashboard", style="Header.TLabel").grid(row=0, column=0, sticky="w")
    for index, title in enumerate(AREAS):
        card = ttk.LabelFrame(frame, text=title, padding=PADDING)
        card.grid(row=1 + index // 2, column=index % 2, padx=6, pady=6, sticky="nsew")
        ttk.Label(card, text="0", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(card, text="Not yet available").pack(anchor="w")
    activity = JobActivity(frame, JobsService(auth))
    activity.grid(row=3, column=0, columnspan=2, padx=6, pady=12, sticky="ew")
    ttk.Button(frame, text="Refresh", command=activity.refresh).grid(
        row=0, column=1, sticky="e", padx=6)
    ttk.Label(frame, text="Operational modules will be added incrementally.", style="Status.TLabel").grid(
        row=4, column=0, columnspan=2, sticky="w", padx=6)
    frame.columnconfigure((0, 1), weight=1)
    return frame
