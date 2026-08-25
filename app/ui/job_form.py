"""Basic Job editor for the Matterport Ops walking skeleton."""

from datetime import datetime

import tkinter as tk
from tkinter import messagebox, ttk

from app.date_utils import display_date_to_iso
from app.ui.dialog_utils import close_modal, prepare_modal_dialog
from app.ui.scrollable_frame import ScrollableFrame
from app.ui.styles import PADDING


JOB_FORM_FIELDS = (
    "external_job_id", "market_id", "client_name_source", "project_name_source", "job_status",
    "scheduled_start_at", "capture_address_raw", "address_1", "address_2", "city", "state",
    "postal_code", "county", "country", "requested_capture_size", "expected_job_revenue",
    "onsite_contact_name", "onsite_contact_email", "onsite_contact_phone", "internal_notes",
)
PRIMARY_TECHNICIAN_FIELD = "primary_technician_id"
JOB_READONLY_FIELDS = frozenset({"capture_address_raw"})

ADDRESS_FIELD_LABELS = {
    "address_1": "Address 1",
    "address_2": "Address 2",
    "city": "City",
    "state": "State",
    "postal_code": "ZIP / Postal Code",
    "county": "County",
    "country": "Country",
}

STATUS_VALUES = (
    "Requested", "Scheduling", "Scheduled", "Assigned", "In Progress",
    "Completed", "Cancelled", "Archived", "On Hold",
)
MERIDIEM_VALUES = ("AM", "PM")

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
        if name in {"requested_capture_size", "expected_job_revenue"}:
            result[name] = result[name] or None
        elif name != "external_job_id":
            result[name] = result[name] or None
    result["job_status"] = result["job_status"] or "Requested"
    return result


def scheduled_start_parts(value) -> tuple[str, str, str, str]:
    """Return date, hour, minute, and meridiem values for the schedule controls."""
    if value in (None, ""):
        return "", "12", "00", "AM"
    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        # Preserve an unexpected legacy value where the user can correct it.
        return str(value), "12", "00", "AM"
    hour = parsed.strftime("%I").lstrip("0") or "12"
    return parsed.strftime("%m/%d/%Y"), hour, parsed.strftime("%M"), parsed.strftime("%p")


def scheduled_start_to_iso(date_value, hour_value, minute_value, meridiem_value) -> str | None:
    """Combine the user-friendly schedule controls into a stored ISO timestamp."""
    if not str(date_value or "").strip():
        return None
    iso_date = display_date_to_iso(date_value)
    try:
        hour = int(hour_value)
        minute = int(minute_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Scheduled start hour and minute must be selected.") from exc
    meridiem = str(meridiem_value or "").upper()
    if hour not in range(1, 13) or minute not in range(60) or meridiem not in MERIDIEM_VALUES:
        raise ValueError("Scheduled start time is invalid.")
    hour_24 = hour % 12 + (12 if meridiem == "PM" else 0)
    return f"{iso_date}T{hour_24:02d}:{minute:02d}"


def changed_fields(original: dict, submitted: dict) -> dict:
    """Return only editable values that differ from the loaded Job."""
    changes = {}
    for name in JOB_FORM_FIELDS:
        if name in JOB_READONLY_FIELDS:
            continue
        old = original.get(name)
        if old in (None, ""):
            old = None
        new = submitted.get(name)
        if name in {"requested_capture_size", "expected_job_revenue"} and new is not None:
            try:
                new = float(new)
            except (TypeError, ValueError):
                pass
        if new != old:
            changes[name] = submitted.get(name)
    return changes


def show_job_form(parent, job: dict | None = None, markets=(), technicians=(), *,
                  lifecycle_permissions: dict | None = None) -> dict | None:
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
        name: tk.StringVar(value=("" if (job or {}).get(name) is None
                                  else str((job or {}).get(name))))
        for name in JOB_FORM_FIELDS
        if name not in {"internal_notes", "market_id", "scheduled_start_at"}
    }
    schedule_date, schedule_hour, schedule_minute, schedule_meridiem = scheduled_start_parts(
        (job or {}).get("scheduled_start_at")
    )
    schedule_date_var = tk.StringVar(value=schedule_date)
    schedule_hour_var = tk.StringVar(value=schedule_hour)
    schedule_minute_var = tk.StringVar(value=schedule_minute)
    schedule_meridiem_var = tk.StringVar(value=schedule_meridiem)
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
    labeled_entry(
        summary, 3, "Job Amount / Expected Revenue ($)", variables["expected_job_revenue"],
        column=2, width=18,
    )

    schedule = section("Schedule and Assignment", 1)
    schedule.columnconfigure(1, weight=1)
    schedule.columnconfigure(3, weight=1)
    ttk.Label(schedule, text="Scheduled Start").grid(
        row=0, column=0, sticky="w", padx=(0, 6), pady=3
    )
    schedule_controls = ttk.Frame(schedule)
    schedule_controls.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=3)
    ttk.Entry(schedule_controls, textvariable=schedule_date_var, width=12).pack(side="left")
    ttk.Label(schedule_controls, text="  at  ").pack(side="left")
    ttk.Spinbox(schedule_controls, textvariable=schedule_hour_var, from_=1, to=12,
                wrap=True, width=3).pack(side="left")
    ttk.Label(schedule_controls, text=":").pack(side="left")
    ttk.Spinbox(schedule_controls, textvariable=schedule_minute_var, from_=0, to=59,
                wrap=True, width=3, format="%02.0f").pack(side="left")
    ttk.Combobox(schedule_controls, textvariable=schedule_meridiem_var,
                 values=MERIDIEM_VALUES, state="readonly", width=4).pack(side="left", padx=(4, 0))
    ttk.Label(schedule, text="Date: MM/DD/YYYY or MM-DD-YYYY").grid(
        row=1, column=1, sticky="w", padx=(0, 12), pady=(0, 3)
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
    address.columnconfigure(3, weight=2)
    source_address = labeled_entry(
        address, 0, "Imported Source Address", variables["capture_address_raw"],
        state="readonly",
    )
    source_address.grid_configure(columnspan=5)
    ttk.Label(
        address,
        text=(
            "Read-only source evidence. Correct the operational address below; locally changed "
            "fields are preserved during later imports."
        ),
        style="Status.TLabel",
        wraplength=760,
    ).grid(row=1, column=1, columnspan=5, sticky="w", padx=(0, 12), pady=(0, 5))
    labeled_entry(address, 2, "Address 1", variables["address_1"])
    address.grid_slaves(row=2, column=1)[0].grid_configure(columnspan=5)
    labeled_entry(address, 3, "Address 2", variables["address_2"])
    address.grid_slaves(row=3, column=1)[0].grid_configure(columnspan=5)
    labeled_entry(address, 4, "City", variables["city"])
    labeled_entry(address, 4, "State", variables["state"], column=2, width=7)
    labeled_entry(
        address, 4, "ZIP / Postal Code", variables["postal_code"], column=4, width=12
    )
    labeled_entry(address, 5, "County", variables["county"])
    labeled_entry(address, 5, "Country", variables["country"], column=2, width=12)
    protected_fields = [
        ADDRESS_FIELD_LABELS[field]
        for field in ADDRESS_FIELD_LABELS
        if field in set((job or {}).get("protected_fields") or ())
    ]
    if protected_fields:
        ttk.Label(
            address,
            text="Protected from source imports: " + ", ".join(protected_fields),
            style="Status.TLabel",
            wraplength=760,
        ).grid(row=6, column=1, columnspan=5, sticky="w", padx=(0, 12), pady=(4, 0))

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

    def lifecycle(action):
        nonlocal result
        result = {"__lifecycle_action": action}
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
            values["scheduled_start_at"] = scheduled_start_to_iso(
                schedule_date_var.get(), schedule_hour_var.get(),
                schedule_minute_var.get(), schedule_meridiem_var.get(),
            ) or ""
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
    permissions = lifecycle_permissions or {}
    if job is not None:
        ttk.Button(buttons, text="Cancel Job", command=lambda: lifecycle("cancel"),
                   state="normal" if permissions.get("cancel") else "disabled").pack(side="left", padx=3)
        ttk.Button(buttons, text="Archive Job", command=lambda: lifecycle("archive"),
                   state="normal" if permissions.get("archive") else "disabled").pack(side="left", padx=3)
        if permissions.get("delete_visible"):
            ttk.Button(buttons, text="Delete Draft", command=lambda: lifecycle("delete"),
                       state="normal" if permissions.get("delete") else "disabled").pack(side="left", padx=3)

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