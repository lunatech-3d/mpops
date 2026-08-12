"""Review-only technician payment email draft dialog."""

import tkinter as tk
import urllib.parse
import webbrowser
from tkinter import messagebox, ttk

from app.ui.styles import PADDING


class PaymentEmailDialog(tk.Toplevel):
    """Allow review/edit/copy or mail-client drafting; never send an email."""

    def __init__(self, parent, draft):
        super().__init__(parent)
        self.title(f"Payment Email Draft — Payment #{draft['payment_id']}")
        self.geometry("900x650")
        self.transient(parent.winfo_toplevel())
        frame=ttk.Frame(self,padding=PADDING);frame.pack(fill="both",expand=True)
        ttk.Label(frame,text="Review Payment Email",style="Header.TLabel").pack(anchor="w")
        ttk.Label(frame,text="This is a reviewable draft. Matterport Ops does not send email automatically.").pack(anchor="w",pady=(0,8))
        fields=ttk.Frame(frame);fields.pack(fill="x");fields.columnconfigure(1,weight=1)
        self.recipient=tk.StringVar(value=draft["recipient"]);self.subject=tk.StringVar(value=draft["subject"])
        for row,(label,var) in enumerate((("To",self.recipient),("Subject",self.subject))):
            ttk.Label(fields,text=label).grid(row=row,column=0,sticky="w",padx=(0,8),pady=3)
            ttk.Entry(fields,textvariable=var).grid(row=row,column=1,sticky="ew",pady=3)
        self.body=tk.Text(frame,wrap="none",font="TkFixedFont",undo=True)
        self.body.insert("1.0",draft["body"]);self.body.pack(fill="both",expand=True,pady=8)
        buttons=ttk.Frame(frame);buttons.pack(fill="x")
        for label,command in (("Copy Subject",lambda:self._copy(self.subject.get())),
                ("Copy Body",lambda:self._copy(self.body.get("1.0","end-1c"))),
                ("Copy Both",lambda:self._copy(f"Subject: {self.subject.get()}\n\n{self.body.get('1.0','end-1c')}")),
                ("Open Email Client",self.open_client)):
            ttk.Button(buttons,text=label,command=command).pack(side="left",padx=(0,6))
        ttk.Button(buttons,text="Close Without Sending",command=self.destroy).pack(side="right")

    def _copy(self,text):
        self.clipboard_clear();self.clipboard_append(text);self.update()

    def open_client(self):
        query=urllib.parse.urlencode({"subject":self.subject.get(),"body":self.body.get("1.0","end-1c")},quote_via=urllib.parse.quote)
        if not webbrowser.open(f"mailto:{urllib.parse.quote(self.recipient.get())}?{query}"):
            messagebox.showwarning("Email Client","No default email client could be opened. Use the copy buttons instead.",parent=self)


def generate_and_open_payment_email(parent, service, session, payment_id):
    try:
        draft=service.generate_payment_email_draft(session,payment_id)
    except Exception as exc:
        if (str(exc)=="No email address is recorded for this technician." and
                session and session.role=="admin" and
                messagebox.askyesno("Payment Email",f"{exc}\n\nOpen the Technician form to add it?",parent=parent)):
            from app.services.technician_service import TechnicianService
            from app.ui.technician_form import show_technician_form
            tech_service=TechnicianService(service.auth)
            detail=service.get_payment_detail(payment_id)
            technician=tech_service.get_technician(detail["tech_id"])
            submitted=show_technician_form(parent,technician,is_admin=True)
            if submitted:
                from app.ui.technician_form import changed_fields
                changes=changed_fields(technician,submitted)
                if changes:tech_service.update_technician(session,detail["tech_id"],changes)
                return generate_and_open_payment_email(parent,service,session,payment_id)
            return None
        messagebox.showerror("Payment Email",str(exc),parent=parent)
        return None
    return PaymentEmailDialog(parent,draft)
