"""Tk views for technician compensation and market revenue-share rules."""

from datetime import date
import sqlite3
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from app.date_utils import display_date_to_iso, format_display_date
from app.security.user_manager import AuthorizationError
from app.services.revenue_rule_service import RuleConfigurationError, RuleDataIntegrityError
from app.ui.dialog_utils import close_modal, prepare_modal_dialog
from app.ui.revenue_rule_formatting import (amount_to_cents, format_basis_points,
                                             format_cents, percentage_to_basis_points)
from app.ui.styles import PADDING

ERRORS = (ValueError, LookupError, AuthorizationError, RuleConfigurationError,
          RuleDataIntegrityError, sqlite3.Error)


def _error(parent, title, exc):
    messages = {
        RuleConfigurationError: "No applicable rule is configured for this date.",
        RuleDataIntegrityError: "Stored rules conflict. Ask an administrator to review the configuration.",
        AuthorizationError: "Only an administrator may change revenue rules.",
    }
    messagebox.showerror(title, messages.get(type(exc), str(exc)), parent=parent)


def _end_date(parent):
    value = simpledialog.askstring("End Current Rule", "Effective end date (MM/DD/YYYY):", parent=parent)
    return display_date_to_iso(value) if value is not None else None


def show_technician_rule_dialog(parent, current=None):
    return _show_rule_dialog(parent, "Technician Compensation Rule", current, technician=True)


def show_market_rule_dialog(parent, current=None):
    return _show_rule_dialog(parent, "LunaTech-East Revenue Share", current, technician=False)


def _show_rule_dialog(parent, title, current, *, technician):
    current = current or {}; result = None
    window = tk.Toplevel(parent); window.withdraw(); window.title(title)
    body = ttk.Frame(window, padding=PADDING); body.pack(fill="both", expand=True); body.columnconfigure(1, weight=1)
    component = tk.StringVar(value=current.get("compensation_component", "Overall"))
    rule_type = tk.StringVar(value=current.get("rule_type", "Percentage"))
    stored = current.get("rule_value" if technician else "share_basis_points")
    if stored is None:
        entry_value = ""
    elif not technician or current.get("rule_type", "Percentage") == "Percentage":
        entry_value = format_basis_points(stored)[:-1]
    else:
        entry_value = format_cents(stored).replace("$", "").replace(",", "")
    value = tk.StringVar(value=entry_value)
    start = tk.StringVar(value=format_display_date(current.get("effective_from")))
    end = tk.StringVar(value=format_display_date(current.get("effective_to")))
    active = tk.BooleanVar(value=bool(current.get("is_active", True)))
    notes = tk.StringVar(value=current.get("notes") or "")
    row = 0
    if technician:
        ttk.Label(body, text="Compensation Component").grid(row=row,column=0,sticky="w",pady=5)
        ttk.Combobox(body,textvariable=component,values=("Overall","Base","Travel","Off Hours"),state="readonly").grid(row=row,column=1,sticky="ew"); row += 1
        ttk.Label(body, text="Rule Type").grid(row=row,column=0,sticky="w",pady=5)
        ttk.Combobox(body,textvariable=rule_type,values=("Percentage","Flat Amount"),state="readonly").grid(row=row,column=1,sticky="ew"); row += 1
    value_label = ttk.Label(body, text="Percentage" if not technician else rule_type.get())
    value_label.grid(row=row,column=0,sticky="w",pady=5); ttk.Entry(body,textvariable=value).grid(row=row,column=1,sticky="ew"); row += 1
    hint = ttk.Label(body, text="Enter 70 for 70%")
    hint.grid(row=row,column=1,sticky="w"); row += 1
    if technician:
        def changed(*_):
            value_label.configure(text=rule_type.get())
            hint.configure(text="Enter 70 for 70%" if rule_type.get()=="Percentage" else "Enter 125.00 for $125.00")
        rule_type.trace_add("write", changed)
    for label, variable in (("Effective From (MM/DD/YYYY)",start),("Effective To (MM/DD/YYYY)",end)):
        ttk.Label(body,text=label).grid(row=row,column=0,sticky="w",pady=5); ttk.Entry(body,textvariable=variable).grid(row=row,column=1,sticky="ew"); row += 1
    ttk.Checkbutton(body,text="Active",variable=active).grid(row=row,column=1,sticky="w",pady=5); row += 1
    if not technician:
        ttk.Label(body,text="Notes").grid(row=row,column=0,sticky="w",pady=5); ttk.Entry(body,textvariable=notes).grid(row=row,column=1,sticky="ew"); row += 1
        ttk.Label(body,wraplength=540,text="LunaTech-East receives this percentage of gross revenue. LunaTech 3D receives the remainder after the technician share and LunaTech-East share are applied.").grid(row=row,column=0,columnspan=2,sticky="w",pady=8); row += 1
    def cancel(): close_modal(window)
    def save():
        nonlocal result
        try:
            effective_from = display_date_to_iso(start.get())
            if not effective_from: raise ValueError("Effective From is required.")
            parsed = (percentage_to_basis_points(value.get()) if (not technician or rule_type.get()=="Percentage") else amount_to_cents(value.get()))
            result = {"effective_from":effective_from,"effective_to":display_date_to_iso(end.get()),"is_active":bool(active.get())}
            if technician: result.update(compensation_component=component.get(),rule_type=rule_type.get(),rule_value=parsed)
            else: result.update(share_basis_points=parsed,notes=notes.get().strip() or None)
        except ValueError as exc: messagebox.showerror("Invalid rule",str(exc),parent=window); return
        close_modal(window)
    bar=ttk.Frame(body); bar.grid(row=row,column=0,columnspan=2,sticky="e",pady=(10,0))
    ttk.Button(bar,text="Save",command=save).pack(side="left",padx=3); ttk.Button(bar,text="Cancel",command=cancel).pack(side="left")
    window.protocol("WM_DELETE_WINDOW",cancel); prepare_modal_dialog(window,parent); window.wait_window(); return result


class TechnicianCompensationView(ttk.Frame):
    COLUMNS=("compensation_component","rule_type","display_value","effective_from","effective_to","is_active","applicability")
    def __init__(self,parent,controller,tech_id):
        super().__init__(parent,padding=PADDING); self.controller=controller; self.tech_id=tech_id; self.rows={}
        summary=ttk.LabelFrame(self,text="Effective Compensation",padding=8); summary.pack(fill="x")
        self.when=tk.StringVar(value=format_display_date(date.today())); self.summary=tk.StringVar()
        ttk.Label(summary,text="Evaluation Date (MM/DD/YYYY)").pack(side="left"); ttk.Entry(summary,textvariable=self.when,width=14).pack(side="left",padx=6)
        ttk.Button(summary,text="Evaluate",command=self.refresh).pack(side="left"); ttk.Label(summary,textvariable=self.summary,wraplength=650).pack(side="left",padx=12)
        ttk.Label(self,text="Job-specific or market-specific rules may override this value for an individual job.",wraplength=850).pack(anchor="w",pady=6)
        self.tree=ttk.Treeview(self,columns=self.COLUMNS,show="headings",selectmode="browse")
        for col,head in zip(self.COLUMNS,("Component","Rule Type","Value","Effective From","Effective To","Active","Applicability")):
            self.tree.heading(col,text=head); self.tree.column(col,width=110)
        self.tree.pack(fill="both",expand=True)
        bar=ttk.Frame(self); bar.pack(fill="x",pady=6)
        actions=(("Add Rule",self.add),("Edit Future Rule",self.edit),("End Current Rule",self.end),("Activate / Deactivate",self.toggle))
        for label,cmd in actions:
            button=ttk.Button(bar,text=label,command=cmd,state="normal" if controller.can_modify else "disabled"); button.pack(side="left",padx=3)
        ttk.Button(bar,text="Refresh",command=self.refresh).pack(side="left",padx=3); self.refresh()
    def selected(self):
        selected=self.tree.selection(); return self.rows.get(selected[0]) if selected else None
    def refresh(self):
        try:
            when=display_date_to_iso(self.when.get()); rule=self.controller.effective(self.tech_id,when)
            self.summary.set(f"Effective payout: {rule['display_value']}  •  Rule type: {rule['rule_type']}  •  Source: {rule['source_label']}  •  Effective from: {format_display_date(rule.get('effective_from'))}  •  Effective to: {format_display_date(rule.get('effective_to'),'Open-ended')}")
            rows=self.controller.history(self.tech_id)
        except RuleConfigurationError:
            self.summary.set("No compensation rule is configured for this technician and date."); rows=self.controller.history(self.tech_id)
        except ERRORS as exc: _error(self,"Compensation",exc); return
        self.tree.delete(*self.tree.get_children()); self.rows={}
        for row in rows:
            iid=str(row["compensation_rule_id"]); self.rows[iid]=row
            self.tree.insert("","end",iid=iid,values=(row["compensation_component"],row["rule_type"],row["display_value"],format_display_date(row.get("effective_from")),format_display_date(row.get("effective_to")),"Yes" if row["is_active"] else "No",row["applicability"]))
    def add(self):
        values=show_technician_rule_dialog(self)
        if values:
            try:self.controller.create(self.tech_id,**values);self.refresh()
            except ERRORS as exc:_error(self,"Compensation",exc)
    def edit(self):
        row=self.selected()
        if not row:return
        values=show_technician_rule_dialog(self,row)
        if values:
            try:self.controller.update_future(self.tech_id,row["compensation_rule_id"],**values);self.refresh()
            except ERRORS as exc:_error(self,"Compensation",exc)
    def end(self):
        row=self.selected(); end=_end_date(self) if row else None
        if end:
            try:self.controller.end_current(self.tech_id,row["compensation_rule_id"],end);self.refresh()
            except ERRORS as exc:_error(self,"Compensation",exc)
    def toggle(self):
        row=self.selected()
        if row and messagebox.askyesno("Confirm",("Deactivate" if row["is_active"] else "Activate")+" this rule?",parent=self):
            try:self.controller.set_active(self.tech_id,row["compensation_rule_id"],not bool(row["is_active"]));self.refresh()
            except ERRORS as exc:_error(self,"Compensation",exc)


class MarketRevenueShareWindow:
    def __init__(self,parent,controller,market):
        self.controller=controller;self.market=market;self.rows={};self.window=tk.Toplevel(parent);self.window.withdraw();self.window.title("Market Revenue Share");self.window.geometry("850x550")
        body=ttk.Frame(self.window,padding=PADDING);body.pack(fill="both",expand=True)
        ttk.Label(body,text=f"{market['market_name']} ({market.get('state') or '—'})",style="Header.TLabel").pack(anchor="w");self.summary=tk.StringVar();ttk.Label(body,textvariable=self.summary).pack(anchor="w",pady=6)
        cols=("recipient_code","display_value","effective_from","effective_to","is_active","applicability");self.tree=ttk.Treeview(body,columns=cols,show="headings",selectmode="browse")
        for col,head in zip(cols,("Recipient","Share","Effective From","Effective To","Active","Applicability")):self.tree.heading(col,text=head);self.tree.column(col,width=125)
        self.tree.pack(fill="both",expand=True);bar=ttk.Frame(body);bar.pack(fill="x",pady=6)
        for label,cmd in (("Add Revenue Share",self.add),("Edit Future Rule",self.edit),("End Current Rule",self.end),("Activate / Deactivate",self.toggle)):
            ttk.Button(bar,text=label,command=cmd,state="normal" if controller.can_modify else "disabled").pack(side="left",padx=3)
        ttk.Button(bar,text="Refresh",command=self.refresh).pack(side="left",padx=3);ttk.Button(bar,text="Close",command=lambda:close_modal(self.window)).pack(side="right")
        self.refresh();self.window.protocol("WM_DELETE_WINDOW",lambda:close_modal(self.window));prepare_modal_dialog(self.window,parent);self.window.wait_window()
    def selected(self):
        s=self.tree.selection();return self.rows.get(s[0]) if s else None
    def refresh(self):
        mid=int(self.market["market_id"])
        try:
            current=self.controller.effective(mid,date.today());self.summary.set(f"Current LunaTech-East share: {current['display_value']}  •  Effective from: {format_display_date(current['effective_from'])}  •  Effective to: {format_display_date(current.get('effective_to'),'Open-ended')}")
        except RuleConfigurationError:self.summary.set("No LunaTech-East revenue share is configured for this market and date.")
        except ERRORS as exc:_error(self.window,"Revenue Share",exc)
        try:rows=self.controller.history(mid)
        except ERRORS as exc:_error(self.window,"Revenue Share",exc);return
        self.tree.delete(*self.tree.get_children());self.rows={}
        for row in rows:
            iid=str(row["market_revenue_share_rule_id"]);self.rows[iid]=row;self.tree.insert("","end",iid=iid,values=(row["recipient_code"],row["display_value"],format_display_date(row["effective_from"]),format_display_date(row.get("effective_to")),"Yes" if row["is_active"] else "No",row["applicability"]))
    def add(self):
        values=show_market_rule_dialog(self.window)
        if values:
            try:self.controller.create(int(self.market["market_id"]),**values);self.refresh()
            except ERRORS as exc:_error(self.window,"Revenue Share",exc)
    def edit(self):
        row=self.selected();values=show_market_rule_dialog(self.window,row) if row else None
        if values:
            try:self.controller.update_future(int(self.market["market_id"]),row["market_revenue_share_rule_id"],**values);self.refresh()
            except ERRORS as exc:_error(self.window,"Revenue Share",exc)
    def end(self):
        row=self.selected();end=_end_date(self.window) if row else None
        if end:
            try:self.controller.end_current(int(self.market["market_id"]),row["market_revenue_share_rule_id"],end);self.refresh()
            except ERRORS as exc:_error(self.window,"Revenue Share",exc)
    def toggle(self):
        row=self.selected()
        if row and messagebox.askyesno("Confirm","Change this rule's active status?",parent=self.window):
            try:self.controller.set_active(int(self.market["market_id"]),row["market_revenue_share_rule_id"],not bool(row["is_active"]));self.refresh()
            except ERRORS as exc:_error(self.window,"Revenue Share",exc)
