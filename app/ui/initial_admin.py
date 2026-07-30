"""First-run initial-administrator dialog."""
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from app.security.user_manager import AuthorizationError, UserManager
from app.ui.dialog_utils import close_modal, prepare_modal_dialog, validate_confirmation, validate_identity
from app.ui.styles import PADDING


def show_initial_admin_dialog(root: tk.Tk, manager: UserManager) -> bool:
    created = False
    dialog = tk.Toplevel(root)
    dialog.title("LunaTech 3D Ops — Create Initial Administrator")
    dialog.resizable(False, False)
    body = ttk.Frame(dialog, padding=PADDING * 2); body.pack()
    ttk.Label(body, text="Create Initial Administrator", style="Header.TLabel").grid(row=0, columnspan=2, sticky="w", pady=(0, 12))
    values = [tk.StringVar() for _ in range(4)]
    labels = ("Username", "Display Name", "Password", "Confirm Password")
    entries = []
    for row, (label, value) in enumerate(zip(labels, values), 1):
        ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        entry = ttk.Entry(body, textvariable=value, width=34, show="•" if "Password" in label else "")
        entry.grid(row=row, column=1, pady=4); entries.append(entry)
    def save():
        nonlocal created
        try:
            username, display = validate_identity(values[0].get(), values[1].get())
            password = validate_confirmation(values[2].get(), values[3].get())
            manager.create_user(username, password, "admin", None, display)
        except (ValueError, AuthorizationError, sqlite3.Error) as exc:
            messagebox.showerror("Unable to create administrator", str(exc), parent=dialog); return
        created = True
        messagebox.showinfo("LunaTech 3D Ops", "The initial administrator was created successfully.", parent=dialog)
        close_modal(dialog)
    buttons = ttk.Frame(body); buttons.grid(row=5, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(buttons, text="Create", command=save).pack(side="left", padx=3)
    ttk.Button(buttons, text="Cancel", command=lambda: close_modal(dialog)).pack(side="left", padx=3)
    dialog.bind("<Return>", lambda _e: save()); dialog.bind("<Escape>", lambda _e: close_modal(dialog))
    dialog.protocol("WM_DELETE_WINDOW", lambda: close_modal(dialog))
    prepare_modal_dialog(dialog, root)
    entries[0].focus_set()
    try:
        root.wait_window(dialog)
    finally:
        if dialog.winfo_exists():
            close_modal(dialog)
    return created
