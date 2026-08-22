"""Shared window sizing helpers for the MPOPS desktop UI."""

import tkinter as tk


LARGE_WINDOW_MIN_WIDTH = 700
LARGE_WINDOW_MIN_HEIGHT = 520


def _dimension(value) -> int:
    """Return a defensive integer window dimension for real and mocked widgets."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def should_start_maximized(window: tk.Misc) -> bool:
    """Return whether an already-sized working window should open maximized."""
    try:
        window.update_idletasks()
    except tk.TclError:
        return False
    width = max(_dimension(window.winfo_width()), _dimension(window.winfo_reqwidth()))
    height = max(_dimension(window.winfo_height()), _dimension(window.winfo_reqheight()))
    return width >= LARGE_WINDOW_MIN_WIDTH and height >= LARGE_WINDOW_MIN_HEIGHT


def is_maximized(window: tk.Misc) -> bool:
    """Return whether *window* is in the window-manager maximized state."""
    try:
        return str(window.state()) == "zoomed"
    except (tk.TclError, TypeError):
        pass
    try:
        return bool(window.attributes("-zoomed"))
    except (tk.TclError, TypeError):
        return False


def maximize_window(window: tk.Misc) -> bool:
    """Maximize a top-level window without entering borderless fullscreen mode."""
    try:
        window.state("zoomed")
        return True
    except (tk.TclError, TypeError):
        pass
    try:
        window.attributes("-zoomed", True)
        return True
    except (tk.TclError, TypeError):
        return False


def restore_window(window: tk.Misc) -> bool:
    """Restore a maximized top-level window to its normal window-manager size."""
    try:
        window.state("normal")
        return True
    except (tk.TclError, TypeError):
        pass
    try:
        window.attributes("-zoomed", False)
        return True
    except (tk.TclError, TypeError):
        return False


def toggle_maximized(window: tk.Misc) -> bool:
    """Toggle maximized/normal state and return whether the window is maximized."""
    if is_maximized(window):
        restore_window(window)
        return False
    maximize_window(window)
    return True


def bind_maximize_shortcut(window: tk.Misc) -> None:
    """Bind F11 to maximize/restore while preserving any existing binding."""
    def toggle(_event=None):
        toggle_maximized(window)
        return "break"

    window.bind("<F11>", toggle, add="+")
