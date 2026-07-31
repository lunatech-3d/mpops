"""Basic Job editor for the Matterport Ops walking skeleton."""

import tkinter as tk
from tkinter import messagebox, ttk

from app.date_utils import display_datetime_to_iso, format_display_datetime
from app.ui.dialog_utils import close_modal, prepare_modal_dialog
from app.ui.scrollable_frame import ScrollableFrame
from app.ui.styles import PADDING


JOB_FORM_FIELDS = (
    "external_job_id", "market_id", "client_name_source", "project_name_source", "job_status",
    "scheduled_start_at", "capture_address_raw", "city", "state", "postal_code",
    "requested_capture_size", "onsite_contact_name", "onsite_contact_email",
    "onsite_contact_phone", "internal_notes",
)
PRIMARY_TECHNICIAN_FIELD = "primary_technician_id"

STATUS_VALUES = (
    "Requested", "Scheduling", "Scheduled", "Assigned", "In Progress",
    "Completed", "Cancelled", "On Hold",
)

JOB_FORM_MIN_WIDTH = 720
JOB_FORM_MAX_WIDTH = 900
JOB_FORM_MIN_HEIGHT = 480
JOB_FORM_MAX_HEIGHT = 760
JOB_FORM_SCREEN_MARGIN = 100


def technicians_by_first_name(technicians):
    """Return UI options alphabetized by first name without changing their IDs."""
    return sorted(
        technicians,
        key=lambda technician: (
            str(technician.get("first_name") or "").casefold(),
            str(technician.get("last_name") or "").casefold(),
            technician.get("tech_id") or 0,
        ),
    )


def job_form_data(values: dict) -> dict:
    """Normalize the basic job form into a JobsService payload."""
    result = {
        name: str(values.get(name, "")).strip()
        for name in JOB_FORM_FIELDS
        if name != "market_id"
    }
    result["market_id"] = values.get("market_id") or None
    # The assignment is stored in JobAssignments rather than Jobs, but it must
    # remain in the form payload so the controller can save it separately.
    result[PRIMARY_TECHNICIAN_FIELD] = values.get(PRIMARY_TECHNICIAN_FIELD) or None
    if not result["external_job_id"]:
        raise ValueError("External Job ID is required.")
    for name in JOB_FORM_FIELDS:
        if name == "requested_capture_size":
            result[name] = result[name] or None
        elif name != "external_job_id":
            result[name] = result[name] or None
    result["job_status"] = result["job_status"] or "Requested"
    return result


def changed_fields(original: dict, submitted: dict) -> dict:
    """Return only editable values that differ from the loaded Job."""
    changes = {}
    for name in JOB_FORM_FIELDS:
        old = original.get(name)
        if old in (None, ""):
            old = None
        new = submitted.get(name)
        if name == "requested_capture_size" and new is not None:
            try:
                new = float(new)
            except (TypeError, ValueError):
                pass
        if new != old:
            changes[name] = submitted.get(name)
    return changes


def show_job_form(parent, job: dict | None = None, markets=(), technicians=()) -> dict | None:
    """Show a compact modal Job editor and return submitted values."""
    result = None
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title("Edit Job" if job else "Add Job")

    shell = ttk.Frame(dialog)
    shell.pack(fill="both", expand=True)
    scrollable = ScrollableFrame(shell)
    scrollable.pack(fill="both", expand=True, padx=PADDING, pady=(PADDING, 0))
    content = scrollable.content
    content.configure(padding=(2, 2, 2, 4))
    content.columnconfigure(0, weight=1)

    variables = {
        name: tk.StringVar(value=(format_display_datetime((job or {}).get(name))
                                  if name == "scheduled_start_at"
                                  else "" if (job or {}).get(name) is None else str((job or {}).get(name))))
        for name in JOB_FORM_FIELDS if name not in {"internal_notes", "market_id"}
    }
    market_id_to_display = {
        market["market_id"]: f"{market['state']} - {market['market_name']}"
        for market in markets
    }
    market_display_to_id = {
        display: market_id for market_id, display in market_id_to_display.items()
    }
    market_var = tk.StringVar(
        value=market_id_to_display.get((job or {}).get("market_id"), "")
    )
    sorted_technicians = technicians_by_first_name(technicians)
    technician_id_to_display = {
        technician["tech_id"]: f"{technician['first_name']} {technician['last_name']}"
        for technician in sorted_technicians
    }
    technician_display_to_id = {
        display: tech_id for tech_id, display in technician_id_to_display.items()
    }
    technician_var = tk.StringVar(
        value=technician_id_to_display.get((job or {}).get(PRIMARY_TECHNICIAN_FIELD), "")
    )

    def section(title, row):
        frame = ttk.LabelFrame(content, text=title, padding=(10, 6))
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        return frame

    def labeled_entry(frame, row, label, variable, *, column=0, width=None, **kwargs):
        ttk.Label(frame, text=label).grid(
            row=row, column=column, sticky="w", padx=(0, 6), pady=3
        )
        entry = ttk.Entry(frame, textvariable=variable, width=width, **kwargs)
        entry.grid(row=row, column=column + 1, sticky="ew", padx=(0, 12), pady=3)
        return entry

    summary = section("Job Summary", 0)
    summary.columnconfigure(1, weight=1)
    summary.columnconfigure(3, weight=1)
    first = labeled_entry(summary, 0, "External Job ID *", variables["external_job_id"])
    ttk.Label(summary, text="Status").grid(row=0, column=2, sticky="w", padx=(0, 6), pady=3)
    status = ttk.Combobox(
        summary, textvariable=variables["job_status"], values=STATUS_VALUES,
        state="readonly", width=18,
    )
    status.grid(row=0, column=3, sticky="ew", padx=(0, 12), pady=3)
    if not variables["job_status"].get():
        variables["job_status"].set("Requested")
    labeled_entry(summary, 1, "Project", variables["project_name_source"])
    labeled_entry(summary, 1, "Client", variables["client_name_source"], column=2)
    ttk.Label(summary, text="Market").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
    market_entry = ttk.Combobox(
        summary, textvariable=market_var, values=tuple(market_display_to_id),
        state="readonly",
    )
    market_entry.grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=3)
    labeled_entry(
        summary, 2, "Requested Capture Size", variables["requested_capture_size"],
        column=2, width=18,
    )

    schedule = section("Schedule and Assignment", 1)
    schedule.columnconfigure(1, weight=1)
    schedule.columnconfigure(3, weight=1)
    labeled_entry(
        schedule, 0, "Scheduled Start", variables["scheduled_start_at"],
    )
    ttk.Label(schedule, text="Technician").grid(
        row=0, column=2, sticky="w", padx=(0, 6), pady=3
    )
    technician_entry = ttk.Combobox(
        schedule, textvariable=technician_var,
        values=("", *technician_display_to_id), state="readonly",
    )
    technician_entry.grid(row=0, column=3, sticky="ew", padx=(0, 12), pady=3)

    address = section("Capture Address", 2)
    address.columnconfigure(1, weight=3)
    labeled_entry(address, 0, "Capture Address", variables["capture_address_raw"])
    address.grid_slaves(row=0, column=1)[0].grid_configure(columnspan=5)
    labeled_entry(address, 1, "City", variables["city"])
    labeled_entry(address, 1, "State", variables["state"], column=2, width=7)
    labeled_entry(address, 1, "ZIP / Postal Code", variables["postal_code"], column=4, width=12)

    contact = section("On-Site Contact", 3)
    contact.columnconfigure(1, weight=1)
    contact.columnconfigure(3, weight=1)
    labeled_entry(contact, 0, "Contact Name", variables["onsite_contact_name"])
    labeled_entry(contact, 0, "Contact Phone", variables["onsite_contact_phone"], column=2)
    labeled_entry(contact, 1, "Contact Email", variables["onsite_contact_email"])
    contact.grid_slaves(row=1, column=1)[0].grid_configure(columnspan=3)

    notes_frame = section("Internal Notes", 4)
    notes_frame.columnconfigure(0, weight=1)
    notes = tk.Text(notes_frame, height=6, wrap="word")
    notes.grid(row=0, column=0, sticky="ew", pady=3)
    notes.insert("1.0", (job or {}).get("internal_notes") or "")

    financial_records = (job or {}).get("financial_records", [])
    if job is not None and financial_records:
        financial_frame = section("Current Financial Information", 5)
        financial_frame.columnconfigure(1, weight=1)
        invoices = ", ".join(
            record["ap_invoice_number"]
            for record in financial_records
            if record.get("ap_invoice_number")
        )
        ttk.Label(financial_frame, text="AP Invoice Number").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 5)
        )
        invoice_entry = ttk.Entry(financial_frame)
        invoice_entry.grid(row=0, column=1, sticky="ew", pady=(0, 5))
        invoice_entry.insert(0, invoices)
        invoice_entry.configure(state="readonly")
        financial_grid = ttk.Treeview(
            financial_frame,
            columns=("rate", "travel", "off_hours"),
            show="headings",
            height=min(len(financial_records), 4),
        )
        for column, heading in (
            ("rate", "CT Rate"),
            ("travel", "CT Travel Payout"),
            ("off_hours", "CT Off Hours Payout"),
        ):
            financial_grid.heading(column, text=heading)
            financial_grid.column(column, width=160, anchor="e")
        for record in financial_records:
            financial_grid.insert("", "end", values=(
                record.get("ct_rate") or 0,
                record.get("ct_travel_payout") or 0,
                record.get("ct_off_hours_payout") or 0,
            ))
        financial_grid.grid(row=1, column=0, columnspan=2, sticky="ew")

    def cancel(_event=None):
        close_modal(dialog)

    def save(_event=None):
        nonlocal result
        values = {name: variable.get() for name, variable in variables.items()}
        values["market_id"] = market_display_to_id.get(
            market_var.get(), (job or {}).get("market_id")
        )
        values[PRIMARY_TECHNICIAN_FIELD] = technician_display_to_id.get(
            technician_var.get()
        )
        values["internal_notes"] = notes.get("1.0", "end-1c")
        try:
            values["scheduled_start_at"] = display_datetime_to_iso(values["scheduled_start_at"]) or ""
            result = job_form_data(values)
        except ValueError as exc:
            messagebox.showerror("Invalid Job", str(exc), parent=dialog)
            return
        close_modal(dialog)

    # Keep primary actions outside the scrolling region so they remain visible
    # at every supported window height.
    buttons = ttk.Frame(shell, padding=(PADDING, 8, PADDING, PADDING))
    buttons.pack(fill="x")
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="right", padx=3)
    ttk.Button(buttons, text="Save", command=save).pack(side="right", padx=3)

    # Size from the completed layout, but reserve desktop chrome and rely on
    # the shared scrolling container whenever the content exceeds that space.
    dialog.update_idletasks()
    available_width = max(1, dialog.winfo_screenwidth() - JOB_FORM_SCREEN_MARGIN)
    available_height = max(1, dialog.winfo_screenheight() - JOB_FORM_SCREEN_MARGIN)
    requested_width = content.winfo_reqwidth() + (PADDING * 2) + 24
    requested_height = content.winfo_reqheight() + buttons.winfo_reqheight() + PADDING
    initial_width = min(max(JOB_FORM_MIN_WIDTH, requested_width),
                        JOB_FORM_MAX_WIDTH, available_width)
    initial_height = min(max(JOB_FORM_MIN_HEIGHT, requested_height),
                         JOB_FORM_MAX_HEIGHT, available_height)
    dialog.geometry(f"{initial_width}x{initial_height}")
    dialog.minsize(min(JOB_FORM_MIN_WIDTH, available_width),
                   min(JOB_FORM_MIN_HEIGHT, available_height))

    dialog.bind("<Escape>", cancel)
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    prepare_modal_dialog(dialog, parent)
    first.focus_set()
    parent.wait_window(dialog)
    return result
