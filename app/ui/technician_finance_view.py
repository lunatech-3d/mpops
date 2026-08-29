"""Technician-specific operational jobs and finance history view."""

import tkinter as tk
from tkinter import messagebox, ttk

from app.date_utils import format_display_date
from app.services.technician_finance_service import TechnicianFinanceService
from app.services.technician_payment_service import TechnicianPaymentService
from app.ui.jobs_manager import open_job_details
from app.ui.payment_helpers import format_cents
from app.ui.styles import PADDING
from app.ui.treeview_utils import natural_sort_key, ordered_tree_items


JOB_COLUMNS = ("date", "job", "project", "job_status", "earnings_status",
               "earned", "base", "travel", "paid", "due")
JOB_HEADINGS = ("Job Date", "Job", "Project / Address", "Job Status", "Earnings Status",
                "Earned", "Base Pay", "Travel Pay", "Paid", "Balance Due")
JOB_MONEY_FIELDS = {
    "earned": "earned_cents", "base": "base_pay_cents",
    "travel": "travel_pay_cents", "paid": "paid_cents",
    "due": "approved_due_cents",
}
JOB_COLUMN_WIDTHS = {
    "date": (100, 90), "job": (115, 90), "project": (380, 220),
    "job_status": (125, 100), "earnings_status": (180, 130),
    "earned": (110, 95), "base": (110, 95), "travel": (110, 95),
    "paid": (110, 95), "due": (120, 105),
}


def technician_job_visible_values(job):
    """Return the values displayed by the Technician Jobs tree."""
    stored_job_date = job.get("completed_at") or job.get("scheduled_start_at")
    job_date = format_display_date(stored_job_date)
    project = job.get("project_name_source") or job.get("job_address") or ""
    money = lambda field: "—" if job.get(field) is None else format_cents(job[field])
    return {
        "date": job_date,
        "job": job.get("external_job_id") or "",
        "project": project,
        "job_status": job.get("job_status") or "",
        "earnings_status": job.get("finance_status") or "",
        **{column: money(field) for column, field in JOB_MONEY_FIELDS.items()},
    }


def technician_job_sort_value(job, column):
    """Return a raw cell value and its data-aware sort key."""
    visible = technician_job_visible_values(job)
    raw = visible[column]
    if column in JOB_MONEY_FIELDS:
        value = job.get(JOB_MONEY_FIELDS[column])
        return raw, 0 if value is None else int(value)
    if column == "date":
        stored = job.get("completed_at") or job.get("scheduled_start_at") or ""
        return raw, str(stored)
    return raw, natural_sort_key(raw)


def search_technician_jobs(jobs, query):
    """Case-insensitively search every displayed Technician Jobs value."""
    term = str(query or "").strip().casefold()
    if not term:
        return list(jobs)
    compact_term = term.replace("$", "").replace(",", "")
    matches = []
    for job in jobs:
        values = technician_job_visible_values(job).values()
        if any(term in str(value).casefold() or
               (compact_term and compact_term in str(value).casefold().replace("$", "").replace(",", ""))
               for value in values):
            matches.append(job)
    return matches


class TechnicianFinanceController:
    def __init__(self, service, technician_id):
        self.service, self.technician_id = service, technician_id

    def summary(self): return self.service.get_summary(self.technician_id)
    def jobs(self, view="All"): return self.service.list_jobs(self.technician_id, view)
    def payments(self): return self.service.list_payments(self.technician_id)
    def activity(self): return self.service.list_account_activity(self.technician_id)


class TechnicianFinanceView(ttk.Frame):
    JOB_COLUMNS = JOB_COLUMNS
    JOB_HEADINGS = JOB_HEADINGS

    def __init__(self, parent, auth, technician_id, mode="all", job_opener=None, session=None):
        super().__init__(parent, padding=PADDING)
        self.auth, self.session, self.job_opener = auth, session, job_opener or open_job_details
        self.controller = TechnicianFinanceController(TechnicianFinanceService(auth), technician_id)
        self.job_rows = {}; self.payment_rows = {}; self.activity_rows = {}
        self.job_sort_column = None; self.job_sort_descending = False
        summary = ttk.LabelFrame(self, text="Financial Summary", padding=5)
        if mode != "jobs": summary.pack(fill="x", pady=(0, 8))
        self.summary_vars = {}
        for column, (key, label) in enumerate((("upcoming_expected_cents", "Upcoming Expected"),
                ("completed_earnings_cents", "Completed Earnings"), ("balance_due_cents", "Current Balance Due"),
                ("total_paid_cents", "Total Paid"), ("pending_approval_cents", "Pending Approval"),
                ("pending_direct_cents", "Pending Direct / Expense Items"))):
            box = ttk.LabelFrame(summary, text=label, padding=7); box.grid(row=0, column=column, sticky="nsew", padx=3)
            variable = tk.StringVar(value="—"); self.summary_vars[key] = variable
            ttk.Label(box, textvariable=variable, style="Header.TLabel").pack()
            summary.columnconfigure(column, weight=1)
        notebook = ttk.Notebook(self); notebook.pack(fill="both", expand=True)
        ledger_tab = ttk.Frame(notebook, padding=5)
        jobs_tab = ttk.Frame(notebook, padding=5); payments_tab = ttk.Frame(notebook, padding=5)
        if mode in {"all", "finances"}: notebook.add(ledger_tab, text="Account Ledger")
        if mode in {"all", "jobs"}: notebook.add(jobs_tab, text="Assigned Jobs")
        if mode in {"all", "finances"}: notebook.add(payments_tab, text="Payment History")
        if mode == "finances": notebook.select(payments_tab)
        self.ledger_tree = ttk.Treeview(ledger_tab, columns=("date","type","description","job","owed","payment","balance","reference"), show="headings")
        for column, heading, width in zip(self.ledger_tree["columns"], ("Date","Type","Description","Job","Owed","Payment","Balance","Reference"), (105,180,300,110,105,105,105,150)):
            self.ledger_tree.heading(column,text=heading)
            self.ledger_tree.column(column,width=width,anchor="e" if column in {"owed","payment","balance"} else "w")
        self.ledger_tree.pack(fill="both",expand=True)
        self.ledger_tree.bind("<Double-1>",self._open_ledger_job)
        filters = ttk.Frame(jobs_tab); filters.pack(fill="x")
        ttk.Label(filters, text="Search:").pack(side="left")
        self.job_search = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.job_search, width=28); search.pack(side="left", padx=(6, 12)); search.bind("<Return>", lambda _event: self.refresh())
        ttk.Label(filters, text="View:").pack(side="left")
        self.job_view = tk.StringVar(value="All")
        view = ttk.Combobox(filters, textvariable=self.job_view, values=("All", "Upcoming", "Completed", "Cancelled", "Owed"), state="readonly", width=12)
        view.pack(side="left", padx=6); view.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        ttk.Button(filters, text="Search", command=self.refresh).pack(side="left", padx=(6, 0)); ttk.Button(filters, text="Clear", command=self.clear_job_search).pack(side="left", padx=6); ttk.Button(filters, text="Refresh", command=self.refresh).pack(side="left")
        table = ttk.Frame(jobs_tab); table.pack(fill="both", expand=True, pady=(7, 0))
        self.jobs_tree = ttk.Treeview(table, columns=self.JOB_COLUMNS, show="headings", selectmode="browse")
        for column, heading in zip(self.JOB_COLUMNS, self.JOB_HEADINGS):
            self.jobs_tree.heading(column, text=heading, command=lambda selected=column: self.sort_jobs_by(selected))
            width, minimum = JOB_COLUMN_WIDTHS[column]
            self.jobs_tree.column(column, width=width, minwidth=minimum, stretch=column == "project", anchor="e" if column in JOB_MONEY_FIELDS else "w")
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.jobs_tree.yview); xbar = ttk.Scrollbar(table, orient="horizontal", command=self.jobs_tree.xview)
        self.jobs_tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set); self.jobs_tree.grid(row=0, column=0, sticky="nsew"); ybar.grid(row=0, column=1, sticky="ns"); xbar.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1); table.columnconfigure(0, weight=1); self.jobs_tree.bind("<Double-1>", self._open_double_clicked_job)
        payment_actions=ttk.Frame(payments_tab);payment_actions.pack(fill="x")
        ttk.Label(payment_actions, text="Payment History", style="Header.TLabel").pack(side="left")
        self.email_button=ttk.Button(payment_actions,text="Generate Payment Email",command=self.payment_email); self.email_button.pack(side="right"); self.email_button.configure(state="disabled")
        self.payment_guidance = tk.StringVar(); ttk.Label(payments_tab, textvariable=self.payment_guidance, style="Status.TLabel").pack(anchor="w", pady=(4, 0))
        self.payments_tree = ttk.Treeview(payments_tab, columns=("date","method","reference","status","amount","base","travel","other"), show="tree headings")
        self.payments_tree.heading("#0", text="Payment / Job")
        for column, heading in zip(self.payments_tree["columns"], ("Date","Method","Reference","Status","Amount Applied","Base Pay","Travel Pay","Other")):
            self.payments_tree.heading(column, text=heading); self.payments_tree.column(column, width=110)
        self.payments_tree.pack(fill="both", expand=True, pady=(7, 0)); self.payments_tree.bind("<<TreeviewSelect>>",self._payment_selected)
        self.status = tk.StringVar(); ttk.Label(self, textvariable=self.status, style="Status.TLabel").pack(anchor="w", pady=(6, 0)); self.mode = mode; self.refresh()

    def _selected_payment(self):
        selection=self.payments_tree.selection()
        if not selection:return None
        root=selection[0].split("-item-")[0]
        return self.payment_rows.get(root)

    def _payment_selected(self,_event=None): self._update_payment_email_action()

    def _update_payment_email_action(self):
        payment=self._selected_payment(); is_current_paid = bool(payment and payment.get("payment_status") == "Paid" and not payment.get("reversed_at")); authorized = bool(self.session and self.session.role in {"admin", "operator"}); active_draft = bool(payment and payment.get("email_draft_status") == "Draft Generated")
        label = "Regenerate Draft" if is_current_paid and active_draft else "Generate Payment Email"; self.email_button.configure(text=label, state="normal" if authorized and is_current_paid else "disabled")
        if not self.payment_rows: guidance = "No technician payments have been recorded."
        elif is_current_paid: guidance = "Generate a reviewable email describing the jobs and amounts included in this payment."
        elif not any(row.get("payment_status") == "Paid" and not row.get("reversed_at") for row in self.payment_rows.values()): guidance = "Payment emails are available after a payment has been recorded as Paid."
        else: guidance = "Select a current Paid payment to generate a payment email."
        self.payment_guidance.set(guidance)

    def payment_email(self):
        payment=self._selected_payment()
        if (not self.session or self.session.role not in {"admin", "operator"} or not payment or payment.get("payment_status") != "Paid" or payment.get("reversed_at")):
            messagebox.showinfo("Payment Email","Select a paid payment.",parent=self);return
        from app.ui.payment_email_dialog import generate_and_open_payment_email
        if generate_and_open_payment_email(self,TechnicianPaymentService(self.auth),self.session,payment["technician_payment_id"]):self.refresh()

    @staticmethod
    def _money(value): return "—" if value is None else format_cents(value)

    def clear_job_search(self): self.job_search.set(""); self.refresh()

    def _open_double_clicked_job(self, event):
        if self.jobs_tree.identify_region(event.x, event.y) not in ("cell", "tree"): return
        iid = self.jobs_tree.identify_row(event.y); job = self.job_rows.get(iid)
        if not job:return
        self.jobs_tree.selection_set(iid); self.jobs_tree.focus(iid)
        try:self.job_opener(self, self.auth, int(job["job_id"]), wait=True)
        except Exception as exc:messagebox.showerror("Job Details", str(exc), parent=self);return
        self.refresh()

    def _open_ledger_job(self, event):
        iid=self.ledger_tree.identify_row(event.y);item=self.activity_rows.get(iid)
        if not item or not item.get("job_id"):return
        try:self.job_opener(self,self.auth,int(item["job_id"]),wait=True)
        except Exception as exc:messagebox.showerror("Job Details",str(exc),parent=self)
        self.refresh()

    def sort_jobs_by(self, column):
        if self.job_sort_column == column:self.job_sort_descending = not self.job_sort_descending
        else:self.job_sort_column = column; self.job_sort_descending = False
        self._apply_job_sort()

    def _apply_job_sort(self):
        column = self.job_sort_column
        if not column:return
        selection = self.jobs_tree.selection(); ordered = ordered_tree_items(self.jobs_tree.get_children(""), lambda iid: technician_job_sort_value(self.job_rows[iid], column), self.job_sort_descending)
        for index, iid in enumerate(ordered):self.jobs_tree.move(iid, "", index)
        for name, heading in zip(self.JOB_COLUMNS, self.JOB_HEADINGS):
            marker = (" ▼" if self.job_sort_descending else " ▲") if name == column else ""; self.jobs_tree.heading(name, text=heading + marker)
        if selection and self.jobs_tree.exists(selection[0]):self.jobs_tree.selection_set(selection[0]); self.jobs_tree.see(selection[0])

    def refresh(self):
        try:summary = self.controller.summary(); jobs = self.controller.jobs(self.job_view.get()); payments = self.controller.payments(); activity = self.controller.activity()
        except Exception as exc:messagebox.showerror("Technician Finances", str(exc), parent=self); return
        for key, variable in self.summary_vars.items():
            value = summary.get(key, 0); variable.set(format_cents(value) if key.endswith("_cents") else str(value))
        self.ledger_tree.delete(*self.ledger_tree.get_children());self.activity_rows.clear()
        for item in activity:
            iid=f"activity-{item['source_record_type']}-{item['source_record_id']}"; self.activity_rows[iid]=item; reference=item.get("payment_reference") or item.get("status") or ""
            self.ledger_tree.insert("","end",iid=iid,values=(format_display_date(item.get("activity_date")),item.get("activity_type") or "",item.get("description") or "",item.get("external_job_id") or "",format_cents(item["amount_owed_cents"]) if item["amount_owed_cents"] else "",format_cents(item["payment_cents"]) if item["payment_cents"] else "",format_cents(item["running_balance_cents"]),reference))
        jobs = search_technician_jobs(jobs, self.job_search.get()); selected = self.jobs_tree.selection(); self.jobs_tree.delete(*self.jobs_tree.get_children()); self.job_rows.clear()
        for job in jobs:
            iid = f"job-{job['job_id']}"; self.job_rows[iid] = job; visible = technician_job_visible_values(job); self.jobs_tree.insert("", "end", iid=iid, values=[visible[column] for column in self.JOB_COLUMNS])
        if self.job_sort_column:self._apply_job_sort()
        if selected and self.jobs_tree.exists(selected[0]):self.jobs_tree.selection_set(selected[0]); self.jobs_tree.see(selected[0])
        selected_payment = self._selected_payment(); selected_payment_id = selected_payment.get("technician_payment_id") if selected_payment else None
        self.payments_tree.delete(*self.payments_tree.get_children()); self.payment_rows.clear()
        for payment in payments:
            pid = payment["technician_payment_id"]; iid = f"payment-{pid}"; self.payment_rows[iid] = payment; amount = payment.get("actual_amount_cents") if payment.get("actual_amount_cents") is not None else payment["payment_amount_cents"]
            self.payments_tree.insert("", "end", iid=iid, text=f"Payment #{pid}", open=False, values=(format_display_date(payment.get("payment_date")), payment.get("payment_method") or "", payment.get("payment_reference") or "", payment["payment_status"], format_cents(amount), "", "", ""))
            for index, item in enumerate(payment["jobs"]):
                label = item.get("external_job_id") or item.get("job_name") or "Adjustment"; other = (item["amount_applied_cents"] - item["base_pay_cents"] - item["travel_pay_cents"] if item["base_pay_cents"] is not None else item["amount_applied_cents"])
                self.payments_tree.insert(iid, "end", iid=f"{iid}-item-{index}", text=label, values=("", "", "", item["entry_type"], format_cents(item["amount_applied_cents"]), self._money(item["base_pay_cents"]), self._money(item["travel_pay_cents"]), format_cents(other)))
        selected_iid = f"payment-{selected_payment_id}" if selected_payment_id is not None else None
        if selected_iid and self.payments_tree.exists(selected_iid):self.payments_tree.selection_set(selected_iid); self.payments_tree.focus(selected_iid); self.payments_tree.see(selected_iid)
        else:self.payments_tree.selection_remove(self.payments_tree.selection())
        self._update_payment_email_action()
        if jobs:job_message = f"{len(jobs)} job(s)"
        elif self.job_search.get().strip():job_message = "No jobs match the current search"
        else:job_message = "No jobs found"
        self.status.set(f"{job_message}; {len(payments)} payment record(s).")
