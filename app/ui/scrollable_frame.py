"""Reusable vertically scrollable content container for Tkinter forms."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def mousewheel_units(delta: int, button: int | None = None) -> int:
    """Translate Windows/macOS and X11 wheel events into Tk scroll units."""
    if button == 4:
        return -1
    if button == 5:
        return 1
    if not delta:
        return 0
    # Windows reports multiples of 120.  Some macOS Tk builds report small
    # integer deltas, so retain at least one unit in either direction.
    return -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)


class ScrollableFrame(ttk.Frame):
    """A frame whose ``content`` scrolls vertically within the available area.

    Wheel bindings are installed on this container's descendants and leave
    nested scrolling controls (for example a Treeview or Text) in charge.
    """

    def __init__(self, parent: tk.Misc, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                takefocus=False)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical",
                                       command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas)
        self._content_window = self.canvas.create_window(
            (0, 0), window=self.content, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.content.bind("<Configure>", self._update_scrollregion)
        self.canvas.bind("<Configure>", self._resize_content)
        # Form children are normally added immediately after construction.
        # Waiting until idle lets us bind the completed widget hierarchy without
        # using process-wide bind_all handlers that could affect other windows.
        self.after_idle(self._bind_mousewheel)

    def _update_scrollregion(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event) -> None:
        self.canvas.itemconfigure(self._content_window, width=event.width)

    def _bind_mousewheel(self, _event=None) -> None:
        pending = [self.canvas, self.content]
        while pending:
            widget = pending.pop()
            widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
            widget.bind("<Button-4>", self._on_mousewheel, add="+")
            widget.bind("<Button-5>", self._on_mousewheel, add="+")
            pending.extend(widget.winfo_children())

    def _on_mousewheel(self, event) -> str | None:
        # Nested controls have their own scrolling behavior; do not move both
        # the control and the containing form for the same wheel event.
        widget = self.winfo_containing(event.x_root, event.y_root)
        if isinstance(widget, (tk.Text, ttk.Treeview, tk.Listbox, tk.Canvas)):
            return None
        units = mousewheel_units(getattr(event, "delta", 0),
                                 getattr(event, "num", None))
        if units:
            self.canvas.yview_scroll(units, "units")
            return "break"
        return None
