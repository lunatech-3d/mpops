"""Small, testable helpers shared by authentication dialogs."""

import tkinter as tk


def center_window(window: tk.Toplevel) -> None:
    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"+{x}+{y}")


def validate_identity(username: str, display_name: str) -> tuple[str, str]:
    username, display_name = username.strip(), display_name.strip()
    if not username:
        raise ValueError("Username is required.")
    if not display_name:
        raise ValueError("Display name is required.")
    return username, display_name


def validate_confirmation(password: str, confirmation: str) -> str:
    if password != confirmation:
        raise ValueError("Passwords do not match.")
    return password
