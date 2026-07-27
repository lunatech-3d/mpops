"""Password reset value-entry dialog."""
import tkinter as tk
from tkinter import messagebox, ttk
from app.ui.dialog_utils import validate_confirmation
from app.ui.styles import PADDING


def show_password_reset(parent) -> str | None:
    result = None
    dialog = tk.Toplevel(parent); dialog.title("Reset Password"); dialog.resizable(False, False); dialog.transient(parent); dialog.grab_set()
    body = ttk.Frame(dialog, padding=PADDING); body.pack(); first, second = tk.StringVar(), tk.StringVar()
    for row, (label, value) in enumerate((("New Password", first), ("Confirm Password", second))):
        ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(body, textvariable=value, show="•", width=30).grid(row=row, column=1, pady=4)
    def save():
        nonlocal result
        try: result = validate_confirmation(first.get(), second.get())
        except ValueError as exc: messagebox.showerror("Invalid password", str(exc), parent=dialog); return
        dialog.destroy()
    ttk.Button(body, text="Reset", command=save).grid(row=2, column=1, sticky="e", pady=8)
    dialog.bind("<Escape>", lambda _e: dialog.destroy()); parent.wait_window(dialog)
    return result
