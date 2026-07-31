"""Central technician payment-run list and testable selection controller."""
from tkinter import messagebox, ttk
from app.services.technician_payment_service import TechnicianPaymentService
from app.ui.payment_helpers import format_cents
from app.ui.styles import PADDING


class TechnicianPaymentRunController:
    def __init__(self,service,session,payment_batch_id=None):self.service,self.session,self.payment_batch_id=service,session,payment_batch_id
    @property
    def can_modify(self):return self.session.role in {"admin","operator"}
    def eligible(self,**filters):
        if self.payment_batch_id is not None and "payment_batch_id" not in filters:filters["payment_batch_id"]=self.payment_batch_id
        return self.service.list_approved_unpaid_earnings(**filters)
    def preview(self,rows):
        grouped={}
        for r in rows:
            g=grouped.setdefault(r["tech_id"],{"technician":r["technician_name"],"jobs":0,"gross_revenue_cents":0,"earnings_cents":0,"adjustments_cents":0,"payment_total_cents":0})
            g["jobs"]+=r["entry_type"]=="Calculated";g["gross_revenue_cents"]+=r["revenue_basis_cents"] if r["entry_type"]=="Calculated" else 0
            key="adjustments_cents" if r["entry_type"]=="Manual Adjustment" else "earnings_cents";g[key]+=r["net_earning_cents"];g["payment_total_cents"]+=r["net_earning_cents"]
        return grouped
    def create(self,ids,notes=None):return self.service.create_payment_run(self.session,ids,notes,source_payment_batch_id=self.payment_batch_id)


class TechnicianPaymentRunManager(ttk.Frame):
    def __init__(self,parent,auth,session,payment_batch_id=None):
        super().__init__(parent,padding=PADDING);self.controller=TechnicianPaymentRunController(TechnicianPaymentService(auth),session,payment_batch_id)
        ttk.Label(self,text="Technician Payment Runs",style="Header.TLabel").pack(anchor="w")
        self.tree=ttk.Treeview(self,columns=("created","creator","techs","earnings","total","status","date","notes"),show="tree headings")
        for c,h in zip(self.tree["columns"],("Created Date","Created By","Technician Count","Earning Count","Total Amount","Status","Payment Date","Notes")):self.tree.heading(c,text=h)
        self.tree.heading("#0",text="Run ID");self.tree.pack(fill="both",expand=True,pady=8)
        bar=ttk.Frame(self);bar.pack(fill="x");self.new_button=ttk.Button(bar,text="New Payment Run",command=self.new_run);self.new_button.pack(side="left");ttk.Button(bar,text="Refresh",command=self.refresh).pack(side="left",padx=6)
        if not self.controller.can_modify:self.new_button.configure(state="disabled")
        self.refresh()
    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.controller.service.list_payment_runs():self.tree.insert("", "end",text=r["technician_payment_run_id"],values=(r["created_at"],r["created_by_name"],r["technician_count"],r["earning_count"],format_cents(r["total_amount_cents"]),r["payment_status"],r["payment_date"] or "",r["notes"] or ""))
    def new_run(self):
        rows=self.controller.eligible()
        if not rows:messagebox.showinfo("No earnings","No approved unpaid earnings match the current filter.",parent=self);return
        messagebox.showinfo("Manual selection required",f"{len(rows)} eligible earnings are available. Select explicit earnings in the Technician Earnings screen before creating a run.",parent=self)
