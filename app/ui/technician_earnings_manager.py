"""Central technician earning review screen and testable controller."""
import tkinter as tk
from datetime import date, datetime
from tkinter import messagebox, ttk

from app.services.compensation_service import CompensationService
from app.ui.payment_helpers import format_cents
from app.ui.styles import PADDING


class TechnicianEarningsController:
    def __init__(self, service, session, payment_batch_id=None, technician_id=None):
        self.service, self.session = service, session
        self.prefilter = {"payment_batch_id": payment_batch_id, "technician_id": technician_id}

    @property
    def can_modify(self):
        return self.session.role in {"admin", "operator"}

    def load(self, **filters):
        return self.service.list_earnings_for_review(**{**self.prefilter, **filters})

    def batch_completeness(self):
        batch_id = self.prefilter["payment_batch_id"]
        return (self.service.get_payment_batch_ledger_completeness(batch_id)
                if batch_id is not None else None)

    def void(self, earning_id, reason):
        return self.service.void_technician_earning(self.session, earning_id, reason)

    def grouped_totals(self, rows):
        result = {}
        for row in rows:
            item = result.setdefault(row["tech_id"], {
                "technician": row["technician_name"], "count": 0,
                "net_earning_cents": 0,
            })
            item["count"] += 1
            item["net_earning_cents"] += row["net_earning_cents"]
        return result


class TechnicianEarningsManager(ttk.Frame):
    COLUMNS = (
        "technician_name", "external_job_id", "job_address", "job_date",
        "market_name", "document_number", "revenue_basis_cents",
        "calculated_amount_cents", "adjustment_amount_cents", "net_earning_cents",
        "payment_state", "valid_paid_cents", "balance_due_cents", "payment_display",
    )
    HEADINGS = {
        "technician_name": "Technician", "external_job_id": "Job",
        "job_address": "Address", "job_date": "Job Date", "market_name": "Market",
        "document_number": "Document Number", "revenue_basis_cents": "Revenue Basis",
        "calculated_amount_cents": "Calculated", "adjustment_amount_cents": "Adjustment",
        "net_earning_cents": "Net Earning",
        "payment_state": "Payment State", "valid_paid_cents": "Paid Amount",
        "balance_due_cents": "Balance Due", "payment_display": "Payment",
    }
    COLUMN_WIDTHS = {
        "technician_name": 165, "external_job_id": 145, "job_address": 240,
        "job_date": 90, "market_name": 90, "document_number": 155,
        "revenue_basis_cents": 105, "calculated_amount_cents": 95,
        "adjustment_amount_cents": 95, "net_earning_cents": 105,
        "payment_state": 105, "valid_paid_cents": 100, "balance_due_cents": 100,
        "payment_display": 115,
    }
    CURRENCY_COLUMNS = {
        "revenue_basis_cents", "calculated_amount_cents", "adjustment_amount_cents",
        "net_earning_cents", "valid_paid_cents", "balance_due_cents",
    }

    def __init__(self, parent, auth, session, payment_batch_id=None, technician_id=None):
        super().__init__(parent, padding=PADDING)
        self.controller = TechnicianEarningsController(
            CompensationService(auth), session, payment_batch_id, technician_id)
        self.rows = []
        self.sort_column = "technician_name"
        self.sort_descending = False

        top = ttk.Frame(self)
        top.pack(fill="x")
        title = ttk.Frame(top)
        title.pack(side="left")
        ttk.Label(title, text="Technician Earnings Review", style="Header.TLabel").pack(anchor="w")
        if payment_batch_id is not None:
            ttk.Label(
                title,
                text=f"Earnings from Matterport Payment Batch #{payment_batch_id}",
            ).pack(anchor="w")

        self.status = tk.StringVar(value="Ready to Pay")
        status_box = ttk.Combobox(
            top, textvariable=self.status,
            values=("All", "Ready to Pay", "Partially Paid", "Paid", "Voided"),
            state="readonly", width=14,
        )
        status_box.pack(side="left", padx=12)
        status_box.bind("<<ComboboxSelected>>", self.refresh)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left", padx=6)

        self.summary = tk.StringVar()
        ttk.Label(self, textvariable=self.summary, style="Status.TLabel").pack(anchor="w", pady=(8, 0))
        self.ledger_warning = tk.StringVar()
        ttk.Label(
            self, textvariable=self.ledger_warning, foreground="#9b1c1c",
            font=("TkDefaultFont", 10, "bold"), justify="left",
        ).pack(anchor="w", pady=(4, 0))

        area = ttk.Frame(self)
        area.pack(fill="both", expand=True, pady=8)
        self.tree = ttk.Treeview(
            area, columns=self.COLUMNS, show="headings", selectmode="extended")
        for column in self.COLUMNS:
            self.tree.heading(
                column, text=self.HEADINGS[column],
                command=lambda selected=column: self.sort_by(selected),
            )
            self.tree.column(
                column, width=self.COLUMN_WIDTHS[column],
                anchor="e" if column in self.CURRENCY_COLUMNS else "w",
            )
        sx = ttk.Scrollbar(area, orient="horizontal", command=self.tree.xview)
        sy = ttk.Scrollbar(area, orient="vertical", command=self.tree.yview)
        self.tree.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.payment_button = ttk.Button(
            bar, text="Record Technician Payment", command=self.record_payment)
        self.payment_button.pack(side="left")
        if not self.controller.can_modify:
            self.payment_button.configure(state="disabled")
        self.refresh()

    @staticmethod
    def _date_value(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if value:
            try:
                return date.fromisoformat(str(value)[:10])
            except ValueError:
                pass
        return None

    def _sort_value(self, row, column):
        value = row.get(column)
        if column in self.CURRENCY_COLUMNS:
            return value
        if column == "job_date":
            return self._date_value(value)
        return str(value).casefold() if value not in (None, "") else None

    def _sort_rows(self):
        """Sort loaded dictionaries while consistently leaving blank values last."""
        column = self.sort_column
        populated = [row for row in self.rows if self._sort_value(row, column) is not None]
        blank = [row for row in self.rows if self._sort_value(row, column) is None]

        # The useful initial order is Technician, then chronological Job Date.
        if column == "technician_name" and not self.sort_descending:
            populated.sort(key=lambda row: self._date_value(row.get("job_date")) or date.max)
        populated.sort(
            key=lambda row: self._sort_value(row, column), reverse=self.sort_descending)
        self.rows = populated + blank

    def _render_rows(self, selected_ids=()):
        self.tree.delete(*self.tree.get_children())
        available = set()
        for row in self.rows:
            earning_id = str(row["technician_earning_id"])
            available.add(earning_id)
            payment_count = row.get("valid_payment_count") or 0
            row["payment_display"] = (f"{payment_count} payments" if payment_count > 1 else
                (f"Payment #{row['latest_paid_payment_id']}" if payment_count else ""))
            values = [
                format_cents(row.get(column)) if column in self.CURRENCY_COLUMNS
                else row.get(column) or ""
                for column in self.COLUMNS
            ]
            self.tree.insert(
                "", "end", iid=earning_id, values=values,
                tags=(row["entry_type"].replace(" ", "_"), row["payment_state"]),
            )
        retained = [earning_id for earning_id in selected_ids if earning_id in available]
        if retained:
            self.tree.selection_set(retained)
            self.tree.see(retained[0])
        indicator = " ▼" if self.sort_descending else " ▲"
        for column in self.COLUMNS:
            label = self.HEADINGS[column] + (indicator if column == self.sort_column else "")
            self.tree.heading(column, text=label)

    def sort_by(self, column):
        selected_ids = self.tree.selection()
        if column == self.sort_column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column = column
            self.sort_descending = False
        self._sort_rows()
        self._render_rows(selected_ids)

    def refresh(self, _event=None):
        selected_ids = self.tree.selection()
        self.rows = list(self.controller.load(status=self.status.get()))
        self._sort_rows()
        self._render_rows(selected_ids)
        technician_count = len({row["tech_id"] for row in self.rows})
        net = sum(row["net_earning_cents"] for row in self.rows)
        paid = sum(row["valid_paid_cents"] for row in self.rows)
        balance = sum(row["balance_due_cents"] for row in self.rows)
        qualifier = " " + self.status.get().lower() if self.status.get() != "All" else ""
        self.summary.set(
            f"{len(self.rows)}{qualifier} earnings across {technician_count} technicians\n"
            f"Net earnings: {format_cents(net)}    Paid: {format_cents(paid)}    "
            f"Remaining: {format_cents(balance)}")
        completeness = self.controller.batch_completeness()
        if completeness and completeness["is_incomplete"]:
            details = [
                "Earnings ledger is incomplete for this Matterport payment.",
                f"Eligible matched jobs: {completeness['preview_entry_count']}",
                f"Permanent earning records: {completeness['permanent_earning_count']}",
                f"Missing earning records: {completeness['missing_earning_count']}", "",
            ]
            details.extend(
                f"Job {item['external_job_id'] or item['job_id']} (ID {item['job_id']}), "
                f"document {item['document_number']}, {item['technician_name']}, "
                f"gross {format_cents(item['gross_amount_cents'])}: {item['reason']}"
                for item in completeness["missing"])
            self.ledger_warning.set("\n".join(details))
        else:
            self.ledger_warning.set("")

    def record_payment(self):
        ids = [int(earning_id) for earning_id in self.tree.selection()]
        if not ids:
            messagebox.showinfo(
                "Record Technician Payment", "Select Ready to Pay earnings first.", parent=self)
            return
        selected=[row for row in self.rows if row["technician_earning_id"] in ids]
        if len({row["tech_id"] for row in selected}) != 1:
            messagebox.showerror("Record Technician Payment","Select earnings for one technician only.",parent=self)
            return
        if any(row["payment_state"] not in {"Ready to Pay", "Partially Paid"}
               or row["balance_due_cents"] <= 0 for row in selected):
            messagebox.showerror("Record Technician Payment","Only Ready to Pay or Partially Paid earnings with a remaining balance may be recorded.",parent=self)
            return
        from app.ui.technician_payment_form import TechnicianPaymentForm
        dialog=tk.Toplevel(self);dialog.title("Record Technician Payment");dialog.geometry("1100x720")
        form=TechnicianPaymentForm(dialog,self.controller.service.auth,self.controller.session,
                                   technician_id=selected[0]["tech_id"],earning_ids=ids,
                                   on_saved=lambda _payment:(self.refresh(),dialog.destroy()))
        form.pack(fill="both",expand=True)
