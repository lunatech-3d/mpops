"""Main LunaTech 3D Ops application shell."""
import tkinter as tk
from tkinter import messagebox, ttk

from app.resources import resource_path
from app.security.auth import Session
from app.ui.dashboard import build_dashboard
from app.ui.jobs_manager import JobsManager
from app.ui.opentable_import_window import open_opentable_import
from app.ui.on_demand_intake_window import open_on_demand_intake
from app.ui.payments_workspace import PaymentsWorkspace
from app.ui.administration_workspace import AdministrationWorkspace
from app.ui.user_manager_window import open_user_manager
from app.ui.technician_manager import TechnicianManager
from app.ui.styles import PADDING
from app.ui.window_utils import bind_maximize_shortcut, maximize_window
from app.services.backup_service import BackupService
from app.ui.backup_manager import BackupManager
from app.date_utils import format_display_datetime


def _fit_logo(image, max_width=180, max_height=48):
    """Return a proportionally subsampled logo that fits in the header."""
    width_factor = (image.width() + max_width - 1) // max_width
    height_factor = (image.height() + max_height - 1) // max_height
    scale = max(1, width_factor, height_factor)
    return image if scale == 1 else image.subsample(scale, scale)


class MainWindow:
    def __init__(self, root, auth, session, on_logout):
        self.root, self.auth, self.session, self.on_logout = root, auth, session, on_logout
        # A new authenticated shell always owns a clean root, including after
        # repeated login/logout cycles.
        for child in root.winfo_children():
            child.destroy()
        # Keep a sensible restored size for multi-window work, but make the
        # authenticated shell use the full desktop by default. F11 toggles the
        # normal window-manager maximized state without entering fullscreen.
        root.title("LunaTech 3D Ops"); root.geometry("1500x800"); root.minsize(1200, 650); root.deiconify()
        bind_maximize_shortcut(root)
        root.after_idle(lambda: maximize_window(root))
        self.secondary_windows = []
        header = ttk.Frame(root, padding=PADDING)
        header.pack(fill="x")
        brand = ttk.Frame(header)
        brand.grid(row=0, column=0, sticky="w")
        # Keep the fitted PhotoImage on the window: Tk discards images whose
        # Python wrapper is garbage-collected.  Deriving the scale from the
        # actual asset prevents a large source logo from taking over the form.
        source_logo = tk.PhotoImage(file=resource_path("lunatech-logo.png"))
        self.brand_logo = _fit_logo(source_logo)
        ttk.Label(brand, image=self.brand_logo).pack(side="left")
        ttk.Label(brand, text="3D Ops", style="Header.TLabel").pack(side="left", padx=(12, 0))
        ttk.Label(
            header,
            text=f"Signed in as: {session.display_name or session.username} | Role: {session.role.title()}",
        ).grid(row=0, column=1, sticky="e", padx=(PADDING, 0))
        if auth.settings.reporting_copy:
            modified = __import__("datetime").datetime.fromtimestamp(
                auth.settings.database_path.stat().st_mtime
            ).astimezone()
            ttk.Label(
                header,
                text=f"REPORTING COPY — Backup file modified {format_display_datetime(modified)}",
                foreground="#9b1c1c",
                font=("Segoe UI", 11, "bold"),
            ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        header.columnconfigure(0, weight=1)
        body=ttk.Frame(root);body.pack(fill="both",expand=True)
        nav=ttk.Frame(body,padding=PADDING);nav.pack(side="left",fill="y")
        self.content=ttk.Frame(body,style="App.TFrame");self.content.pack(side="left",fill="both",expand=True)
        names = (("Dashboard",) if auth.settings.reporting_copy else
                 ("Dashboard","Jobs","Technicians","Payments","Administration"))
        for name in names:
            command=(self.show_dashboard if name=="Dashboard" else
                     self.show_jobs if name=="Jobs" else
                     self.show_technicians if name=="Technicians" else
                     self.show_payments if name=="Payments" else
                     self.show_administration if name=="Administration" else
                     lambda n=name:self.show_placeholder(n))
            ttk.Button(nav,text=name,style="Nav.TButton",command=command,width=18).pack(fill="x",pady=2)
        ttk.Separator(nav).pack(fill="x",pady=8)
        ttk.Button(nav,text="Log Out",command=self.logout).pack(fill="x",pady=(20,2))
        ttk.Button(nav,text="Exit",command=self.request_exit).pack(fill="x",pady=2)
        root.protocol("WM_DELETE_WINDOW", self.request_exit)
        # Matterport jobs are the current operational focus, so make the job
        # list the first screen users see after signing in.
        self.show_dashboard() if auth.settings.reporting_copy else self.show_jobs()

    def clear(self):
        for child in self.content.winfo_children(): child.destroy()
    def show_dashboard(self):
        self.clear();build_dashboard(self.content,self.session,self.auth).pack(fill="both",expand=True)
    def show_jobs(self):
        self.clear(); JobsManager(self.content,self.auth,self.session,
            open_opentable=self.open_opentable_import,
            open_on_demand=self.open_on_demand_intake).pack(fill="both",expand=True)
    def show_technicians(self):
        self.clear(); TechnicianManager(self.content,self.auth,self.session).pack(fill="both",expand=True)
    def show_payments(self):
        self.clear(); PaymentsWorkspace(self.content,self.auth,self.session).pack(fill="both",expand=True)
    def show_administration(self):
        self.clear(); AdministrationWorkspace(self.content,self.auth,self.session,self.open_users).pack(fill="both",expand=True)
    def show_backup(self):
        self.clear(); BackupManager(self.content, self.auth, self.session).pack(fill="both", expand=True)
    def show_placeholder(self,name):
        self.clear(); frame=ttk.Frame(self.content,padding=PADDING*2,style="App.TFrame");frame.pack(fill="both",expand=True)
        ttk.Label(frame,text=name,style="Header.TLabel").pack(anchor="w");ttk.Label(frame,text="This module has not yet been implemented.",style="Status.TLabel").pack(anchor="w",pady=12)
    def _register_large_window(self, window):
        if window:
            bind_maximize_shortcut(window)
            window.after_idle(lambda: maximize_window(window))
            self.secondary_windows.append(window)
        return window
    def open_opentable_import(self):
        return self._register_large_window(open_opentable_import(
            self.root,
            self.auth,
            self.session,
            on_imported=self.show_jobs,
        ))
    def open_on_demand_intake(self):
        return self._register_large_window(open_on_demand_intake(
            self.root, self.auth, self.session, on_imported=self.show_jobs,
        ))
    def open_users(self):
        window=open_user_manager(self.root,self.auth,self.session)
        if window:self.secondary_windows.append(window)
    def logout(self):
        for window in self.secondary_windows:
            if window.winfo_exists():window.destroy()
        Session.clear_environment()
        for child in self.root.winfo_children():child.destroy()
        self.root.withdraw();self.on_logout()

    def request_exit(self):
        if self.auth.settings.reporting_copy:
            self.root.destroy(); return
        service = BackupService(self.auth)
        reminder = service.get_setting("backup_close_reminder", "1") == "1"
        if reminder and not service.backed_up_today():
            choice = messagebox.askyesnocancel(
                "Daily database backup",
                "No database backup has been completed today.\n\nWould you like to back up the database before exiting?\n\n"
                "Yes: Back Up Now    No: Exit Without Backup    Cancel: Keep MPOPS open",
                parent=self.root,
            )
            if choice is None:
                return
            if choice:
                self.show_backup()
                manager = next((child for child in self.content.winfo_children() if isinstance(child, BackupManager)), None)
                if manager is None or not manager.backup_now():
                    return
        self.root.destroy()