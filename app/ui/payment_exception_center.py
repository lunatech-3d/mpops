"""Operator workspace for resolving payment exceptions through PaymentService."""

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from app.services.payment_service import PaymentService
from app.ui.payment_helpers import format_cents, visible_exception_tabs


class PaymentExceptionCenter(tk.Toplevel):
    """Tabbed exception queue; this window never accesses the database directly."""

    def __init__(self, parent, service: PaymentService, session, batch_id: int,
                 on_changed: Callable[[], None]):
        super().__init__(parent)
        self.service, self.session, self.batch_id = service, session, batch_id
        self.on_changed = on_changed
        self.can_modify = session.role in {"admin", "operator"}
        self.title("Resolve Payment Exceptions")
        self.geometry("1100x680"); self.minsize(850, 520)
        self.message = ttk.Label(self, text="", font=("TkDefaultFont", 12, "bold"))
        self.message.pack(anchor="w", padx=12, pady=(12, 4))
        self.notebook = ttk.Notebook(self); self.notebook.pack(fill="both", expand=True, padx=12, pady=8)
        ttk.Button(self, text="Close", command=self.destroy).pack(anchor="e", padx=12, pady=(0, 12))
        self.refresh()

    def refresh(self) -> None:
        groups = self.service.list_payment_exceptions(self.batch_id)
        for tab in self.notebook.tabs(): self.notebook.forget(tab)
        tabs = visible_exception_tabs(groups)
        self.message.configure(text="" if tabs else "No unresolved exceptions remain.")
        for name in tabs:
            self._build_tab(name, groups[name])

    def _build_tab(self, name, records) -> None:
        tab = ttk.Frame(self.notebook, padding=8); self.notebook.add(tab, text=f"{name} ({len(records)})")
        pane = ttk.Panedwindow(tab, orient="horizontal"); pane.pack(fill="both", expand=True)
        left, right = ttk.Frame(pane), ttk.Frame(pane, padding=(12, 0)); pane.add(left, weight=2); pane.add(right, weight=3)
        tree = ttk.Treeview(left, columns=("document", "description", "amount", "status"), show="headings")
        for key, title, width in (("document", "Document Number", 130), ("description", "Description", 190),
                                  ("amount", "Amount", 90), ("status", "Current Status", 110)):
            tree.heading(key, text=title); tree.column(key, width=width)
        by_id = {}
        for item in records:
            iid = str(item["payment_item_id"]); by_id[iid] = item
            tree.insert("", "end", iid=iid, values=(item.get("document_number") or "", item.get("description_raw") or "",
                                                       format_cents(item.get("amount_received_cents")), item.get("match_status") or ""))
        tree.pack(fill="both", expand=True)
        details = tk.Text(right, height=12, wrap="word", state="disabled"); details.pack(fill="x")
        candidates = ttk.Treeview(right, columns=("job", "customer", "address", "date", "tech", "confidence"), show="headings", height=9)
        for key, title in zip(("job", "customer", "address", "date", "tech", "confidence"),
                              ("Job Number", "Customer", "Property Address", "Capture Date", "Technician", "Confidence")):
            candidates.heading(key, text=title); candidates.column(key, width=105)
        candidate_ids = {}
        if name in {"Missing Jobs", "Ambiguous Matches"}: candidates.pack(fill="both", expand=True, pady=8)
        actions = ttk.Frame(right); actions.pack(fill="x", pady=6)

        def selected():
            selection = tree.selection()
            return by_id.get(selection[0]) if selection else None
        def load(_event=None):
            item = selected()
            if not item: return
            details.configure(state="normal"); details.delete("1.0", "end")
            details.insert("1.0", "\n".join((f"Document Number: {item.get('document_number') or ''}",
                f"Description: {item.get('description_raw') or ''}", f"Amount: {format_cents(item.get('amount_received_cents'))}",
                f"Import Date: {item.get('created_at') or ''}", f"Match Notes: {item.get('match_notes') or ''}",
                f"Existing Suggestions: {'See candidates below' if name in {'Missing Jobs', 'Ambiguous Matches'} else 'None'}")))
            details.configure(state="disabled")
            if name in {"Missing Jobs", "Ambiguous Matches"}: load_candidates(item)
        def load_candidates(item=None):
            item = item or selected()
            if not item: return
            candidates.delete(*candidates.get_children()); candidate_ids.clear()
            for candidate in self.service.list_exception_candidates(item["payment_item_id"]):
                iid = str(candidate["job_id"]); candidate_ids[iid] = candidate["job_id"]
                candidates.insert("", "end", iid=iid, values=(candidate["job_number"], candidate.get("customer") or "",
                    candidate.get("property_address") or "", candidate.get("capture_date") or "", candidate.get("technician") or "",
                    f"{candidate['confidence']}%"))
        def notes(): return simpledialog.askstring("Resolution Notes", "Optional notes (maximum 500 characters):", parent=self)
        def act(callback):
            item = selected()
            if not item: messagebox.showwarning(self.title(), "Select a payment item first.", parent=self); return
            try: callback(item)
            except Exception as exc: messagebox.showerror(self.title(), str(exc), parent=self); return
            self.on_changed(); self.refresh()
        state = "normal" if self.can_modify else "disabled"
        if name in {"Missing Jobs", "Ambiguous Matches"}:
            def assign(item):
                choice = candidates.selection()
                if not choice: raise ValueError("Select a suggested job first, or use Search Jobs.")
                self.service.assign_payment_item_job(self.session, item["payment_item_id"], candidate_ids[choice[0]], notes())
            ttk.Button(actions, text="Assign Selected Job", command=lambda: act(assign), state=state).pack(side="left", padx=3)
            ttk.Button(actions, text="Search Jobs", command=lambda: load_candidates(), state=state).pack(side="left", padx=3)
            ttk.Button(actions, text="Refresh Suggestions", command=lambda: load_candidates()).pack(side="left", padx=3)
        elif name == "Amount Review":
            ttk.Button(actions, text="Accept Imported Amount", command=lambda: act(lambda i: self.service.accept_amount_difference(self.session, i["payment_item_id"], "imported", notes())), state=state).pack(side="left", padx=3)
            def accept_job(item):
                value = simpledialog.askinteger("Job Amount", "Matched Job Amount (cents):", parent=self, minvalue=0)
                if value is not None: self.service.accept_amount_difference(self.session, item["payment_item_id"], "job", notes(), value)
            ttk.Button(actions, text="Accept Job Amount", command=lambda: act(accept_job), state=state).pack(side="left", padx=3)
            ttk.Button(actions, text="Exclude Payment", command=lambda: act(lambda i: self.service.exclude_payment_item(self.session, i["payment_item_id"], notes(), "Amount difference")), state=state).pack(side="left", padx=3)
        elif name == "Excluded":
            ttk.Button(actions, text="Restore", command=lambda: act(lambda i: self.service.restore_payment_item(self.session, i["payment_item_id"], notes())), state=state).pack(side="left", padx=3)
            ttk.Button(actions, text="Edit Notes", command=lambda: act(lambda i: self.service.exclude_payment_item(self.session, i["payment_item_id"], notes(), "Operator decision")), state=state).pack(side="left", padx=3)
        tree.bind("<<TreeviewSelect>>", load)
        if records: tree.selection_set(str(records[0]["payment_item_id"])); load()
