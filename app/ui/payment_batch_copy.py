"""Column-aware AP Number and Job # copy behavior for Matterport payment batches."""

import tkinter as tk

from app.ui.payment_batch_review import (
    PaymentBatchDetail as ReviewPaymentBatchDetail,
    PaymentBatchManager as ReviewPaymentBatchManager,
)


class PaymentBatchDetail(ReviewPaymentBatchDetail):
    """Keep AP Number and Job # consistent and copyable in both batch trees."""

    def __init__(self, *args, **kwargs):
        self.context_copy_value = None
        self.context_copy_label = None
        super().__init__(*args, **kwargs)

        # Final Exceptions should use the same first two columns, labels, and order
        # as the Payment Items tree above it.
        self.financial_exceptions.configure(
            columns=("ap_number", "job_number", "customer", "amount", "category", "problem", "action")
        )
        specs = (
            ("ap_number", "AP Number", 125),
            ("job_number", "Job #", 110),
            ("customer", "Customer / Project", 150),
            ("amount", "Gross Amount", 90),
            ("category", "Category", 135),
            ("problem", "Specific Problem", 310),
            ("action", "Corrective Action", 150),
        )
        for key, heading, width in specs:
            self.financial_exceptions.heading(key, text=heading)
            self.financial_exceptions.column(
                key, width=width, anchor="e" if key == "amount" else "w"
            )

        self.copy_value_menu = tk.Menu(self, tearoff=False)
        self.copy_value_menu.add_command(label="Copy", command=self.copy_context_value)
        self.financial_exceptions.bind("<Button-3>", self.show_financial_exception_copy_menu, add="+")
        if self.tk.call("tk", "windowingsystem") == "aqua":
            self.financial_exceptions.bind("<Button-2>", self.show_financial_exception_copy_menu, add="+")

        # Re-render once so existing exception rows use AP Number, Job # order.
        self._render_technician_summary()

    def show_payment_item_menu(self, event) -> str:
        """Copy AP Number from column 1 or Job # from column 2."""
        region = self.items.identify_region(event.x, event.y)
        iid = self.items.identify_row(event.y)
        column = self.items.identify_column(event.x)
        if region != "cell" or not iid or column not in {"#1", "#2"}:
            return "break"

        values = self.items.item(iid, "values")
        index = 0 if column == "#1" else 1
        value = str(values[index] if len(values) > index else "").strip()
        if not value or value == "—":
            return "break"

        self.items.selection_set(iid)
        self.items.focus(iid)
        self.context_copy_value = value
        self.context_copy_label = "AP Number" if column == "#1" else "Job #"
        self.copy_value_menu.entryconfigure(0, label=f"Copy {self.context_copy_label}")
        try:
            self.copy_value_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.copy_value_menu.grab_release()
        return "break"

    def show_financial_exception_copy_menu(self, event) -> str:
        """Copy AP Number or Job # from the corresponding Final Exceptions cell."""
        region = self.financial_exceptions.identify_region(event.x, event.y)
        iid = self.financial_exceptions.identify_row(event.y)
        column = self.financial_exceptions.identify_column(event.x)
        if region != "cell" or not iid or column not in {"#1", "#2"}:
            return "break"

        values = self.financial_exceptions.item(iid, "values")
        index = 0 if column == "#1" else 1
        value = str(values[index] if len(values) > index else "").strip()
        if not value or value == "—":
            return "break"

        self.financial_exceptions.selection_set(iid)
        self.financial_exceptions.focus(iid)
        self.context_copy_value = value
        self.context_copy_label = "AP Number" if column == "#1" else "Job #"
        self.copy_value_menu.entryconfigure(0, label=f"Copy {self.context_copy_label}")
        try:
            self.copy_value_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.copy_value_menu.grab_release()
        return "break"

    def copy_context_value(self) -> None:
        value = self.context_copy_value
        if not value:
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update_idletasks()
        self.next_step_var.set(f"Copied {self.context_copy_label}: {value}")

    def _render_technician_summary(self) -> None:
        """Render base financial exceptions, then put AP Number before Job #."""
        super()._render_technician_summary()
        tree = getattr(self, "financial_exceptions", None)
        if tree is None:
            return
        for iid in tree.get_children():
            values = list(tree.item(iid, "values"))
            if len(values) >= 2:
                # Base renderer emits Job #, AP Number. The visible standard is AP, Job.
                values[0], values[1] = values[1], values[0]
                tree.item(iid, values=values)


class PaymentBatchManager(ReviewPaymentBatchManager):
    """Open the payment batch detail with column-aware copy behavior."""

    def _open_detail(self, batch_id: int | None) -> None:
        if (
            batch_id is not None
            and batch_id in self.detail_windows
            and self.detail_windows[batch_id].winfo_exists()
        ):
            self.detail_windows[batch_id].lift()
            self.detail_windows[batch_id].focus_force()
            return
        detail = PaymentBatchDetail(
            self,
            self.service,
            self.session,
            batch_id,
            self.refresh,
        )
        if batch_id is not None:
            self.detail_windows[batch_id] = detail
