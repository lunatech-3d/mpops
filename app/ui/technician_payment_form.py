"""Centralized, allocation-aware technician payment entry form."""

from __future__ import annotations

import tkinter as tk
from datetime import date, datetime
from tkinter import messagebox, simpledialog, ttk

from app.services.technician_payment_service import (DIRECT_PAYMENT_CATEGORIES, PAYMENT_METHODS,
                                                     TechnicianPaymentService)
from app.services.technician_service import TechnicianService
from app.ui.payment_helpers import format_cents as format_currency, parse_currency
from app.ui.styles import PADDING


class TechnicianPaymentForm(ttk.Frame):
    """One entry surface for new payments and already-paid bank transactions."""

    STATUSES=("Draft","Approved","Scheduled","Paid")
    NON_JOB_TYPES=DIRECT_PAYMENT_CATEGORIES

    def __init__(self,parent,auth,session,technician_id=None,earning_ids=None,on_saved=None):
        super().__init__(parent,padding=PADDING)
        self.service=TechnicianPaymentService(auth);self.session=session
        technicians=TechnicianService(auth).list_technicians(False)
        self.technicians={f"{t.get('preferred_name') or t['first_name']} {t['last_name']} ({t['tech_code']})":t['tech_id'] for t in technicians}
        self.vars={key:tk.StringVar() for key in ("technician","date","amount","method","status","reference","description","notes","allocated","unallocated","balance")}
        self.historical=tk.BooleanVar(value=False);self.confirmed=tk.BooleanVar(value=True)
        # Keep email drafting as an explicit form choice instead of asking only
        # after the payment has already been recorded.  reset() deliberately does
        # not change this value, so the operator's choice survives successive
        # payments entered through this form.
        self.generate_email=tk.BooleanVar(value=True)
        self.initial_earning_ids=set(earning_ids or ());self.on_saved=on_saved
        self.vars["date"].set(date.today().strftime("%m/%d/%Y"));self.vars["method"].set(PAYMENT_METHODS[0]);self.vars["status"].set("Paid")
        self.allocations={};self.non_job=[];self._technician_name=""
        title=ttk.Frame(self);title.pack(fill="x")
        ttk.Label(title,text="Record Technician Payment",style="Header.TLabel").pack(side="left")
        ttk.Label(self,text="This records a payment issued outside Matterport Ops.\nIt does not send or schedule money through PNC PINACLE.",style="Status.TLabel").pack(anchor="w",pady=(4,2))
        fields=ttk.Frame(self);fields.pack(fill="x",pady=8);fields.columnconfigure(1,weight=1);fields.columnconfigure(3,weight=1)
        specs=(("technician","Technician",tuple(self.technicians)),("date","Payment date (MM/DD/YYYY)",None),("amount","Amount issued through PNC PINACLE",None),("method","Payment method",PAYMENT_METHODS),("reference","PINACLE confirmation/reference",None),("description","Description / memo",None),("notes","Internal notes",None))
        for index,(key,label,values) in enumerate(specs):
            row,col=divmod(index,2);col*=2
            ttk.Label(fields,text=label).grid(row=row,column=col,sticky="w",padx=(0,8),pady=3)
            widget=ttk.Combobox(fields,textvariable=self.vars[key],values=values,state="readonly") if values else ttk.Entry(fields,textvariable=self.vars[key])
            widget.grid(row=row,column=col+1,sticky="ew",padx=(0,14),pady=3)
            if key=="technician":widget.bind("<<ComboboxSelected>>",self._change_technician)
            if key=="amount":widget.bind("<KeyRelease>",lambda _e:self._totals())
        ttk.Label(self,textvariable=self.vars["balance"],style="Status.TLabel").pack(anchor="w",pady=(4,2))
        tree_frame=ttk.Frame(self);tree_frame.pack(fill="both",expand=True)
        columns=("job","project","date","component","approved","paid","due","allocate")
        self.tree=ttk.Treeview(tree_frame,columns=columns,show="headings",height=10)
        labels=("Job ID","Project","Service date","Earning type","Approved","Previously paid","Balance due","Allocate now")
        for col,label in zip(columns,labels):self.tree.heading(col,text=label);self.tree.column(col,width=110,anchor="e" if col in {"approved","paid","due","allocate"} else "w")
        scroll=ttk.Scrollbar(tree_frame,orient="vertical",command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left",fill="both",expand=True);scroll.pack(side="right",fill="y")
        actions=ttk.Frame(self);actions.pack(fill="x",pady=6)
        ttk.Button(actions,text="Allocate Selected",command=self.allocate_selected).pack(side="left")
        ttk.Button(actions,text="Allocate Oldest First",command=self.allocate_oldest).pack(side="left",padx=5)
        ttk.Button(actions,text="Clear Allocation",command=self.clear_selected).pack(side="left")
        ttk.Button(actions,text="Add Non-job Item",command=self.add_non_job).pack(side="left",padx=5)
        totals=ttk.Frame(self);totals.pack(fill="x")
        for label,key in (("Payment total","amount"),("Allocated to jobs/items","allocated"),("Unallocated amount","unallocated")):
            ttk.Label(totals,text=label).pack(side="left",padx=(0,4));ttk.Label(totals,textvariable=self.vars[key]).pack(side="left",padx=(0,18))
        buttons=ttk.Frame(self);buttons.pack(fill="x",pady=(8,0))
        ttk.Checkbutton(buttons,text="Generate payment email after recording",
                        variable=self.generate_email).pack(side="left")
        self.save=ttk.Button(buttons,text="Record as Paid",command=self.submit);self.save.pack(side="right")
        ttk.Button(buttons,text="Cancel",command=self.reset).pack(side="right",padx=6)
        if session.role not in {"admin","operator"}:self.save.configure(state="disabled")
        if technician_id is not None:
            selected=next((name for name,value in self.technicians.items() if value==technician_id),None)
            if selected:self.vars["technician"].set(selected);self._technician_name=selected;self.refresh_earnings()
        self._totals()

    def _mode_changed(self):
        if self.historical.get():self.vars["status"].set("Paid")
        self.save.configure(text="Record Historical Payment" if self.historical.get() else "Save Payment")

    def _change_technician(self,_event=None):
        selected=self.vars["technician"].get()
        if self.allocations and selected!=self._technician_name and not messagebox.askyesno("Change technician","Changing technicians will clear entered allocations. Continue?",parent=self):
            self.vars["technician"].set(self._technician_name);return
        self._technician_name=selected;self.allocations.clear();self.non_job.clear();self.refresh_earnings()

    def refresh_earnings(self):
        self.tree.delete(*self.tree.get_children());tech_id=self.technicians.get(self.vars["technician"].get())
        rows=self.service.list_outstanding_earnings(tech_id) if tech_id else []
        for row in rows:
            service=(row.get("service_date") or "")[:10]
            try:service=datetime.strptime(service,"%Y-%m-%d").strftime("%m/%d/%Y")
            except ValueError:service="—"
            self.tree.insert("","end",iid=str(row["technician_earning_id"]),values=(row.get("external_job_id") or "—",row.get("project_name_source") or "—",service,row.get("entry_type") or "—",format_currency(row["net_earning_cents"]),format_currency(row["previously_paid_cents"]),format_currency(row["balance_due_cents"]),format_currency(self.allocations.get(row["technician_earning_id"],0))))
            if row["technician_earning_id"] in self.initial_earning_ids:
                self.allocations[row["technician_earning_id"]]=row["balance_due_cents"]
                self.tree.set(str(row["technician_earning_id"]),"allocate",format_currency(row["balance_due_cents"]))
        if self.initial_earning_ids:
            self.vars["amount"].set(f"{sum(self.allocations.values())/100:.2f}")
        due=sum(r["balance_due_cents"] for r in rows);self.vars["balance"].set(f"Current approved balance due: {format_currency(due)}" if rows else "No approved outstanding job earnings. A classified non-job item may still be entered.");self._totals()

    def allocate_selected(self):
        for iid in self.tree.selection():
            due=parse_currency(self.tree.set(iid,"due"));value=simpledialog.askstring("Allocation",f"Allocate up to {format_currency(due)}",initialvalue=f"{due/100:.2f}",parent=self)
            if value is not None:
                cents=parse_currency(value)
                if cents<0 or cents>due:messagebox.showerror("Allocation","Allocation cannot exceed the earning balance.",parent=self);return
                self.allocations[int(iid)]=cents;self.tree.set(iid,"allocate",format_currency(cents))
        self._totals()

    def allocate_oldest(self):
        try:remaining=parse_currency(self.vars["amount"].get())-sum(x["amount_cents"] for x in self.non_job)
        except ValueError:return
        technician_id=self.technicians.get(self.vars["technician"].get())
        if not technician_id:return
        self.allocations={item["earning_id"]:item["amount_cents"] for item in
                          self.service.build_fifo_allocations(technician_id,max(0,remaining))}
        for iid in self.tree.get_children():
            self.tree.set(iid,"allocate",format_currency(self.allocations.get(int(iid),0)))
        self._totals()

    def clear_selected(self):
        for iid in self.tree.selection():self.allocations.pop(int(iid),None);self.tree.set(iid,"allocate",format_currency(0))
        self._totals()

    def add_non_job(self):
        category=simpledialog.askstring("Non-job item",f"Type: {', '.join(self.NON_JOB_TYPES)}",parent=self)
        if category not in self.NON_JOB_TYPES:messagebox.showerror("Non-job item","Select a supported classification.",parent=self);return
        amount=simpledialog.askstring("Non-job item","Amount",parent=self);description=simpledialog.askstring("Non-job item","Description",parent=self)
        try:self.non_job.append({"type":category,"amount_cents":parse_currency(amount or ""),"description":description or ""})
        except ValueError:messagebox.showerror("Non-job item","Enter a valid amount.",parent=self)
        self._totals()

    def _totals(self):
        try:total=parse_currency(self.vars["amount"].get()) if self.vars["amount"].get().strip() else 0
        except ValueError:total=0
        allocated=sum(self.allocations.values())+sum(x["amount_cents"] for x in self.non_job)
        self.vars["allocated"].set(format_currency(allocated));self.vars["unallocated"].set(format_currency(total-allocated))

    def submit(self):
        try:
            payment=self.service.create_manual_payment(self.session,technician_id=self.technicians.get(self.vars["technician"].get()),payment_date=datetime.strptime(self.vars["date"].get(),"%m/%d/%Y").date().isoformat(),amount_cents=parse_currency(self.vars["amount"].get()),payment_method=self.vars["method"].get(),status=self.vars["status"].get(),reference=self.vars["reference"].get(),description=self.vars["description"].get(),notes=self.vars["notes"].get(),allocations=[{"earning_id":key,"amount_cents":value} for key,value in self.allocations.items() if value],non_job_items=self.non_job,historical=self.historical.get(),technician_confirmed=self.confirmed.get())
        except Exception as exc:messagebox.showerror("Technician Payment",str(exc),parent=self);return
        messagebox.showinfo("Technician Payment",f"Recorded paid payment #{payment['technician_payment_id']}.",parent=self)
        if self.generate_email.get():
            from app.ui.payment_email_dialog import generate_and_open_payment_email
            generate_and_open_payment_email(self,self.service,self.session,payment["technician_payment_id"])
        if self.on_saved:self.on_saved(payment)
        else:self.reset()

    def reset(self):
        for key in ("technician","amount","reference","description","notes"):self.vars[key].set("")
        self.historical.set(False);self.confirmed.set(True);self.vars["status"].set("Paid");self.allocations.clear();self.non_job.clear();self.initial_earning_ids.clear();self.refresh_earnings()
