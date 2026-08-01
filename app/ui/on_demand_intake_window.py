"""Review-before-commit interface for Matterport On-Demand job intake."""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox, ttk

from app.date_utils import format_display_datetime
from app.services.on_demand_intake_service import OnDemandIntakeService, combine_sources
from app.ui.scrollable_frame import ScrollableFrame
from app.ui.styles import PADDING


FIELDS = (
    ("job_id", "Job ID"), ("address", "Raw Address"), ("address_1", "Address 1"),
    ("suite", "Suite / Address 2"), ("city", "City"), ("state", "State"),
    ("postal_code", "Postal Code"), ("country", "Country"),
    ("scheduled_start_at", "Scheduled Start (ISO, with zone)"),
    ("estimated_minutes", "Estimated Duration (minutes)"), ("expected_payout", "Expected Payout"),
    ("contact_name", "Contact Name"), ("contact_email", "Contact Email"),
    ("contact_phone", "Contact Phone"), ("contact_onsite", "Contact Onsite"),
    ("property_type", "Property Type"), ("property_size", "Property Size"),
    ("space_name", "Space Name"), ("space_property_size", "Space Property Size"),
    ("capture_type", "Capture Type"), ("job_link", "Job Link"),
)


def _phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if len(digits) == 10 else str(value or "")


def _duration(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    hours, remainder = divmod(minutes, 60)
    return " ".join(part for part in (f"{hours} hours" if hours else "", f"{remainder} minutes" if remainder else "") if part)


def calendar_details(data):
    address = "\n".join(filter(None, (data.get("address_1") or data.get("address"),
        " ".join(filter(None, (", ".join(filter(None, (data.get("city"), data.get("state")))), data.get("postal_code")))))))
    try:
        payout = f"${float(str(data.get('expected_payout')).replace(',', '').replace('$', '')):,.2f}"
    except (TypeError, ValueError):
        payout = str(data.get("expected_payout") or "")
    property_size = str(data.get("property_size") or "")
    numeric_property_size = re.sub(r"[^\d.]", "", property_size)
    try:
        size = f"{float(numeric_property_size):,.0f} sq ft" if numeric_property_size else ""
    except ValueError:
        size = property_size
    return f"""Matterport On-Demand Capture

Job ID: {data.get('job_id') or ''}
Job Link: {data.get('job_link') or ''}

Address:
{address}

Scheduled:
{format_display_datetime(data.get('scheduled_start_at'))}

Estimated Duration:
{_duration(data.get('estimated_minutes'))}

Property Type:
{data.get('property_type') or ''}

Property Size:
{size}

Capture Type:
{data.get('capture_type') or ''}

Contact:
{data.get('contact_name') or ''}
{data.get('contact_email') or ''}
{_phone(data.get('contact_phone'))}
Will be onsite: {data.get('contact_onsite') or ''}

Site Instructions:
{data.get('site_info') or ''}

Expected Payout:
{payout}"""


class OnDemandIntakeWindow(tk.Toplevel):
    def __init__(self, parent, auth, session, on_imported=None):
        super().__init__(parent)
        self.service, self.session, self.on_imported = OnDemandIntakeService(auth), session, on_imported
        self.parsed = None
        self.title("On-Demand Job Intake"); self.geometry("1120x900"); self.minsize(900, 700)
        self.transient(parent)
        outer = ttk.Frame(self, padding=PADDING); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="On-Demand Job Intake", style="Header.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Select the actual technician, paste both sources, parse them, then review every value.").pack(anchor="w", pady=(2, 8))

        technician = ttk.Frame(outer); technician.pack(fill="x", pady=(0, 8))
        ttk.Label(technician, text="Technician *").pack(side="left", padx=(0, 5))
        technicians = self.service.list_active_technicians()
        self.tech_by_name = {" ".join(filter(None, (r.get("first_name"), r.get("last_name")))): int(r["tech_id"]) for r in technicians}
        self.tech_var = tk.StringVar()
        ttk.Combobox(technician, textvariable=self.tech_var, values=list(self.tech_by_name),
                     state="readonly", width=35).pack(side="left")

        sources = ttk.Panedwindow(outer, orient="horizontal"); sources.pack(fill="x")
        self.email_text = self._source_box(sources, "A. Matterport Confirmation Email")
        self.notes_text = self._source_box(sources, "B. Skedulo Notes")
        actions = ttk.Frame(outer); actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Parse", command=self.parse).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Clear", command=self.clear).pack(side="left")
        self.status = tk.StringVar(value="Nothing has been saved.")
        ttk.Label(actions, textvariable=self.status, style="Status.TLabel").pack(side="left", padx=12)
        ttk.Button(actions, text="Close", command=self.destroy).pack(side="right")

        self.preview = ttk.LabelFrame(outer, text="C. Combined Preview", padding=8)
        scroll = ScrollableFrame(self.preview); scroll.pack(fill="both", expand=True)
        form = scroll.content
        self.vars = {key: tk.StringVar() for key, _ in FIELDS}
        for index, (key, label) in enumerate(FIELDS):
            row, column = divmod(index, 2)
            base = column * 2
            ttk.Label(form, text=label).grid(row=row, column=base, sticky="w", padx=(0, 5), pady=2)
            ttk.Entry(form, textvariable=self.vars[key], width=38).grid(row=row, column=base + 1, sticky="ew", padx=(0, 14), pady=2)
        row = (len(FIELDS) + 1) // 2
        ttk.Label(form, text="Site Instructions").grid(row=row, column=0, sticky="nw", pady=2)
        self.site_text = tk.Text(form, height=4, wrap="word"); self.site_text.grid(row=row, column=1, columnspan=3, sticky="ew", pady=2)
        self.warning_var = tk.StringVar()
        ttk.Label(form, textvariable=self.warning_var, foreground="#9b5c00", wraplength=850).grid(row=row + 1, column=0, columnspan=4, sticky="w", pady=5)
        for col in (1, 3): form.columnconfigure(col, weight=1)
        final = ttk.Frame(self.preview); final.pack(fill="x", pady=(8, 0))
        ttk.Button(final, text="Import Job", command=self.import_job).pack(side="left", padx=(0, 6))
        ttk.Button(final, text="Copy Calendar Details", command=self.copy_calendar).pack(side="left")

    @staticmethod
    def _source_box(parent, title):
        frame = ttk.LabelFrame(parent, text=title, padding=6); parent.add(frame, weight=1)
        editor = tk.Text(frame, height=12, wrap="word", undo=True); editor.pack(fill="both", expand=True)
        return editor

    def clear(self):
        self.email_text.delete("1.0", "end"); self.notes_text.delete("1.0", "end")
        self.preview.pack_forget(); self.parsed = None; self.status.set("Cleared. Nothing has been saved.")

    def parse(self):
        try:
            self.parsed = combine_sources(self.email_text.get("1.0", "end-1c"), self.notes_text.get("1.0", "end-1c"))
        except ValueError as exc:
            messagebox.showerror("On-Demand Job Intake", str(exc), parent=self); return
        for key, variable in self.vars.items(): variable.set(self.parsed.get(key) or "")
        self.site_text.delete("1.0", "end"); self.site_text.insert("1.0", self.parsed.get("site_info") or "")
        existing = self.service.jobs.get_job_by_external_id(self.parsed["job_id"]) if self.parsed.get("job_id") else None
        messages = list(self.parsed["warnings"])
        if existing: messages.append(f"Job already exists (MPOPS Job {existing['job_id']}); importing will update only the reviewed source fields.")
        for key, label in (("contact_name", "contact name"), ("contact_phone", "contact phone"), ("job_link", "job link")):
            if not self.parsed.get(key): messages.append(f"Missing {label}.")
        self.warning_var.set("  ".join(messages)); self.status.set("Parsed successfully. Review before importing.")
        self.preview.pack(fill="both", expand=True)

    def _data(self):
        data = dict(self.parsed or {})
        data.update({key: variable.get().strip() or None for key, variable in self.vars.items()})
        data["site_info"] = self.site_text.get("1.0", "end-1c").strip() or None
        return data

    def import_job(self):
        data, tech_id = self._data(), self.tech_by_name.get(self.tech_var.get())
        existing = self.service.jobs.get_job_by_external_id(data.get("job_id")) if data.get("job_id") else None
        if existing:
            assignment = self.service.assignments.get_active_primary(int(existing["job_id"]))
            if assignment and int(assignment["tech_id"]) != tech_id:
                name = " ".join(filter(None, (assignment.get("first_name"), assignment.get("last_name"))))
                if not messagebox.askyesno("Change primary technician?", f"{name} is currently primary. Replace that assignment and preserve it in history?", parent=self): return
            if not messagebox.askyesno("Update existing Job?", "This Job ID already exists. Update only the reviewed On-Demand source fields?", parent=self): return
        try:
            job_id, created = self.service.import_job(self.session, data,
                self.email_text.get("1.0", "end-1c"), self.notes_text.get("1.0", "end-1c"), tech_id)
        except (ValueError, LookupError) as exc:
            messagebox.showerror("On-Demand Job Intake", str(exc), parent=self); return
        self.status.set(f"Job {job_id} {'created' if created else 'updated'} successfully.")
        messagebox.showinfo("On-Demand Job Intake", self.status.get(), parent=self)
        if self.on_imported: self.on_imported()

    def copy_calendar(self):
        if not self.parsed:
            messagebox.showwarning("On-Demand Job Intake", "Parse both sources first.", parent=self); return
        text = calendar_details(self._data())
        self.clipboard_clear(); self.clipboard_append(text); self.update()
        self.status.set("Calendar details copied to the clipboard.")


def open_on_demand_intake(parent, auth, session, on_imported=None):
    return OnDemandIntakeWindow(parent, auth, session, on_imported)
