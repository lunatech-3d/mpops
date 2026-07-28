"""Markets manager with editable state and status fields."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from app.security.user_manager import AuthorizationError
from app.services.market_service import MarketService
from app.ui.dialog_utils import close_modal, prepare_modal_dialog
from app.ui.styles import PADDING

EXPECTED_ERRORS = (ValueError, LookupError, AuthorizationError, sqlite3.Error)

US_STATE_CODES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
)


class MarketManager(ttk.Frame):
    """CRUD interface for operational Markets."""

    COLUMNS = ("market_name", "state", "status")

    def __init__(self, parent: tk.Misc, auth, session):
        super().__init__(parent, padding=PADDING, style="App.TFrame")
        self.service = MarketService(auth)
        self.session = session
        self.can_modify = session.role == "admin"
        self.rows: dict[str, dict[str, Any]] = {}
        self.sort_column = "market_name"
        self.sort_descending = False

        ttk.Label(self, text="Markets", style="Header.TLabel").pack(
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
            text="Include inactive markets",
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
        headings = {
            "market_name": "Market",
            "state": "State",
            "status": "Status",
        }
        widths = {"market_name": 360, "state": 90, "status": 110}
        for column in self.COLUMNS:
            self.tree.heading(
                column,
                text=headings[column],
                command=lambda selected=column: self.sort_by(selected),
            )
            self.tree.column(column, width=widths[column], minwidth=70)

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
            ("Add Market", self.add),
            ("Edit Market", self.edit),
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

        if not self.can_modify:
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
        headings = {
            "market_name": "Market",
            "state": "State",
            "status": "Status",
        }
        for item in self.COLUMNS:
            marker = (
                " ▼"
                if item == column and descending
                else " ▲" if item == column else ""
            )
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
                self.service.search_markets(query, bool(self.inactive_var.get()))
                if query
                else self.service.list_markets(bool(self.inactive_var.get()))
            )
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return

        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for row in rows:
            market_id = int(row["market_id"])
            iid = f"market-{market_id}"
            display_row = {
                **row,
                "market_name": row.get("market_name") or "",
                "state": row.get("state") or "",
                "status": row.get("status") or "",
            }
            self.rows[iid] = display_row
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    display_row["market_name"],
                    display_row["state"],
                    display_row["status"],
                ),
            )
        self._sort_tree()
        count = len(rows)
        self.status_var.set(
            f"{count} market record(s) found." if rows else "No markets found."
        )
        iid = f"market-{select_id}" if select_id else None
        if iid and self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def selected(self) -> dict[str, Any] | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Markets", "Select a market first.", parent=self)
            return None
        return self.rows.get(selection[0])

    def _error(self, exc: Exception) -> None:
        messagebox.showerror("Markets", str(exc), parent=self)

    def add(self) -> None:
        values = show_market_form(self)
        if values is None:
            return
        try:
            market_id = self.service.create_market(
                self.session,
                values["market_name"],
                values["state"],
                values["status"],
            )
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        self.refresh(market_id)
        self.status_var.set("Market added successfully.")

    def edit(self) -> None:
        row = self.selected()
        if not row:
            return
        market_id = int(row["market_id"])
        try:
            current = self.service.get_market(market_id)
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        if current is None:
            self._error(LookupError("Market not found"))
            return

        values = show_market_form(self, current)
        if values is None:
            return
        original = (
            str(current.get("market_name") or "").strip(),
            str(current.get("state") or "").strip().upper(),
            str(current.get("status") or "Active").strip().title(),
        )
        updated = (
            values["market_name"],
            values["state"],
            values["status"],
        )
        if updated == original:
            self.status_var.set("No changes were made.")
            return

        try:
            self.service.update_market(
                self.session,
                market_id,
                values["market_name"],
                values["state"],
                values["status"],
            )
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        self.refresh(market_id)
        self.status_var.set("Market updated.")

    def toggle_active(self) -> None:
        row = self.selected()
        if not row:
            return
        market_id = int(row["market_id"])
        activate = row.get("status") != "Active"
        action = "activate" if activate else "deactivate"
        if not messagebox.askyesno(
            "Confirm status change",
            f"{action.title()} {row['market_name']}?",
            parent=self,
        ):
            return
        try:
            self.service.set_market_active(self.session, market_id, activate)
        except EXPECTED_ERRORS as exc:
            self._error(exc)
            return
        self.refresh(market_id)
        self.status_var.set(
            f"Market {'activated' if activate else 'deactivated'}."
        )


def show_market_form(
    parent: tk.Misc, current: dict[str, Any] | None = None
) -> dict[str, str] | None:
    """Collect and validate the editable Market fields."""

    current = current or {}
    result: dict[str, str] | None = None
    window = tk.Toplevel(parent)
    window.withdraw()
    window.title("Edit Market" if current else "Add Market")
    body = ttk.Frame(window, padding=PADDING)
    body.pack(fill="both", expand=True)
    body.columnconfigure(1, weight=1)

    name_var = tk.StringVar(value=str(current.get("market_name") or ""))
    state_var = tk.StringVar(value=str(current.get("state") or "").upper())
    status_var = tk.StringVar(value=str(current.get("status") or "Active").title())

    ttk.Label(body, text="Market Name").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=5
    )
    name_entry = ttk.Entry(body, textvariable=name_var, width=42)
    name_entry.grid(row=0, column=1, sticky="ew", pady=5)

    ttk.Label(body, text="State").grid(
        row=1, column=0, sticky="w", padx=(0, 10), pady=5
    )
    state_combo = ttk.Combobox(
        body,
        textvariable=state_var,
        values=US_STATE_CODES,
        state="readonly",
        width=8,
    )
    state_combo.grid(row=1, column=1, sticky="w", pady=5)

    ttk.Label(body, text="Status").grid(
        row=2, column=0, sticky="nw", padx=(0, 10), pady=5
    )
    status_frame = ttk.Frame(body)
    status_frame.grid(row=2, column=1, sticky="w", pady=5)
    ttk.Radiobutton(
        status_frame, text="Active", variable=status_var, value="Active"
    ).pack(side="left", padx=(0, 14))
    ttk.Radiobutton(
        status_frame, text="Inactive", variable=status_var, value="Inactive"
    ).pack(side="left")

    def cancel(_event=None) -> None:
        close_modal(window)

    def submit(_event=None) -> None:
        nonlocal result
        market_name = name_var.get().strip()
        state = state_var.get().strip().upper()
        status = status_var.get().strip().title()
        if not market_name:
            messagebox.showerror(
                "Market", "Market name is required.", parent=window
            )
            name_entry.focus_set()
            return
        if state not in US_STATE_CODES:
            messagebox.showerror(
                "Market", "Select a state.", parent=window
            )
            state_combo.focus_set()
            return
        if status not in MarketService.VALID_STATUSES:
            messagebox.showerror(
                "Market", "Status must be Active or Inactive.", parent=window
            )
            return
        result = {
            "market_name": market_name,
            "state": state,
            "status": status,
        }
        close_modal(window)

    buttons = ttk.Frame(body)
    buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Save", command=submit).pack(side="left", padx=3)
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="left", padx=3)
    window.bind("<Return>", submit)
    window.bind("<Escape>", cancel)
    window.protocol("WM_DELETE_WINDOW", cancel)
    prepare_modal_dialog(window, parent)
    name_entry.focus_set()
    name_entry.selection_range(0, "end")
    window.wait_window()
    return result
