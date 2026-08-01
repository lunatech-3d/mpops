"""Central technician earning review screen and testable controller."""
import tkinter as tk
from tkinter import messagebox, ttk

from app.services.compensation_service import CompensationService
from app.services.technician_payment_service import TechnicianPaymentService
from app.ui.payment_helpers import format_cents
from app.ui.styles import PADDING


class TechnicianEarningsController:
    def __init__(self, service, session, payment_batch_id=None, technician_id=None):
        self.service,self.session=service,session
        self.prefilter={"payment_batch_id":payment_batch_id,"technician_id":technician_id}
    @property
    def can_modify(self): return self.session.role in {"admin","operator"}
    def load(self, **filters):
        return self.service.list_earnings_for_review(**{**self.prefilter,**filters})
    def approve(self, ids): return self.service.approve_technician_earnings(self.session,ids)
    def void(self, earning_id, reason): return self.service.void_technician_earning(self.session,earning_id,reason)
    def create_payment_run(self, ids):
        batch_id=self.prefilter.get("payment_batch_id")
        return TechnicianPaymentService(self.service.auth).create_payment_run(
            self.session,ids,source_payment_batch_id=batch_id)
    def grouped_totals(self, rows):
        result={}
        for row in rows:
            item=result.setdefault(row["tech_id"],{"technician":row["technician_name"],"count":0,"net_earning_cents":0})
            item["count"]+=1;item["net_earning_cents"]+=row["net_earning_cents"]
        return result


class TechnicianEarningsManager(ttk.Frame):
    COLUMNS=("technician_name","external_job_id","job_address","job_date","market_name","payment_batch_id","payment_item_id",
             "document_number","compensation_rule_type","compensation_rule_value",
             "revenue_basis_cents","calculated_amount_cents","adjustment_amount_cents","net_earning_cents",
             "lunatech_east_amount_cents","lunatech_amount_cents","earning_status","technician_payment_id")
    def __init__(self,parent,auth,session,payment_batch_id=None,technician_id=None):
        super().__init__(parent,padding=PADDING);self.controller=TechnicianEarningsController(CompensationService(auth),session,payment_batch_id,technician_id)
        top=ttk.Frame(self);top.pack(fill="x");ttk.Label(top,text="Technician Earnings Review",style="Header.TLabel").pack(side="left")
        self.status=tk.StringVar(value="Pending");ttk.Combobox(top,textvariable=self.status,values=("All","Pending","Approved","Paid","Voided"),state="readonly",width=14).pack(side="left",padx=12)
        self.unpaid=tk.BooleanVar(value=False);ttk.Checkbutton(top,text="Show only unpaid",variable=self.unpaid).pack(side="left")
        ttk.Button(top,text="Refresh",command=self.refresh).pack(side="left",padx=6)
        area=ttk.Frame(self);area.pack(fill="both",expand=True,pady=8)
        self.tree=ttk.Treeview(area,columns=self.COLUMNS,show="headings",selectmode="extended")
        headings=("Technician","Job","Address","Job Date","Market","Matterport Batch","Payment Item ID",
                  "Payment Item","Rule Type","Rule Value","Revenue Basis","Calculated","Adjustment",
                  "Net Earning","LunaTech-East","LunaTech","Status","Payment")
        for col,label in zip(self.COLUMNS,headings):self.tree.heading(col,text=label);self.tree.column(col,width=110,anchor="e" if "cents" in col else "w")
        sx=ttk.Scrollbar(area,orient="horizontal",command=self.tree.xview);sy=ttk.Scrollbar(area,orient="vertical",command=self.tree.yview);self.tree.configure(xscrollcommand=sx.set,yscrollcommand=sy.set)
        self.tree.grid(row=0,column=0,sticky="nsew");sy.grid(row=0,column=1,sticky="ns");sx.grid(row=1,column=0,sticky="ew");area.rowconfigure(0,weight=1);area.columnconfigure(0,weight=1)
        bar=ttk.Frame(self);bar.pack(fill="x")
        self.approve_button=ttk.Button(bar,text="Approve Selected",command=self.approve);self.approve_button.pack(side="left")
        self.payment_run_button=ttk.Button(bar,text="Create Payment Run from Selected",command=self.create_payment_run)
        self.payment_run_button.pack(side="left",padx=6)
        ttk.Button(bar,text="View Details",command=self.details).pack(side="left",padx=6)
        if not self.controller.can_modify:
            self.approve_button.configure(state="disabled");self.payment_run_button.configure(state="disabled")
        self.rows={};self.refresh()
    def refresh(self):
        self.tree.delete(*self.tree.get_children());self.rows={}
        for row in self.controller.load(status=self.status.get(),unpaid_only=self.unpaid.get()):
            eid=row["technician_earning_id"];self.rows[str(eid)]=row
            values=[format_cents(row.get(c)) if "cents" in c else row.get(c) or "" for c in self.COLUMNS]
            self.tree.insert("", "end",iid=str(eid),values=values,tags=(row["entry_type"].replace(" ","_"),row["earning_status"]))
    def approve(self):
        try:self.controller.approve([int(x) for x in self.tree.selection()]);self.refresh()
        except Exception as exc:messagebox.showerror("Approval blocked",str(exc),parent=self)
    def create_payment_run(self):
        ids=[int(x) for x in self.tree.selection()]
        if not ids:
            messagebox.showinfo("Technician Payment Run","Select approved earnings first.",parent=self);return
        try:run=self.controller.create_payment_run(ids)
        except Exception as exc:messagebox.showerror("Payment run blocked",str(exc),parent=self);return
        self.refresh()
        messagebox.showinfo("Technician Payment Run",
            f"Draft payment run #{run['technician_payment_run_id']} was created from {len(ids)} earning(s).",
            parent=self)
    def details(self):
        if not self.tree.selection():return
        data=self.controller.service.get_earning_calculation_details(int(self.tree.selection()[0]));messagebox.showinfo("Earning Details",str(data),parent=self)
