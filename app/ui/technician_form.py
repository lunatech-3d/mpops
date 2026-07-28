"""Reusable, role-aware technician editor and testable form-data helpers."""
import tkinter as tk
from tkinter import messagebox, ttk

from app.ui.dialog_utils import close_modal, prepare_modal_dialog
from app.ui.styles import PADDING

TECHNICIAN_FIELDS = (
    "tech_code", "first_name", "middle_name", "last_name", "suffix", "preferred_name",
    "company_name", "contractor_type", "inactive_reason", "date_of_birth", "ssn_last4",
    "drivers_license_number", "drivers_license_state", "email", "alternate_email",
    "mobile_phone", "home_phone", "work_phone", "emergency_contact_name",
    "emergency_contact_relationship", "emergency_contact_phone", "hire_date",
    "termination_date", "notes", "notes_private",
)
OPTIONAL_TECHNICIAN_FIELDS = frozenset(TECHNICIAN_FIELDS) - {"tech_code", "first_name", "last_name"}
RESTRICTED_FIELDS = frozenset({
    "date_of_birth", "ssn_last4", "drivers_license_number", "drivers_license_state",
    "emergency_contact_name", "emergency_contact_relationship", "emergency_contact_phone",
    "notes_private",
})


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


def show_technician_form(parent, technician: dict | None = None, *, is_admin=True) -> dict | None:
    """Show the compact tabbed editor; restricted widgets are never built for non-admins."""
    result = None
    dialog = tk.Toplevel(parent); dialog.withdraw()
    dialog.title("Edit Technician" if technician else "Add Technician")
    dialog.geometry("720x590"); dialog.minsize(640, 520)
    body = ttk.Frame(dialog, padding=PADDING); body.pack(fill="both", expand=True)
    notebook = ttk.Notebook(body); notebook.pack(fill="both", expand=True)
    variables = {}
    widgets = {}

    def tab(title):
        frame = ttk.Frame(notebook, padding=PADDING); notebook.add(frame, text=title)
        frame.columnconfigure(1, weight=1); return frame

    def fields(frame, specs):
        for row, (name, label, kind) in enumerate(specs):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            variables[name] = tk.StringVar(value=(technician or {}).get(name) or "")
            if kind == "suffix":
                widget = ttk.Combobox(frame, textvariable=variables[name],
                                      values=("", "Jr.", "Sr.", "II", "III", "IV", "V"))
            else:
                widget = ttk.Entry(frame, textvariable=variables[name])
            widget.grid(row=row, column=1, sticky="ew", pady=6); widgets[name] = widget

    identity = tab("Identity")
    fields(identity, (("tech_code", "Technician Code *", "entry"),
                      ("first_name", "First Name *", "entry"),
                      ("middle_name", "Middle Name", "entry"),
                      ("last_name", "Last Name *", "entry"), ("suffix", "Suffix", "suffix"),
                      ("preferred_name", "Preferred Name", "entry")))
    engagement = tab("Engagement")
    fields(engagement, (("company_name", "Company Name", "entry"),
                        ("contractor_type", "Contractor Type", "entry"),
                        ("hire_date", "Hire Date (YYYY-MM-DD)", "entry"),
                        ("termination_date", "Termination Date (YYYY-MM-DD)", "entry"),
                        ("inactive_reason", "Inactive Reason", "entry")))
    ttk.Label(engagement, text="Status").grid(row=5, column=0, sticky="w", pady=6)
    status = tk.StringVar(value=(technician or {}).get("status") or "Active")
    ttk.Entry(engagement, textvariable=status, state="readonly").grid(row=5, column=1, sticky="ew")
    contact = tab("Contact")
    fields(contact, (("email", "Primary Email", "entry"),
                     ("alternate_email", "Alternate Email", "entry"),
                     ("mobile_phone", "Mobile Phone", "entry"),
                     ("home_phone", "Home Phone", "entry"), ("work_phone", "Work Phone", "entry")))
    if is_admin:
        emergency = tab("Emergency Contact")
        fields(emergency, (("emergency_contact_name", "Contact Name", "entry"),
                           ("emergency_contact_relationship", "Relationship", "entry"),
                           ("emergency_contact_phone", "Phone", "entry")))
        restricted = tab("Restricted")
        fields(restricted, (("date_of_birth", "Date of Birth (YYYY-MM-DD)", "entry"),
                            ("ssn_last4", "SSN — Last 4 Digits", "entry"),
                            ("drivers_license_number", "Driver’s License Number", "entry"),
                            ("drivers_license_state", "Driver’s License State", "entry")))
    notes_tab = tab("Notes")
    texts = {}
    note_specs = [("notes", "General Notes")]
    if is_admin: note_specs.append(("notes_private", "Private Administrative Notes — Restricted"))
    for row, (name, label) in enumerate(note_specs):
        ttk.Label(notes_tab, text=label).grid(row=row * 2, column=0, sticky="w", pady=(4, 2))
        text = tk.Text(notes_tab, height=7, wrap="word"); text.grid(row=row * 2 + 1, column=0, sticky="nsew")
        text.insert("1.0", (technician or {}).get(name) or ""); texts[name] = text
        notes_tab.rowconfigure(row * 2 + 1, weight=1)
    notes_tab.columnconfigure(0, weight=1)

    def cancel(_event=None): close_modal(dialog)
    def save(_event=None):
        nonlocal result
        values = {name: value.get() for name, value in variables.items()}
        values.update({name: text.get("1.0", "end-1c") for name, text in texts.items()})
        # A non-admin form never receives sensitive values and therefore cannot submit them.
        if not is_admin:
            values.update({name: (technician or {}).get(name) or "" for name in RESTRICTED_FIELDS})
        try: result = technician_form_data(values)
        except ValueError as exc:
            messagebox.showerror("Invalid technician", str(exc), parent=dialog); return
        close_modal(dialog)

    buttons = ttk.Frame(body); buttons.pack(fill="x", pady=(10, 0))
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="right", padx=3)
    ttk.Button(buttons, text="Save", command=save).pack(side="right", padx=3)
    dialog.bind("<Escape>", cancel)
    dialog.bind("<Return>", lambda event: None if isinstance(event.widget, tk.Text) else save())
    dialog.protocol("WM_DELETE_WINDOW", cancel); prepare_modal_dialog(dialog, parent)
    widgets["tech_code" if not technician else "first_name"].focus_set()
    parent.wait_window(dialog)
    return result
