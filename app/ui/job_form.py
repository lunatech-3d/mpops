"""Basic Job editor for the Matterport Ops walking skeleton."""

import tkinter as tk
from tkinter import messagebox, ttk

from app.ui.dialog_utils import close_modal, prepare_modal_dialog
from app.ui.styles import PADDING


JOB_FORM_FIELDS = (
    "external_job_id", "market_id", "client_name_source", "project_name_source", "job_status",
    "scheduled_start_at", "capture_address_raw", "city", "state", "postal_code",
    "requested_capture_size", "onsite_contact_name", "onsite_contact_email",
    "onsite_contact_phone", "internal_notes",
)
PRIMARY_TECHNICIAN_FIELD = "primary_technician_id"

STATUS_VALUES = (
    "Requested", "Scheduling", "Scheduled", "Assigned", "In Progress",
    "Completed", "Cancelled", "On Hold",
)


def job_form_data(values: dict) -> dict:
    """Normalize the basic job form into a JobsService payload."""
    result = {
        name: str(values.get(name, "")).strip()
        for name in JOB_FORM_FIELDS
        if name != "market_id"
    }
    result["market_id"] = values.get("market_id") or None
    # The assignment is stored in JobAssignments rather than Jobs, but it must
    # remain in the form payload so the controller can save it separately.
    result[PRIMARY_TECHNICIAN_FIELD] = values.get(PRIMARY_TECHNICIAN_FIELD) or None
    if not result["external_job_id"]:
        raise ValueError("External Job ID is required.")
    for name in JOB_FORM_FIELDS:
        if name == "requested_capture_size":
            result[name] = result[name] or None
        elif name != "external_job_id":
            result[name] = result[name] or None
    result["job_status"] = result["job_status"] or "Requested"
    return result


def changed_fields(original: dict, submitted: dict) -> dict:
    """Return only editable values that differ from the loaded Job."""
    changes = {}
    for name in JOB_FORM_FIELDS:
        old = original.get(name)
        if old in (None, ""):
            old = None
        new = submitted.get(name)
        if name == "requested_capture_size" and new is not None:
            try:
                new = float(new)
            except (TypeError, ValueError):
                pass
        if new != old:
            changes[name] = submitted.get(name)
    return changes


def show_job_form(parent, job: dict | None = None, markets=(), technicians=()) -> dict | None:
    """Show a compact modal Job editor and return submitted values."""
    result = None
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title("Edit Job" if job else "Add Job")
    dialog.geometry("650x650")
    dialog.minsize(600, 580)

    outer = ttk.Frame(dialog, padding=PADDING)
    outer.pack(fill="both", expand=True)
    outer.columnconfigure(1, weight=1)

    labels = (
        ("external_job_id", "External Job ID *"),
        ("market_id", "Market"),
        (PRIMARY_TECHNICIAN_FIELD, "Technician"),
        ("client_name_source", "Client"),
        ("project_name_source", "Project"),
        ("scheduled_start_at", "Scheduled Start"),
        ("capture_address_raw", "Capture Address"),
        ("city", "City"),
        ("state", "State"),
        ("postal_code", "ZIP / Postal Code"),
        ("requested_capture_size", "Requested Capture Size"),
        ("onsite_contact_name", "On-site Contact"),
        ("onsite_contact_email", "Contact Email"),
        ("onsite_contact_phone", "Contact Phone"),
    )

    variables = {
        name: tk.StringVar(value="" if (job or {}).get(name) is None else str((job or {}).get(name)))
        for name in JOB_FORM_FIELDS if name not in {"internal_notes", "market_id"}
    }
    market_id_to_display = {
        market["market_id"]: f"{market['state']} - {market['market_name']}"
        for market in markets
    }
    market_display_to_id = {
        display: market_id for market_id, display in market_id_to_display.items()
    }
    market_var = tk.StringVar(
        value=market_id_to_display.get((job or {}).get("market_id"), "")
    )
    technician_id_to_display = {
        technician["tech_id"]: f"{technician['first_name']} {technician['last_name']}"
        for technician in technicians
    }
    technician_display_to_id = {
        display: tech_id for tech_id, display in technician_id_to_display.items()
    }
    technician_var = tk.StringVar(
        value=technician_id_to_display.get((job or {}).get(PRIMARY_TECHNICIAN_FIELD), "")
    )

    first = None
    row = 0
    for name, label in labels:
        ttk.Label(outer, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        if name == "market_id":
            entry = ttk.Combobox(
                outer,
                textvariable=market_var,
                values=tuple(market_display_to_id),
                state="readonly",
            )
        elif name == PRIMARY_TECHNICIAN_FIELD:
            entry = ttk.Combobox(
                outer,
                textvariable=technician_var,
                values=("", *technician_display_to_id),
                state="readonly",
            )
        else:
            entry = ttk.Entry(outer, textvariable=variables[name])
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        first = first or entry
        row += 1

    ttk.Label(outer, text="Status").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
    status = ttk.Combobox(
        outer, textvariable=variables["job_status"], values=STATUS_VALUES, state="readonly"
    )
    status.grid(row=row, column=1, sticky="ew", pady=4)
    if not variables["job_status"].get():
        variables["job_status"].set("Requested")
    row += 1

    ttk.Label(outer, text="Internal Notes").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=4)
    notes = tk.Text(outer, height=8, wrap="word")
    notes.grid(row=row, column=1, sticky="nsew", pady=4)
    notes.insert("1.0", (job or {}).get("internal_notes") or "")
    outer.rowconfigure(row, weight=1)
    row += 1

    def cancel(_event=None):
        close_modal(dialog)

    def save(_event=None):
        nonlocal result
        values = {name: variable.get() for name, variable in variables.items()}
        values["market_id"] = market_display_to_id.get(
            market_var.get(), (job or {}).get("market_id")
        )
        values[PRIMARY_TECHNICIAN_FIELD] = technician_display_to_id.get(
            technician_var.get()
        )
        values["internal_notes"] = notes.get("1.0", "end-1c")
        try:
            result = job_form_data(values)
        except ValueError as exc:
            messagebox.showerror("Invalid Job", str(exc), parent=dialog)
            return
        close_modal(dialog)

    buttons = ttk.Frame(outer)
    buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(buttons, text="Save", command=save).pack(side="left", padx=3)
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="left", padx=3)

    dialog.bind("<Escape>", cancel)
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    prepare_modal_dialog(dialog, parent)
    first.focus_set()
    parent.wait_window(dialog)
    return result
