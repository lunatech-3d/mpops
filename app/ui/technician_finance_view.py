"""Technician-specific operational jobs and finance history view."""

import tkinter as tk
from tkinter import messagebox, ttk

from app.services.technician_finance_service import TechnicianFinanceService
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


def technician_job_visible_values(job):
    """Return the values displayed by the Technician Jobs tree."""
    job_date = (job.get("completed_at") or job.get("scheduled_start_at") or "")[:10]
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
        # ISO dates sort chronologically as text, including timestamps truncated
        # to their displayed calendar date.
        return raw, raw
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
               (compact_term and compact_term in
                str(value).casefold().replace("$", "").replace(",", ""))
               for value in values):
            matches.append(job)
    return matches


class TechnicianFinanceController:
    def __init__(self, service, technician_id):
        self.service, self.technician_id = service, technician_id

    def summary(self): return self.service.get_summary(self.technician_id)
    def jobs(self, view="All"): return self.service.list_jobs(self.technician_id, view)
    def payments(self): return self.service.list_payments(self.technician_id)


class TechnicianFinanceView(ttk.Frame):
    JOB_COLUMNS = JOB_COLUMNS
    JOB_HEADINGS = JOB_HEADINGS

    def __init__(self, parent, auth, technician_id, mode="all"):
        super().__init__(parent, padding=PADDING)
        self.controller = TechnicianFinanceController(TechnicianFinanceService(auth), technician_id)
        self.job_rows = {}; self.payment_rows = {}
        self.job_sort_column = None; self.job_sort_descending = False
        summary = ttk.Frame(self)
        if mode != "jobs": summary.pack(fill="x", pady=(0, 8))
        self.summary_vars = {}
        for column, (key, label) in enumerate((("upcoming_expected_cents", "Upcoming Expected"),
                ("completed_earnings_cents", "Completed Earnings"), ("balance_due_cents", "Approved Balance Due"),
                ("total_paid_cents", "Total Paid"), ("pending_approval_cents", "Pending Approval"),
                ("pending_direct_cents", "Pending Direct Items"))):
            box = ttk.LabelFrame(summary, text=label, padding=7); box.grid(row=0, column=column, sticky="nsew", padx=3)
            variable = tk.StringVar(value="—"); self.summary_vars[key] = variable
            ttk.Label(box, textvariable=variable, style="Header.TLabel").pack()
            summary.columnconfigure(column, weight=1)
        notebook = ttk.Notebook(self); notebook.pack(fill="both", expand=True)
        jobs_tab = ttk.Frame(notebook, padding=5); payments_tab = ttk.Frame(notebook, padding=5)
        if mode in {"all", "jobs"}: notebook.add(jobs_tab, text="Assigned Jobs")
        if mode in {"all", "finances"}: notebook.add(payments_tab, text="Complete Payment History")
        filters = ttk.Frame(jobs_tab); filters.pack(fill="x")
        ttk.Label(filters, text="Search:").pack(side="left")
        self.job_search = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.job_search, width=28)
        search.pack(side="left", padx=(6, 12))
        search.bind("<Return>", lambda _event: self.refresh())
        ttk.Label(filters, text="View:").pack(side="left")
        self.job_view = tk.StringVar(value="All")
        view = ttk.Combobox(filters, textvariable=self.job_view,
                            values=("All", "Upcoming", "Completed", "Cancelled", "Owed"),
                            state="readonly", width=12)
        view.pack(side="left", padx=6)
        view.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        ttk.Button(filters, text="Search", command=self.refresh).pack(side="left", padx=(6, 0))
        ttk.Button(filters, text="Clear", command=self.clear_job_search).pack(side="left", padx=6)
        ttk.Button(filters, text="Refresh", command=self.refresh).pack(side="left")
        self.jobs_tree = ttk.Treeview(jobs_tab, columns=self.JOB_COLUMNS, show="headings")
        for column, heading in zip(self.JOB_COLUMNS, self.JOB_HEADINGS):
            self.jobs_tree.heading(column, text=heading,
                                   command=lambda selected=column: self.sort_jobs_by(selected))
            self.jobs_tree.column(column, width=105, anchor="e" if column in {"earned","base","travel","paid","due"} else "w")
        self.jobs_tree.pack(fill="both", expand=True, pady=(7, 0))
        ttk.Label(payments_tab, text="Expand a payment to see every job and pay component it covered.").pack(anchor="w")
        self.payments_tree = ttk.Treeview(payments_tab,
            columns=("date","method","reference","status","amount","base","travel","other"), show="tree headings")
        self.payments_tree.heading("#0", text="Payment / Job")
        for column, heading in zip(self.payments_tree["columns"],
                ("Date","Method","Reference","Status","Amount Applied","Base Pay","Travel Pay","Other")):
            self.payments_tree.heading(column, text=heading); self.payments_tree.column(column, width=110)
        self.payments_tree.pack(fill="both", expand=True, pady=(7, 0))
        self.status = tk.StringVar(); ttk.Label(self, textvariable=self.status, style="Status.TLabel").pack(anchor="w", pady=(6, 0))
        self.mode = mode
        self.refresh()

    @staticmethod
    def _money(value):
        return "—" if value is None else format_cents(value)

    def clear_job_search(self):
        """Clear only the query, retaining the selected technician job view."""
        self.job_search.set("")
        self.refresh()

    def sort_jobs_by(self, column):
        """Select a job column or toggle the active column's direction."""
        if self.job_sort_column == column:
            self.job_sort_descending = not self.job_sort_descending
        else:
            self.job_sort_column = column
            self.job_sort_descending = False
        self._apply_job_sort()

    def _apply_job_sort(self):
        column = self.job_sort_column
        if not column:
            return
        selection = self.jobs_tree.selection()
        ordered = ordered_tree_items(
            self.jobs_tree.get_children(""),
            lambda iid: technician_job_sort_value(self.job_rows[iid], column),
            self.job_sort_descending,
        )
        for index, iid in enumerate(ordered):
            self.jobs_tree.move(iid, "", index)
        for name, heading in zip(self.JOB_COLUMNS, self.JOB_HEADINGS):
            marker = (" ▼" if self.job_sort_descending else " ▲") if name == column else ""
            self.jobs_tree.heading(name, text=heading + marker)
        if selection and self.jobs_tree.exists(selection[0]):
            self.jobs_tree.selection_set(selection[0]); self.jobs_tree.see(selection[0])

    def refresh(self):
        try:
            summary = self.controller.summary(); jobs = self.controller.jobs(self.job_view.get())
            payments = self.controller.payments()
        except Exception as exc:
            messagebox.showerror("Technician Finances", str(exc), parent=self); return
        for key, variable in self.summary_vars.items():
            value = summary.get(key, 0)
            variable.set(format_cents(value) if key.endswith("_cents") else str(value))
        jobs = search_technician_jobs(jobs, self.job_search.get())
        selected = self.jobs_tree.selection()
        self.jobs_tree.delete(*self.jobs_tree.get_children()); self.job_rows.clear()
        for job in jobs:
            iid = f"job-{job['job_id']}"; self.job_rows[iid] = job
            visible = technician_job_visible_values(job)
            self.jobs_tree.insert("", "end", iid=iid,
                                  values=[visible[column] for column in self.JOB_COLUMNS])
        if self.job_sort_column: self._apply_job_sort()
        if selected and self.jobs_tree.exists(selected[0]):
            self.jobs_tree.selection_set(selected[0]); self.jobs_tree.see(selected[0])
        self.payments_tree.delete(*self.payments_tree.get_children()); self.payment_rows.clear()
        for payment in payments:
            pid = payment["technician_payment_id"]; iid = f"payment-{pid}"; self.payment_rows[iid] = payment
            amount = payment.get("actual_amount_cents") if payment.get("actual_amount_cents") is not None else payment["payment_amount_cents"]
            self.payments_tree.insert("", "end", iid=iid, text=f"Payment #{pid}", open=False, values=(
                payment.get("payment_date") or "", payment.get("payment_method") or "", payment.get("payment_reference") or "",
                payment["payment_status"], format_cents(amount), "", "", ""))
            for index, item in enumerate(payment["jobs"]):
                label = item.get("external_job_id") or item.get("job_name") or "Adjustment"
                other = (item["amount_applied_cents"] - item["base_pay_cents"] - item["travel_pay_cents"]
                         if item["base_pay_cents"] is not None else item["amount_applied_cents"])
                self.payments_tree.insert(iid, "end", iid=f"{iid}-item-{index}", text=label,
                    values=("", "", "", item["entry_type"], format_cents(item["amount_applied_cents"]),
                            self._money(item["base_pay_cents"]), self._money(item["travel_pay_cents"]), format_cents(other)))
        if jobs:
            job_message = f"{len(jobs)} job(s)"
        elif self.job_search.get().strip():
            job_message = "No jobs match the current search"
        else:
            job_message = "No jobs found"
        self.status.set(f"{job_message}; {len(payments)} payment record(s).")
