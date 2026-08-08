"""Single business-area workspace for all incoming and outgoing payments."""

import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk

from app.services.technician_payment_service import (
    DIRECT_PAYMENT_CATEGORIES, DIRECT_PAYMENT_STATUSES, PAYMENT_METHODS,
    TechnicianPaymentService,
)
from app.services.technician_service import TechnicianService
from app.ui.payment_batch_manager import PaymentBatchManager
from app.ui.payment_helpers import parse_currency
from app.ui.technician_payment_form import TechnicianPaymentForm
from app.ui.technician_earnings_manager import TechnicianEarningsManager
from app.ui.technician_payment_run_manager import TechnicianPaymentRunManager
from app.ui.styles import PADDING


class DirectPaymentPanel(ttk.Frame):
    """Central, permission-aware entry point for one-off technician payments."""
    def __init__(self, parent, auth, session):
        super().__init__(parent, padding=PADDING)
        self.service, self.session = TechnicianPaymentService(auth), session
        technicians = TechnicianService(auth).list_technicians(False)
        self.technicians = {f"{t.get('preferred_name') or t['first_name']} {t['last_name']} ({t['tech_code']})": t['tech_id'] for t in technicians}
        self.vars = {name: tk.StringVar() for name in ("technician","date","category","amount","description","job","component","method","reference","status")}
        defaults = {"date": date.today().isoformat(), "category": DIRECT_PAYMENT_CATEGORIES[0],
                    "method": PAYMENT_METHODS[0], "status": "Draft"}
        for key,value in defaults.items(): self.vars[key].set(value)
        ttk.Label(self,text="Direct Technician Payment",style="Header.TLabel").grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,8))
        specs=(("technician","Technician",tuple(self.technicians)),("date","Payment date (YYYY-MM-DD)",None),
               ("category","Category",DIRECT_PAYMENT_CATEGORIES),("amount","Amount",None),
               ("description","Description / reason",None),("job","Job ID (optional)",None),
               ("component","Financial component (optional)",("","Capture","Travel","Off Hours","Other")),
               ("method","Payment method",PAYMENT_METHODS),("reference","Reference number",None),
               ("status","Lifecycle status",DIRECT_PAYMENT_STATUSES))
        for row,(key,label,values) in enumerate(specs,1):
            ttk.Label(self,text=label).grid(row=row,column=0,sticky="w",padx=(0,12),pady=4)
            widget=(ttk.Combobox(self,textvariable=self.vars[key],values=values,state="readonly") if values else
                    ttk.Entry(self,textvariable=self.vars[key]))
            widget.grid(row=row,column=1,sticky="ew",pady=4)
        self.columnconfigure(1,weight=1)
        self.save = ttk.Button(self,text="Save Direct Payment",command=self.submit)
        self.save.grid(row=len(specs)+1,column=1,sticky="e",pady=10)
        if session.role not in {"admin","operator"}: self.save.configure(state="disabled")
        self.message=tk.StringVar(value="Direct items use the technician ledger and never require a Matterport receipt.")
        ttk.Label(self,textvariable=self.message,style="Status.TLabel").grid(row=len(specs)+2,column=0,columnspan=2,sticky="w")

    def submit(self):
        try:
            technician_id=self.technicians.get(self.vars["technician"].get())
            if not technician_id: raise ValueError("Select a technician")
            job=int(self.vars["job"].get()) if self.vars["job"].get().strip() else None
            payment=self.service.create_direct_payment(self.session,technician_id=technician_id,
                payment_date=self.vars["date"].get().strip(),category=self.vars["category"].get(),
                amount_cents=parse_currency(self.vars["amount"].get()),description=self.vars["description"].get(),
                status=self.vars["status"].get(),job_id=job,
                financial_component=self.vars["component"].get() or None,
                payment_method=self.vars["method"].get(),reference=self.vars["reference"].get() or None)
        except Exception as exc:
            messagebox.showerror("Direct Technician Payment",str(exc),parent=self); return
        self.message.set(f"Saved permanent payment record #{payment['technician_payment_id']} ({payment['payment_status']}).")


class PaymentsWorkspace(ttk.Frame):
    TAB_NAMES=("Matterport Payments","Earnings Review","Technician Payment Runs",
               "Issue Technician Payment","Exceptions / Reconciliation")
    def __init__(self,parent,auth,session):
        super().__init__(parent,padding=PADDING,style="App.TFrame")
        ttk.Label(self,text="Payments",style="Header.TLabel").pack(anchor="w",pady=(0,8))
        book=ttk.Notebook(self);book.pack(fill="both",expand=True)
        tabs=[ttk.Frame(book) for _ in self.TAB_NAMES]
        for frame,name in zip(tabs,self.TAB_NAMES): book.add(frame,text=name)
        PaymentBatchManager(tabs[0],auth,session).pack(fill="both",expand=True)
        TechnicianEarningsManager(tabs[1],auth,session).pack(fill="both",expand=True)
        TechnicianPaymentRunManager(tabs[2],auth,session).pack(fill="both",expand=True)
        TechnicianPaymentForm(tabs[3],auth,session).pack(fill="both",expand=True)
        ttk.Label(tabs[4],text="Reconciliation exceptions are opened from a Matterport payment batch.\nSelect a batch in the first tab to review unmatched, ambiguous, or amount-review items.",padding=PADDING).pack(anchor="w")
