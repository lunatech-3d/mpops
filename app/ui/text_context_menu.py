"""Application-wide context menus for Tk text and displayed-value widgets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


_TEXT_WIDGET_CLASSES = {
    "Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "TCombobox",
}
_LABEL_WIDGET_CLASSES = {"Label", "TLabel"}


def _is_text_widget(widget: tk.Misc) -> bool:
    """Return whether *widget* exposes one of Tk's text-editing interfaces.

    ``winfo_class`` is the most reliable check for themed widgets because ttk
    and application subclasses still report their underlying Tk class.  The
    ``isinstance`` checks make the intent explicit and also support test or
    wrapper widgets which preserve the Python widget type.
    """
    text_types = (tk.Entry, tk.Text, tk.Spinbox, ttk.Entry, ttk.Combobox)
    ttk_spinbox = getattr(ttk, "Spinbox", None)
    if ttk_spinbox is not None:
        text_types += (ttk_spinbox,)
    return isinstance(widget, text_types) or widget.winfo_class() in _TEXT_WIDGET_CLASSES


class TextContextMenu:
    """Own the single clipboard menu used by every window in an application.

    The bindings are installed at ``bind_all`` level, so widgets in dialogs and
    future dynamically-created ``Toplevel`` windows are covered automatically.
    Non-text widgets are ignored, leaving Treeview and other workflow-specific
    popup bindings untouched.
    """

    def __init__(self, owner: tk.Misc):
        self.owner = owner
        self.widget: tk.Misc | None = None
        self._create_menus()

    def _create_menus(self) -> None:
        """Create the Tk menu commands (again, if a form cleared the root)."""
        self.menu = tk.Menu(self.owner, tearoff=False)
        self.menu.add_command(label="Cut", command=lambda: self._event("<<Cut>>"))
        self.menu.add_command(label="Copy", command=self._copy)
        self.menu.add_command(label="Paste", command=lambda: self._event("<<Paste>>"))
        self.menu.add_separator()
        self.menu.add_command(label="Select All", command=self._select_all)
        self.value_menu = tk.Menu(self.owner, tearoff=False)
        self.value_menu.add_command(label="Copy Value", command=self._copy_value)

    def _ensure_menus(self) -> None:
        """Recreate menu widgets whose Tcl commands were destroyed externally."""
        try:
            menus_exist = self.menu.winfo_exists() and self.value_menu.winfo_exists()
        except tk.TclError:
            menus_exist = False
        if not menus_exist:
            self._create_menus()

    def install(self) -> None:
        """Install idempotent application-wide Windows and macOS bindings."""
        if getattr(self.owner, "_mpops_text_context_menu", None) is not None:
            return
        self.owner._mpops_text_context_menu = self  # type: ignore[attr-defined]
        self.owner.bind_all("<Button-3>", self._show, add="+")
        if self.owner.tk.call("tk", "windowingsystem") == "aqua":
            self.owner.bind_all("<Button-2>", self._show, add="+")
            self.owner.bind_all("<Control-Button-1>", self._show, add="+")

    def bind(self, *_widgets: tk.Misc) -> None:
        """Backward-compatible alias; global installation needs no registration."""
        self.install()

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
        return bool(widget.selection_present())

    @staticmethod
    def _value(widget: tk.Misc) -> str:
        if isinstance(widget, tk.Text):
            return widget.get("1.0", "end-1c")
        if widget.winfo_class() in _LABEL_WIDGET_CLASSES:
            variable = str(widget.cget("textvariable"))
            return str(widget.getvar(variable)) if variable else str(widget.cget("text"))
        return str(widget.get())

    def _clipboard_has_text(self, widget: tk.Misc) -> bool:
        try:
            widget.clipboard_get()
            return True
        except tk.TclError:
            return False

    def _show(self, event: tk.Event) -> str | None:
        self._ensure_menus()
        widget = event.widget
        widget_class = widget.winfo_class()
        if widget_class in _LABEL_WIDGET_CLASSES:
            try:
                value = self._value(widget)
            except tk.TclError:
                return None
            if not value.strip():
                return None
            self.widget = widget
            self._popup(self.value_menu, event)
            return "break"
        if not _is_text_widget(widget):
            return None

        self.widget = widget
        try:
            widget.focus_set()
            editable = self._editable(widget)
            selected = self._has_selection(widget)
            has_value = bool(self._value(widget))
        except tk.TclError:
            editable = selected = has_value = False
        self.menu.entryconfigure("Cut", state="normal" if editable and selected else "disabled")
        self.menu.entryconfigure("Copy", state="normal" if selected or has_value else "disabled")
        self.menu.entryconfigure(
            "Paste", state="normal" if editable and self._clipboard_has_text(widget) else "disabled"
        )
        # Disabled Tk fields cannot own a selection; direct Copy still works.
        selectable = str(widget.cget("state")) != "disabled"
        self.menu.entryconfigure("Select All", state="normal" if selectable and has_value else "disabled")
        self._popup(self.menu, event)
        return "break"

    @staticmethod
    def _popup(menu: tk.Menu, event: tk.Event) -> None:
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _event(self, event_name: str) -> None:
        if self.widget is not None and self.widget.winfo_exists():
            # Tk's virtual editing events preserve validation and undo behavior.
            self.widget.event_generate(event_name)

    def _copy(self) -> None:
        widget = self.widget
        if widget is None or not widget.winfo_exists():
            return
        try:
            if self._has_selection(widget):
                widget.event_generate("<<Copy>>")
            else:
                self._copy_value()
        except tk.TclError:
            return

    def _copy_value(self) -> None:
        widget = self.widget
        if widget is None or not widget.winfo_exists():
            return
        try:
            value = self._value(widget)
            widget.clipboard_clear()
            widget.clipboard_append(value)
            widget.update_idletasks()
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
                widget.mark_set("insert", "end-1c")
            else:
                widget.selection_range(0, "end")
                widget.icursor("end")
        except tk.TclError:
            return


def install_text_context_menu(root: tk.Misc) -> TextContextMenu:
    """Install and return MPOPS's application-wide clipboard menu."""
    # A caller may only have a frame or Toplevel.  Store the singleton on the
    # interpreter's root so repeated installation cannot add duplicate
    # ``bind_all`` callbacks.
    owner = root._root()  # type: ignore[attr-defined]
    existing = getattr(owner, "_mpops_text_context_menu", None)
    if existing is not None:
        return existing
    context_menu = TextContextMenu(owner)
    context_menu.install()
    return context_menu
