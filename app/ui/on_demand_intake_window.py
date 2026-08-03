"""Review-before-commit interface for Matterport On-Demand job intake."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from app.services.on_demand_intake_service import (OnDemandIntakeService, calendar_details,
                                                   combine_sources)
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


class OnDemandIntakeWindow(tk.Toplevel):
    def __init__(self, parent, auth, session, on_imported=None):
        super().__init__(parent)
        self.service, self.session, self.on_imported = OnDemandIntakeService(auth), session, on_imported
        self.parsed = None
        self.technician_identity = None
        self.technician_payout = None
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
        tech_box = ttk.Combobox(technician, textvariable=self.tech_var, values=list(self.tech_by_name),
                                state="readonly", width=35)
        tech_box.pack(side="left")
        tech_box.bind("<<ComboboxSelected>>", self._refresh_calendar_info)
        self.tech_email_var = tk.StringVar(value="Select a technician to view email.")
        ttk.Label(technician, text="Email:").pack(side="left", padx=(18, 5))
        ttk.Label(technician, textvariable=self.tech_email_var, wraplength=430).pack(side="left")

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
        self.calendar_title_var = tk.StringVar(value="Matterport Capture")
        self.attendee_var = tk.StringVar(value="No technician selected")
        self.gross_payout_var = tk.StringVar(value="—")
        self.tech_payout_var = tk.StringVar(value="Unable to calculate")
        self.rule_var = tk.StringVar(value="—")
        info_row = row + 2
        for offset, (label, variable) in enumerate((
                ("Suggested Calendar Title", self.calendar_title_var),
                ("Technician Attendee", self.attendee_var),
                ("Matterport Expected Payout", self.gross_payout_var),
                ("Technician Expected Payout", self.tech_payout_var),
                ("Compensation Rule", self.rule_var))):
            ttk.Label(form, text=label).grid(row=info_row + offset, column=0, sticky="w", pady=2)
            ttk.Label(form, textvariable=variable, wraplength=700).grid(
                row=info_row + offset, column=1, columnspan=3, sticky="w", pady=2)
        for col in (1, 3): form.columnconfigure(col, weight=1)
        final = ttk.Frame(self.preview); final.pack(fill="x", pady=(8, 0))
        ttk.Button(final, text="Import Job", command=self.import_job).pack(side="left", padx=(0, 6))
        ttk.Button(final, text="Copy Calendar Details", command=self.copy_calendar).pack(side="left")
        self.vars["expected_payout"].trace_add("write", self._refresh_calendar_info)

    @staticmethod
    def _source_box(parent, title):
        frame = ttk.LabelFrame(parent, text=title, padding=6); parent.add(frame, weight=1)
        editor = tk.Text(frame, height=12, wrap="word", undo=True); editor.pack(fill="both", expand=True)
        return editor

    def clear(self):
        self.email_text.delete("1.0", "end"); self.notes_text.delete("1.0", "end")
        self.preview.pack_forget(); self.parsed = None; self.technician_payout = None
        self._refresh_calendar_info(); self.status.set("Cleared. Nothing has been saved.")

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
        self._refresh_calendar_info()

    def _data(self):
        data = dict(self.parsed or {})
        data.update({key: variable.get().strip() or None for key, variable in self.vars.items()})
        data["site_info"] = self.site_text.get("1.0", "end-1c").strip() or None
        return data

    @staticmethod
    def _currency(value):
        try:
            return f"${float(str(value).replace(',', '').replace('$', '')):,.2f}"
        except (TypeError, ValueError):
            return "Invalid or blank"

    def _refresh_calendar_info(self, *_args):
        """Refresh attendee and compensation display after any relevant UI change."""
        tech_id = self.tech_by_name.get(self.tech_var.get())
        self.technician_identity = None
        self.technician_payout = None
        if tech_id:
            try:
                self.technician_identity = self.service.technician_calendar_identity(tech_id)
            except (ValueError, LookupError) as exc:
                self.tech_email_var.set(str(exc))
                self.attendee_var.set("Unable to resolve technician")
            else:
                identity = self.technician_identity
                if identity.get("email"):
                    self.tech_email_var.set(identity["email"])
                    self.attendee_var.set(f"{identity['name']} <{identity['email']}>")
                else:
                    warning = "No email address is stored for this technician."
                    self.tech_email_var.set(warning)
                    self.attendee_var.set(f"{identity['name']} — No email on file")
        else:
            self.tech_email_var.set("Select a technician to view email.")
            self.attendee_var.set("No technician selected")

        if not hasattr(self, "vars"):
            return
        data = self._data()
        address = data.get("address_1") or data.get("address") or ""
        street = str(address).split(",", 1)[0].strip()
        self.calendar_title_var.set(f"Matterport Capture - {street}" if street else "Matterport Capture")
        self.gross_payout_var.set(self._currency(data.get("expected_payout")))
        self.tech_payout_var.set("Unable to calculate")
        self.rule_var.set("—")
        if not tech_id or not self.parsed:
            return
        try:
            self.technician_payout = self.service.expected_technician_payout(data, tech_id)
        except (ValueError, LookupError) as exc:
            self.rule_var.set(str(exc))
        except Exception as exc:
            # Rule configuration/data-integrity errors are reviewable intake issues,
            # not reasons for a Tk callback to terminate the application.
            self.rule_var.set(f"Unable to calculate: {exc}")
        else:
            payout = self.technician_payout
            self.tech_payout_var.set(f"${payout['amount_cents'] / 100:,.2f}")
            value = (f"{payout['rule_value'] / 100:g}%" if payout["rule_type"] == "Percentage"
                     else f"${payout['rule_value'] / 100:,.2f} flat")
            self.rule_var.set(f"{payout['rule_source']} — {value}")

    def import_job(self):
        data, tech_id = self._data(), self.tech_by_name.get(self.tech_var.get())
        existing = self.service.jobs.get_job_by_external_id(data.get("job_id")) if data.get("job_id") else None
        if existing:
            assignment = self.service.assignments.get_active_primary(int(existing["job_id"]))
            if assignment and int(assignment["tech_id"]) != tech_id:
                name = " ".join(filter(None, (assignment.get("first_name"), assignment.get("last_name"))))
                if not messagebox.askyesno("Change primary technician?", f"{name} is currently primary. Replace that assignment and preserve it in history?", parent=self): return
            if not messagebox.askyesno("Update existing Job?", "This Job ID already exists. Update only the reviewed On-Demand source fields?", parent=self): return
        update_protected = False
        try:
            existing = self.service.jobs.get_job_by_external_id(data.get("job_id") or "")
            if existing and str(existing.get("job_status")).casefold() in {"cancelled", "archived"}:
                if not messagebox.askyesno(
                    "Lifecycle-protected Job",
                    f"Job {data['job_id']} is {existing['job_status']}.\n\n"
                    "Update its source details while preserving that status?\n"
                    "Choose No to leave it unchanged.", parent=self):
                    return
                update_protected = True
            job_id, created = self.service.import_job(self.session, data,
                self.email_text.get("1.0", "end-1c"), self.notes_text.get("1.0", "end-1c"),
                tech_id, update_protected=update_protected)
        except (ValueError, LookupError) as exc:
            messagebox.showerror("On-Demand Job Intake", str(exc), parent=self); return
        self.status.set(f"Job {job_id} {'created' if created else 'updated'} successfully.")
        self._refresh_calendar_info()
        messagebox.showinfo("On-Demand Job Intake", self.status.get(), parent=self)
        if self.on_imported: self.on_imported()

    def copy_calendar(self):
        if not self.parsed:
            messagebox.showwarning("On-Demand Job Intake", "Parse both sources first.", parent=self); return
        self._refresh_calendar_info()
        if not self.technician_identity:
            messagebox.showwarning("On-Demand Job Intake", "Select a technician first.", parent=self); return
        if not self.technician_payout:
            messagebox.showwarning(
                "On-Demand Job Intake",
                "Technician payout is unable to be calculated. Review the Job and compensation rule before copying.",
                parent=self); return
        text = calendar_details(self._data(), self.technician_identity, self.technician_payout)
        self.clipboard_clear(); self.clipboard_append(text); self.update()
        self.status.set("Calendar details copied to the clipboard.")
        messagebox.showinfo("On-Demand Job Intake", self.status.get(), parent=self)


def open_on_demand_intake(parent, auth, session, on_imported=None):
    return OnDemandIntakeWindow(parent, auth, session, on_imported)
