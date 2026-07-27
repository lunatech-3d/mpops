"""Shared, intentionally small ttk style palette."""
from tkinter import ttk

PADDING = 12
BACKGROUND = "#f4f6f8"
NAV_BACKGROUND = "#243447"


def configure_styles(root) -> None:
    root.configure(background=BACKGROUND)
    style = ttk.Style(root)
    style.configure("App.TFrame", background=BACKGROUND)
    style.configure("Header.TLabel", font=("Segoe UI", 20, "bold"), background=BACKGROUND)
    style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"), background=BACKGROUND)
    style.configure("Status.TLabel", foreground="#52606d", background=BACKGROUND)
    style.configure("Nav.TButton", font=("Segoe UI", 10), padding=(12, 9))
    style.configure("TButton", padding=(8, 5))
    style.configure("TLabel", font=("Segoe UI", 10))
    style.configure("TEntry", padding=5)
    style.configure("Treeview", rowheight=26, font=("Segoe UI", 9))
