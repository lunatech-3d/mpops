"""Reusable vertically scrollable content container for Tkinter forms."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk


def mousewheel_units(
    delta: int | float,
    button: int | None = None,
    platform: str | None = None,
) -> float:
    """Translate a native wheel event into signed Tk scroll units.

    Windows wheel deltas conventionally use 120 per notch.  macOS deltas are
    already useful scroll-unit values and can be fractional with a
    high-resolution trackpad, so they must not be divided by 120.
    """
    if button == 4:
        return -1
    if button == 5:
        return 1
    if not delta:
        return 0

    platform = platform or sys.platform
    if platform == "darwin":
        return -float(delta)
    return -float(delta) / 120


def should_scroll_outer(widget: tk.Misc | None, own_canvas: tk.Canvas) -> bool:
    """Return whether a wheel event over *widget* belongs to the outer form."""
    if widget is own_canvas:
        return True
    if isinstance(widget, (tk.Text, ttk.Treeview, tk.Listbox)):
        return False
    if isinstance(widget, tk.Canvas):
        # A canvas is only considered independently scrollable when it has an
        # explicit scroll region.  Decorative/blank canvases need not swallow
        # the containing form's wheel events.
        return not bool(widget.cget("scrollregion"))
    return True


class ScrollableFrame(ttk.Frame):
    """A frame whose ``content`` scrolls vertically within the available area.

    Wheel bindings live on this frame's toplevel bind tag.  That tag is already
    present in the default bind tags of every widget in the window, including
    descendants created later.  The handler filters events back to this frame,
    keeping it scoped when multiple forms or windows are open.
    """

    _WHEEL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")

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
        self._wheel_remainder = 0.0
        self._wheel_bindings: list[tuple[str, str]] = []
        self._wheel_toplevel = self.winfo_toplevel()
        for sequence in self._WHEEL_EVENTS:
            binding_id = self._wheel_toplevel.bind(
                sequence, self._on_mousewheel, add="+"
            )
            if binding_id:
                self._wheel_bindings.append((sequence, binding_id))
        self.bind("<Destroy>", self._cleanup_mousewheel, add="+")

    def _update_scrollregion(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event) -> None:
        self.canvas.itemconfigure(self._content_window, width=event.width)

    def _contains_widget(self, widget: tk.Misc | None) -> bool:
        """Return whether *widget* is this frame or one of its descendants."""
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _cleanup_mousewheel(self, event) -> None:
        """Remove only this instance's bindings when the frame is destroyed."""
        if event.widget is not self:
            return
        for sequence, binding_id in self._wheel_bindings:
            self._wheel_toplevel.unbind(sequence, binding_id)
        self._wheel_bindings.clear()

    def _on_mousewheel(self, event) -> str | None:
        widget = event.widget
        if not self._contains_widget(widget):
            return None
        if not should_scroll_outer(widget, self.canvas):
            return None

        units = mousewheel_units(
            getattr(event, "delta", 0), getattr(event, "num", None)
        )
        self._wheel_remainder += units
        whole_units = int(self._wheel_remainder)
        if whole_units:
            self._wheel_remainder -= whole_units
            self.canvas.yview_scroll(whole_units, "units")
            return "break"
        # Consume a fractional macOS event even when it has not accumulated to
        # a full Tk unit yet, preventing an ancestor from also handling it.
        return "break" if units else None
