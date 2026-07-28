"""Reusable manager and edit dialog for simple name/status lookup tables."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any, Callable

from app.security.user_manager import AuthorizationError
from app.ui.dialog_utils import close_modal, prepare_modal_dialog
from app.ui.styles import PADDING

EXPECTED_ERRORS = (ValueError, LookupError, AuthorizationError, sqlite3.Error)


@dataclass(frozen=True)
class LookupManagerConfig:
    """Labels and callbacks required by the reusable lookup-table screen."""

    singular_name: str
    plural_name: str
    id_field: str
    name_field: str
    list_records: Callable[[bool], list[dict[str, Any]]]
    search_records: Callable[[str, bool], list[dict[str, Any]]]
    get_record: Callable[[int], dict[str, Any] | None]
    create_record: Callable[[str], int]
    update_record: Callable[[int, str], Any]
    set_active: Callable[[int, bool], Any]
    can_modify: bool = False


class LookupManager(ttk.Frame):
    """CRUD interface for lookup tables containing an ID, name, and status."""

    COLUMNS = ("name", "status")

    def __init__(self, parent: tk.Misc, config: LookupManagerConfig):
        super().__init__(parent, padding=PADDING, style="App.TFrame")
        self.config = config
        self.rows: dict[str, dict[str, Any]] = {}
        self.sort_column = "name"
        self.sort_descending = False

        ttk.Label(self, text=config.plural_name, style="Header.TLabel").pack(
            anchor="w", pady=(0, 10)
        )

        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=(0, 8))
        self.search_var = tk.StringVar()
        self.inactive_var = tk.BooleanVar(value=False)
        ttk.Label(filters, text="Search:").pack(side="left")
        entry = ttk.Entry(filters, textvariable=self.search_var, width=35)
        entry.pack(side="left", padx=6)
        ttk.Button(filters, text="Search", command=self.refresh).pack(side="left")
        ttk.Checkbutton(
            filters,
            text=f"Include inactive {config.plural_name.lower()}",
            variable=self.inactive_var,
            command=self.refresh,
        ).pack(side="left", padx=12)
        ttk.Button(filters, text="Refresh", command=self.refresh).pack(side="left")
        entry.bind("<Return>", lambda _event: self.refresh())

        table = ttk.Frame(self)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table, columns=self.COLUMNS, show="headings", selectmode="browse"
        )
        headings = (config.singular_name, "Status")
        widths = (360, 110)
        for column, heading, width in zip(self.COLUMNS, headings, widths):
            self.tree.heading(
                column,
                text=heading,
                command=lambda selected=column: self.sort_by(selected),
            )
            self.tree.column(column, width=width, minwidth=80)
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ybar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", self._handle_double_click)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(8, 0))
        self.mutation_buttons = []
        for label, command in (
            (f"Add {config.singular_name}", self.add),
            (f"Edit {config.singular_name}", self.edit),
            ("Activate / Deactivate", self.toggle_active),
        ):
            button = ttk.Button(actions, text=label, command=command)
            button.pack(side="left", padx=(0, 6))
            self.mutation_buttons.append(button)
        ttk.Button(actions, text="Refresh", command=self.refresh).pack(side="left")

        self.status_var = tk.StringVar()
        ttk.Label(
            self, textvariable=self.status_var, style="Status.TLabel"
        ).pack(anchor="w", pady=(7, 0))

        if not config.can_modify:
            for button in self.mutation_buttons:
                button.configure(state="disabled")
        self.refresh()

    def _handle_double_click(self, event: tk.Event) -> None:
        if self.tree.identify_region(event.x, event.y) in ("cell", "tree"):
            self.edit()

    def sort_by(self, column: str) -> None:
        descending = self.sort_column == column and not self.sort_descending
        self.sort_column = column
        self.sort_descending = descending
        self._sort_tree()
        headings = {"name": self.config.singular_name, "status": "Status"}
        for item in self.COLUMNS:
            marker = " ▼" if item == column and descending else " ▲" if item == column else ""
            self.tree.heading(item, text=headings[item] + marker)

    def _sort_tree(self) -> None:
        selected = self.tree.selection()
        items = list(self.tree.get_children())
        items.sort(
            key=lambda iid: str(self.rows[iid].get(self.sort_column) or "").casefold(),
            reverse=self.sort_descending,
        )
        for position, iid in enumerate(items):
            self.tree.move(iid, "", position)
        if selected:
            self.tree.selection_set(selected)
            self.tree.see(selected[0])

    def refresh(self, select_id: int | None = None) -> None:
        try:
            query = self.search_var.get().strip()
            rows = (
                self.config.search_records(query, bool(self.inactive_var.get()))
                if query
                else self.config.list_records(bool(self.inactive_var.get()))
            )
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return

        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for row in rows:
            record_id = int(row[self.config.id_field])
            iid = f"lookup-{record_id}"
            display_row = {
                **row,
                "name": row.get(self.config.name_field) or "",
                "status": row.get("status") or "",
            }
            self.rows[iid] = display_row
            self.tree.insert(
                "", "end", iid=iid, values=(display_row["name"], display_row["status"])
            )
        self._sort_tree()
        count = len(rows)
        self.status_var.set(
            f"{count} {self.config.singular_name.lower()} record(s) found."
            if rows
            else f"No {self.config.plural_name.lower()} found."
        )
        iid = f"lookup-{select_id}" if select_id else None
        if iid and self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def selected(self) -> dict[str, Any] | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning(
                self.config.plural_name,
                f"Select a {self.config.singular_name.lower()} first.",
                parent=self,
            )
            return None
        return self.rows.get(selection[0])

    def _error(self, exc: Exception) -> None:
        messagebox.showerror(self.config.plural_name, str(exc), parent=self)

    def add(self) -> None:
        name = show_lookup_form(self, self.config.singular_name)
        if name is None:
            return
        try:
            record_id = self.config.create_record(name)
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        self.refresh(record_id)
        self.status_var.set(f"{self.config.singular_name} added successfully.")

    def edit(self) -> None:
        row = self.selected()
        if not row:
            return
        record_id = int(row[self.config.id_field])
        try:
            current = self.config.get_record(record_id)
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        if current is None:
            self._error(LookupError(f"{self.config.singular_name} not found"))
            return
        original_name = str(current.get(self.config.name_field) or "")
        name = show_lookup_form(self, self.config.singular_name, original_name)
        if name is None:
            return
        if name.strip() == original_name.strip():
            self.status_var.set("No changes were made.")
            return
        try:
            self.config.update_record(record_id, name)
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        self.refresh(record_id)
        self.status_var.set(f"{self.config.singular_name} updated.")

    def toggle_active(self) -> None:
        row = self.selected()
        if not row:
            return
        record_id = int(row[self.config.id_field])
        activate = row.get("status") != "Active"
        action = "activate" if activate else "deactivate"
        if not messagebox.askyesno(
            "Confirm status change",
            f"{action.title()} {row['name']}?",
            parent=self,
        ):
            return
        try:
            self.config.set_active(record_id, activate)
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        self.refresh(record_id)
        self.status_var.set(
            f"{self.config.singular_name} {'activated' if activate else 'deactivated'}."
        )


def show_lookup_form(
    parent: tk.Misc, singular_name: str, current_name: str = ""
) -> str | None:
    """Collect and validate the display name for a lookup-table record."""

    result: str | None = None
    window = tk.Toplevel(parent)
    window.withdraw()
    window.title(f"{'Edit' if current_name else 'Add'} {singular_name}")
    body = ttk.Frame(window, padding=PADDING)
    body.pack(fill="both", expand=True)
    name_var = tk.StringVar(value=current_name)
    ttk.Label(body, text=f"{singular_name} Name").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=5
    )
    entry = ttk.Entry(body, textvariable=name_var, width=42)
    entry.grid(row=0, column=1, pady=5)

    def cancel(_event=None) -> None:
        close_modal(window)

    def submit(_event=None) -> None:
        nonlocal result
        value = name_var.get().strip()
        if not value:
            messagebox.showerror(
                singular_name, f"{singular_name} name is required.", parent=window
            )
            entry.focus_set()
            return
        result = value
        close_modal(window)

    buttons = ttk.Frame(body)
    buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Save", command=submit).pack(side="left", padx=3)
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="left", padx=3)
    window.bind("<Return>", submit)
    window.bind("<Escape>", cancel)
    window.protocol("WM_DELETE_WINDOW", cancel)
    prepare_modal_dialog(window, parent)
    entry.focus_set()
    entry.selection_range(0, "end")
    window.wait_window()
    return result
