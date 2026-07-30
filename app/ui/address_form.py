"""Reusable technician-address editor and form-data helpers."""
import tkinter as tk
from tkinter import messagebox, ttk

from app.date_utils import display_date_to_iso, format_display_date
from app.ui.dialog_utils import close_modal, prepare_modal_dialog
from app.ui.styles import PADDING

ADDRESS_FIELDS = ("address_1", "address_2", "city", "state", "zip_code", "is_primary",
                  "effective_date", "end_date")


def address_form_data(values: dict) -> dict:
    """Build the exact address service payload, including a real Boolean."""
    result = {name: str(values.get(name, "")).strip()
              for name in ADDRESS_FIELDS if name != "is_primary"}
    for name in ("address_1", "city", "state", "zip_code"):
        if not result[name]:
            raise ValueError(f"{name.replace('_', ' ').title()} is required.")
    for name in ("address_2", "effective_date", "end_date"):
        result[name] = result[name] or None
    result["is_primary"] = bool(values.get("is_primary", False))
    return result


def show_address_form(parent, address: dict | None = None) -> dict | None:
    result = None
    dialog = tk.Toplevel(parent); dialog.withdraw()
    dialog.title("Edit Address" if address else "Add Address"); dialog.resizable(False, False)
    body = ttk.Frame(dialog, padding=PADDING); body.pack()
    labels = (("address_1", "Address 1 *"), ("address_2", "Address 2"),
              ("city", "City *"), ("state", "State *"), ("zip_code", "ZIP Code *"),
              ("effective_date", "Effective Date (MM/DD/YYYY)"),
              ("end_date", "End Date (MM/DD/YYYY)"))
    variables = {name: tk.StringVar(value=(format_display_date((address or {}).get(name))
                                            if name in {"effective_date", "end_date"}
                                            else (address or {}).get(name) or "")) for name, _ in labels}
    primary = tk.BooleanVar(value=bool((address or {}).get("is_primary", False)))
    first = None
    for row, (name, label) in enumerate(labels):
        ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
        entry = ttk.Entry(body, textvariable=variables[name], width=40)
        entry.grid(row=row, column=1, pady=3); first = first or entry
    ttk.Checkbutton(body, text="Primary Address", variable=primary).grid(
        row=len(labels), column=1, sticky="w", pady=4)

    def cancel(_event=None): close_modal(dialog)
    def save(_event=None):
        nonlocal result
        try:
            values = {name: value.get() for name, value in variables.items()}
            values["is_primary"] = primary.get()
            for field in ("effective_date", "end_date"):
                values[field] = display_date_to_iso(values[field]) or ""
            result = address_form_data(values)
        except ValueError as exc:
            messagebox.showerror("Invalid address", str(exc), parent=dialog); return
        close_modal(dialog)
    buttons = ttk.Frame(body); buttons.grid(row=len(labels)+1, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Save", command=save).pack(side="left", padx=3)
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="left", padx=3)
    dialog.bind("<Escape>", cancel); dialog.bind("<Return>", save); dialog.protocol("WM_DELETE_WINDOW", cancel)
    prepare_modal_dialog(dialog, parent); first.focus_set(); parent.wait_window(dialog)
    return result
