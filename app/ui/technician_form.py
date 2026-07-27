"""Reusable technician editor and testable form-data helpers."""
import tkinter as tk
from tkinter import messagebox, ttk

from app.ui.dialog_utils import close_modal, prepare_modal_dialog
from app.ui.styles import PADDING

TECHNICIAN_FIELDS = ("tech_code", "first_name", "last_name", "preferred_name", "email",
                     "mobile_phone", "home_phone", "hire_date", "termination_date", "notes")
OPTIONAL_TECHNICIAN_FIELDS = frozenset(TECHNICIAN_FIELDS) - {"tech_code", "first_name", "last_name"}


def technician_form_data(values: dict) -> dict:
    """Map form values to the service allowlist, normalizing blank optionals."""
    result = {name: str(values.get(name, "")).strip() for name in TECHNICIAN_FIELDS}
    for name in ("tech_code", "first_name", "last_name"):
        if not result[name]:
            raise ValueError(f"{name.replace('_', ' ').title()} is required.")
    for name in OPTIONAL_TECHNICIAN_FIELDS:
        result[name] = result[name] or None
    return result


def changed_fields(original: dict, submitted: dict, fields=TECHNICIAN_FIELDS) -> dict:
    """Return only editable values that differ from a loaded service record."""
    return {name: submitted[name] for name in fields
            if submitted.get(name) != (None if original.get(name) in (None, "") else original.get(name))}


def show_technician_form(parent, technician: dict | None = None) -> dict | None:
    result = None
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title("Edit Technician" if technician else "Add Technician")
    dialog.resizable(False, False)
    body = ttk.Frame(dialog, padding=PADDING)
    body.pack(fill="both", expand=True)
    labels = (("tech_code", "Technician Code *"), ("first_name", "First Name *"),
              ("last_name", "Last Name *"), ("preferred_name", "Preferred Name"),
              ("email", "Email"), ("mobile_phone", "Mobile Phone"),
              ("home_phone", "Home Phone"), ("hire_date", "Hire Date (YYYY-MM-DD)"),
              ("termination_date", "Termination Date (YYYY-MM-DD)"))
    variables = {name: tk.StringVar(value=(technician or {}).get(name) or "")
                 for name in TECHNICIAN_FIELDS if name != "notes"}
    first = None
    for row, (name, label) in enumerate(labels):
        ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
        entry = ttk.Entry(body, textvariable=variables[name], width=42)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        first = first or entry
    ttk.Label(body, text="Notes").grid(row=len(labels), column=0, sticky="nw", pady=3)
    notes = tk.Text(body, width=42, height=6, wrap="word")
    notes.grid(row=len(labels), column=1, pady=3)
    notes.insert("1.0", (technician or {}).get("notes") or "")

    def cancel(_event=None):
        close_modal(dialog)

    def save(_event=None):
        nonlocal result
        try:
            values = {name: variable.get() for name, variable in variables.items()}
            values["notes"] = notes.get("1.0", "end-1c")
            result = technician_form_data(values)
        except ValueError as exc:
            messagebox.showerror("Invalid technician", str(exc), parent=dialog)
            return
        close_modal(dialog)

    buttons = ttk.Frame(body)
    buttons.grid(row=len(labels) + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Save", command=save).pack(side="left", padx=3)
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="left", padx=3)
    dialog.bind("<Escape>", cancel)
    dialog.bind("<Return>", lambda event: None if event.widget is notes else save())
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    prepare_modal_dialog(dialog, parent)
    first.focus_set()
    parent.wait_window(dialog)
    return result
