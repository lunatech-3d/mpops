"""Shared native-style editing context menu for Tk text-entry widgets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class TextContextMenu:
    """Provide one reusable editing and copying menu for text widgets."""

    def __init__(self, owner: tk.Misc):
        self.owner = owner
        self.widget: tk.Misc | None = None
        self.menu = tk.Menu(owner, tearoff=False)
        self.menu.add_command(label="Cut", command=lambda: self._event("<<Cut>>"))
        self.menu.add_command(label="Copy", command=lambda: self._event("<<Copy>>"))
        self.menu.add_command(label="Copy Field Value", command=self._copy_field_value)
        self.menu.add_command(label="Paste", command=lambda: self._event("<<Paste>>"))
        self.menu.add_separator()
        self.menu.add_command(label="Select All", command=self._select_all)

    def bind(self, *widgets: tk.Misc) -> None:
        """Attach the shared popup without replacing a widget's other bindings."""
        for widget in widgets:
            widget.bind("<Button-3>", self._show, add="+")
            if widget.tk.call("tk", "windowingsystem") == "aqua":
                widget.bind("<Button-2>", self._show, add="+")
                widget.bind("<Control-Button-1>", self._show, add="+")

    @staticmethod
    def _editable(widget: tk.Misc) -> bool:
        state = str(widget.cget("state"))
        if isinstance(widget, ttk.Widget):
            return state not in {"disabled", "readonly"} and not widget.instate(
                ("disabled", "readonly")
            )
        return state == "normal"

    @staticmethod
    def _has_selection(widget: tk.Misc) -> bool:
        if isinstance(widget, tk.Text):
            return bool(widget.tag_ranges("sel"))
        try:
            return bool(widget.selection_present())
        except (AttributeError, tk.TclError):
            return False

    def _clipboard_has_text(self, widget: tk.Misc) -> bool:
        try:
            return bool(widget.clipboard_get())
        except tk.TclError:
            return False

    def _show(self, event: tk.Event) -> str:
        widget = event.widget
        self.widget = widget
        try:
            editable = self._editable(widget)
        except tk.TclError:
            editable = False
        try:
            selected = self._has_selection(widget)
        except tk.TclError:
            selected = False
        self.menu.entryconfigure("Cut", state="normal" if editable and selected else "disabled")
        self.menu.entryconfigure("Copy", state="normal" if selected else "disabled")
        self.menu.entryconfigure("Copy Field Value", state="normal")
        self.menu.entryconfigure(
            "Paste", state="normal" if editable and self._clipboard_has_text(widget) else "disabled"
        )
        self.menu.entryconfigure("Select All", state="normal")
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        return "break"

    def _event(self, event_name: str) -> None:
        if self.widget is not None and self.widget.winfo_exists():
            self.widget.event_generate(event_name)

    def _copy_field_value(self) -> None:
        """Copy all displayed text, including from disabled or read-only fields."""
        widget = self.widget
        if widget is None or not widget.winfo_exists():
            return
        try:
            value = (widget.get("1.0", "end-1c")
                     if isinstance(widget, tk.Text) else widget.get())
            self.owner.clipboard_clear()
            self.owner.clipboard_append(value)
            self.owner.update_idletasks()
        except (AttributeError, tk.TclError):
            return

    def _select_all(self) -> None:
        widget = self.widget
        if widget is None or not widget.winfo_exists():
            return
        try:
            widget.focus_set()
            if isinstance(widget, tk.Text):
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "1.0")
            else:
                widget.selection_range(0, "end")
                widget.icursor("end")
        except tk.TclError:
            # Some Tk builds disallow selection changes while a field is disabled.
            # Copy Field Value remains available without changing that field's state.
            return
