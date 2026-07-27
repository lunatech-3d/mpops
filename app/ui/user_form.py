"""Reusable modal for collecting user details; persistence stays in the caller."""
import tkinter as tk
from tkinter import messagebox, ttk

from app.security.user_manager import VALID_ROLES
from app.ui.dialog_utils import validate_confirmation, validate_identity
from app.ui.styles import PADDING


def show_user_form(parent, user: dict | None = None) -> dict | None:
    result = None
    dialog = tk.Toplevel(parent); dialog.title("Edit User" if user else "Add User")
    dialog.resizable(False, False); dialog.transient(parent); dialog.grab_set()
    body = ttk.Frame(dialog, padding=PADDING); body.pack()
    username = tk.StringVar(value=user["username"] if user else "")
    display = tk.StringVar(value=(user["display_name"] or "") if user else "")
    role = tk.StringVar(value=user["role"] if user else "operator")
    active = tk.BooleanVar(value=bool(user["is_active"]) if user else True)
    password, confirmation = tk.StringVar(), tk.StringVar()
    rows = [("Username", username, False), ("Display Name", display, False),
            ("Role", role, False), ("Password", password, True),
            ("Confirm Password", confirmation, True)]
    for row, (label, value, secret) in enumerate(rows):
        if user and secret: continue
        ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        if label == "Role":
            widget = ttk.Combobox(body, textvariable=value, values=sorted(VALID_ROLES), state="readonly", width=27)
        else:
            widget = ttk.Entry(body, textvariable=value, show="•" if secret else "", width=30)
            if user and label == "Username": widget.configure(state="disabled")
        widget.grid(row=row, column=1, pady=4)
    ttk.Checkbutton(body, text="Active", variable=active).grid(row=5, column=1, sticky="w", pady=4)
    def save():
        nonlocal result
        try:
            clean_username, clean_display = validate_identity(username.get(), display.get())
            if role.get() not in VALID_ROLES: raise ValueError("Select a valid role.")
            result = {"username": clean_username, "display_name": clean_display,
                      "role": role.get(), "is_active": bool(active.get())}
            if not user: result["password"] = validate_confirmation(password.get(), confirmation.get())
        except ValueError as exc:
            messagebox.showerror("Invalid user", str(exc), parent=dialog); return
        dialog.destroy()
    buttons = ttk.Frame(body); buttons.grid(row=6, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Save", command=save).pack(side="left", padx=3)
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=3)
    dialog.bind("<Escape>", lambda _e: dialog.destroy()); parent.wait_window(dialog)
    return result
