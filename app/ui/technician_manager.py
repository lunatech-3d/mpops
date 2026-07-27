"""Embedded technician manager and modal address-details view."""
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from app.security.user_manager import AuthorizationError
from app.services.technician_service import TechnicianService
from app.ui.address_form import ADDRESS_FIELDS, address_form_data, show_address_form
from app.ui.dialog_utils import close_modal, prepare_modal_dialog
from app.ui.styles import PADDING
from app.ui.technician_form import changed_fields, show_technician_form

EXPECTED_ERRORS = (ValueError, LookupError, AuthorizationError, sqlite3.Error)


class TechnicianController:
    """Small UI-facing adapter whose methods are easy to exercise without Tk."""
    def __init__(self, service, session):
        self.service, self.session = service, session

    @property
    def can_modify(self):
        return self.session.role == "admin"

    def load(self, query="", include_inactive=False):
        return (self.service.search_technicians(query, include_inactive)
                if query.strip() else self.service.list_technicians(include_inactive))

    def create(self, data): return self.service.create_technician(self.session, data)
    def update(self, tech_id, original, submitted):
        changes = changed_fields(original, submitted)
        return self.service.update_technician(self.session, tech_id, changes) if changes else None
    def set_active(self, tech_id, active):
        return self.service.set_technician_active(self.session, tech_id, active)
    def add_address(self, tech_id, data): return self.service.add_address(self.session, tech_id, data)
    def update_address(self, tech_id, address_id, original, submitted):
        changes = changed_fields(original, submitted, ADDRESS_FIELDS)
        return self.service.update_address(self.session, tech_id, address_id, changes) if changes else None
    def delete_address(self, tech_id, address_id):
        return self.service.delete_address(self.session, tech_id, address_id)


class TechnicianManager(ttk.Frame):
    COLUMNS = ("tech_id", "tech_code", "first_name", "last_name", "preferred_name", "status",
               "email", "mobile_phone", "hire_date", "termination_date")
    HEADINGS = ("ID", "Tech Code", "First Name", "Last Name", "Preferred Name", "Status",
                "Email", "Mobile Phone", "Hire Date", "Termination Date")

    def __init__(self, parent, auth, session, service=None):
        super().__init__(parent, padding=PADDING, style="App.TFrame")
        self.controller = TechnicianController(service or TechnicianService(auth), session)
        self.rows = {}
        ttk.Label(self, text="Technicians", style="Header.TLabel").pack(anchor="w", pady=(0, 10))
        filters = ttk.Frame(self); filters.pack(fill="x", pady=(0, 8))
        self.search_var = tk.StringVar(); self.inactive_var = tk.BooleanVar(value=False)
        ttk.Label(filters, text="Search:").pack(side="left")
        entry = ttk.Entry(filters, textvariable=self.search_var, width=35); entry.pack(side="left", padx=6)
        ttk.Button(filters, text="Search", command=self.refresh).pack(side="left")
        ttk.Checkbutton(filters, text="Include inactive technicians", variable=self.inactive_var,
                        command=self.refresh).pack(side="left", padx=12)
        ttk.Button(filters, text="Refresh", command=self.refresh).pack(side="left")
        entry.bind("<Return>", lambda _event: self.refresh())
        table = ttk.Frame(self); table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=self.COLUMNS, show="headings", selectmode="browse")
        widths = (55, 90, 105, 105, 110, 70, 155, 110, 95, 110)
        for name, heading, width in zip(self.COLUMNS, self.HEADINGS, widths):
            self.tree.heading(name, text=heading); self.tree.column(name, width=width, minwidth=50)
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew"); table.rowconfigure(0, weight=1); table.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _event: self.view_details())
        actions = ttk.Frame(self); actions.pack(fill="x", pady=(8, 0))
        self.mutation_buttons = []
        for label, command in (("Add Technician", self.add), ("Edit Technician", self.edit),
                               ("Activate / Deactivate", self.toggle_active)):
            button = ttk.Button(actions, text=label, command=command); button.pack(side="left", padx=(0, 6))
            self.mutation_buttons.append(button)
        ttk.Button(actions, text="View Details", command=self.view_details).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Refresh", command=self.refresh).pack(side="left")
        self.status = tk.StringVar(); ttk.Label(self, textvariable=self.status, style="Status.TLabel").pack(anchor="w", pady=(7, 0))
        if not self.controller.can_modify:
            for button in self.mutation_buttons: button.configure(state="disabled")
        self.refresh()

    def refresh(self, select_id=None):
        try: rows = self.controller.load(self.search_var.get(), bool(self.inactive_var.get()))
        except EXPECTED_ERRORS as exc:
            messagebox.showerror("Technicians", str(exc), parent=self); return
        self.tree.delete(*self.tree.get_children()); self.rows.clear()
        for row in rows:
            tech_id = int(row["tech_id"]); iid = f"tech-{tech_id}"; self.rows[iid] = row
            self.tree.insert("", "end", iid=iid, values=[row.get(c) or "" for c in self.COLUMNS])
        self.status.set(f"{len(rows)} technician(s) found." if rows else "No technicians found.")
        iid = f"tech-{select_id}" if select_id else None
        if iid and self.tree.exists(iid): self.tree.selection_set(iid); self.tree.see(iid)

    def selected(self, warn=True):
        selection = self.tree.selection()
        if not selection:
            if warn: messagebox.showwarning("Technicians", "Select a technician first.", parent=self)
            return None
        return self.rows.get(selection[0])

    def _error(self, exc): messagebox.showerror("Technicians", str(exc), parent=self)
    def add(self):
        data = show_technician_form(self)
        if data is None: return
        try: tech_id = self.controller.create(data)
        except EXPECTED_ERRORS as exc: self._error(exc); return
        self.refresh(tech_id); self.status.set("Technician added successfully.")
    def edit(self):
        row = self.selected()
        if not row: return
        try: original = self.controller.service.get_technician(int(row["tech_id"]))
        except EXPECTED_ERRORS as exc: self._error(exc); return
        if original is None: self._error(LookupError("Technician not found")); return
        data = show_technician_form(self, original)
        if data is None: return
        try: result = self.controller.update(int(row["tech_id"]), original, data)
        except EXPECTED_ERRORS as exc: self._error(exc); return
        self.refresh(int(row["tech_id"])); self.status.set("Technician updated." if result else "No changes were made.")
    def toggle_active(self):
        row = self.selected()
        if not row: return
        activate = row.get("status") != "Active"; name = row.get("preferred_name") or f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
        text = (f"Reactivate {name}?" if activate else
                f"Deactivate {name}?\n\nThe technician will remain in the database but will no longer appear in the active technician list.")
        if not messagebox.askyesno("Confirm status change", text, parent=self): return
        try: self.controller.set_active(int(row["tech_id"]), activate)
        except EXPECTED_ERRORS as exc: self._error(exc); return
        self.refresh(int(row["tech_id"])); self.status.set("Technician reactivated." if activate else "Technician deactivated.")
    def view_details(self):
        row = self.selected()
        if row: TechnicianDetails(self, self.controller, int(row["tech_id"]))


class TechnicianDetails:
    COLUMNS = ("is_primary", "address_1", "address_2", "city", "state", "zip_code", "effective_date", "end_date")
    def __init__(self, parent, controller, tech_id):
        self.parent, self.controller, self.tech_id, self.rows = parent, controller, tech_id, {}
        try: technician = controller.service.get_technician(tech_id)
        except EXPECTED_ERRORS as exc: messagebox.showerror("Technician Details", str(exc), parent=parent); return
        if not technician: messagebox.showerror("Technician Details", "Technician not found.", parent=parent); return
        self.window = tk.Toplevel(parent); self.window.withdraw(); self.window.title("Technician Details"); self.window.geometry("950x480")
        body = ttk.Frame(self.window, padding=PADDING); body.pack(fill="both", expand=True)
        name = f"{technician['first_name']} {technician['last_name']}"
        ttk.Label(body, text=name, style="Header.TLabel").pack(anchor="w")
        ttk.Label(body, text=f"{technician['tech_code']}  •  {technician['status']}  •  {technician.get('email') or 'No email'}").pack(anchor="w", pady=(0, 10))
        self.tree = ttk.Treeview(body, columns=self.COLUMNS, show="headings", selectmode="browse")
        headings = ("Primary", "Address 1", "Address 2", "City", "State", "ZIP", "Effective Date", "End Date")
        for field, heading in zip(self.COLUMNS, headings): self.tree.heading(field, text=heading); self.tree.column(field, width=105)
        self.tree.pack(fill="both", expand=True); self.tree.bind("<<TreeviewSelect>>", lambda _e: self.update_buttons())
        bar = ttk.Frame(body); bar.pack(fill="x", pady=(8, 0)); self.buttons = []
        for label, command in (("Add Address", self.add), ("Edit Address", self.edit),
                               ("Set as Primary", self.set_primary), ("Delete Address", self.delete)):
            button=ttk.Button(bar,text=label,command=command); button.pack(side="left",padx=(0,6)); self.buttons.append(button)
        ttk.Button(bar,text="Close",command=lambda: close_modal(self.window)).pack(side="right")
        self.status=tk.StringVar(); ttk.Label(body,textvariable=self.status,style="Status.TLabel").pack(anchor="w",pady=(6,0))
        self.refresh(); self.window.protocol("WM_DELETE_WINDOW",lambda:close_modal(self.window)); prepare_modal_dialog(self.window,parent); self.window.wait_window()
    def refresh(self, select_id=None):
        try: rows=self.controller.service.list_addresses(self.tech_id)
        except EXPECTED_ERRORS as exc: messagebox.showerror("Addresses",str(exc),parent=self.window); return
        self.tree.delete(*self.tree.get_children()); self.rows.clear()
        for row in rows:
            iid=f"address-{row['address_id']}"; self.rows[iid]=row
            values=[("Yes" if row.get(c) else "") if c == "is_primary" else row.get(c) or ""
                    for c in self.COLUMNS]
            self.tree.insert("","end",iid=iid,values=values)
        self.status.set(f"{len(rows)} address(es)." if rows else "No addresses found.")
        iid=f"address-{select_id}" if select_id else None
        if iid and self.tree.exists(iid): self.tree.selection_set(iid)
        self.update_buttons()
    def selected(self,warn=True):
        selected=self.tree.selection()
        if not selected:
            if warn: messagebox.showwarning("Addresses","Select an address first.",parent=self.window)
            return None
        return self.rows.get(selected[0])
    def update_buttons(self):
        row=self.selected(False); allowed=self.controller.can_modify
        self.buttons[0].configure(state="normal" if allowed else "disabled")
        self.buttons[1].configure(state="normal" if allowed and row else "disabled")
        self.buttons[2].configure(state="normal" if allowed and row and not row.get("is_primary") else "disabled")
        self.buttons[3].configure(state="normal" if allowed and row else "disabled")
    def add(self):
        data=show_address_form(self.window)
        if data is None:return
        try: address_id=self.controller.add_address(self.tech_id,data)
        except EXPECTED_ERRORS as exc: messagebox.showerror("Addresses",str(exc),parent=self.window);return
        self.refresh(address_id)
    def edit(self):
        row=self.selected()
        if not row:return
        data=show_address_form(self.window,row)
        if data is None:return
        try:self.controller.update_address(self.tech_id,int(row["address_id"]),row,data)
        except EXPECTED_ERRORS as exc:messagebox.showerror("Addresses",str(exc),parent=self.window);return
        self.refresh(int(row["address_id"]))
    def set_primary(self):
        row=self.selected()
        if not row:return
        try:self.controller.service.set_primary_address(self.controller.session,self.tech_id,int(row["address_id"]))
        except EXPECTED_ERRORS as exc:messagebox.showerror("Addresses",str(exc),parent=self.window);return
        self.refresh(int(row["address_id"]))
    def delete(self):
        row=self.selected()
        if not row:return
        if not messagebox.askyesno("Delete address","Delete this address?\n\nThis action removes the address record from the technician.",parent=self.window):return
        try:self.controller.delete_address(self.tech_id,int(row["address_id"]))
        except EXPECTED_ERRORS as exc:messagebox.showerror("Addresses",str(exc),parent=self.window);return
        self.refresh()
