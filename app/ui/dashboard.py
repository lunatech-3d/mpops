"""Initial operational dashboard with neutral placeholders."""
from tkinter import ttk
from app.ui.styles import PADDING

AREAS = ("Upcoming Jobs", "Jobs Awaiting Assignment", "Technician Payments Due", "Unreconciled Payments")


def build_dashboard(parent, session, database_path):
    frame = ttk.Frame(parent, style="App.TFrame", padding=PADDING)
    ttk.Label(frame, text="Dashboard", style="Header.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
    for index, title in enumerate(AREAS):
        card = ttk.LabelFrame(frame, text=title, padding=PADDING)
        card.grid(row=1 + index // 2, column=index % 2, padx=6, pady=6, sticky="nsew")
        ttk.Label(card, text="0", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(card, text="Not yet available").pack(anchor="w")
    info = ttk.LabelFrame(frame, text="Application", padding=PADDING)
    info.grid(row=3, column=0, columnspan=2, padx=6, pady=12, sticky="ew")
    for text in (f"Current user: {session.username}", f"Role: {session.role.title()}",
                 f"Database: {database_path}", "Status: Ready"):
        ttk.Label(info, text=text).pack(anchor="w")
    ttk.Label(frame, text="Operational modules will be added incrementally.", style="Status.TLabel").grid(
        row=4, column=0, columnspan=2, sticky="w", padx=6)
    frame.columnconfigure((0, 1), weight=1)
    return frame
