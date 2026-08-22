"""Small, testable helpers shared by authentication dialogs."""

import tkinter as tk

from app.ui.window_utils import bind_maximize_shortcut, maximize_window, should_start_maximized


def center_window(window: tk.Toplevel) -> None:
    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"+{x}+{y}")


def prepare_modal_dialog(
    dialog: tk.Toplevel,
    parent: tk.Misc | None = None,
) -> None:
    """Make *dialog* visible and modal, maximizing large working forms.

    Windows can hide a toplevel that is transient to a withdrawn root.  Only
    establish that relationship when the parent is actually viewable, and do
    not acquire the grab until the window manager has displayed the dialog.
    Large data-entry dialogs are maximized after visibility so macOS and
    Windows both retain normal desktop chrome rather than entering fullscreen.
    """
    dialog.update_idletasks()
    maximize_on_open = should_start_maximized(dialog)
    center_window(dialog)
    if parent is not None and parent.winfo_viewable():
        dialog.transient(parent)

    dialog.deiconify()
    dialog.lift()

    def clear_topmost() -> None:
        try:
            dialog.attributes("-topmost", False)
        except tk.TclError:
            pass

    try:
        dialog.attributes("-topmost", True)
        dialog.after_idle(clear_topmost)
    except tk.TclError:
        pass

    dialog.wait_visibility()
    if maximize_on_open:
        maximize_window(dialog)
        bind_maximize_shortcut(dialog)
    dialog.focus_force()
    dialog.grab_set()


def close_modal(dialog: tk.Toplevel) -> None:
    """Release a modal grab and destroy a dialog, tolerating an early close."""
    try:
        dialog.grab_release()
    except tk.TclError:
        pass
    try:
        dialog.destroy()
    except tk.TclError:
        pass


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
