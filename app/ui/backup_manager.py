"""Database backup utility screen."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app.date_utils import format_display_datetime
from app.services.backup_service import BackupService
from app.ui.styles import PADDING


class BackupManager(ttk.Frame):
    def __init__(self, parent, auth, session):
        super().__init__(parent, padding=PADDING * 2, style="App.TFrame")
        self.auth, self.session = auth, session
        self.service = BackupService(auth)
        self.folder = tk.StringVar()
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self, text="Database Backup", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            self,
            text="Create a verified snapshot for distribution. Never open the live database from a synchronized folder.",
            wraplength=850,
        ).pack(anchor="w", pady=(4, 18))
        folder_row = ttk.Frame(self)
        folder_row.pack(fill="x")
        ttk.Label(folder_row, text="Configured backup folder").pack(anchor="w")
        entry_row = ttk.Frame(folder_row)
        entry_row.pack(fill="x", pady=(3, 12))
        ttk.Entry(entry_row, textvariable=self.folder, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(entry_row, text="Browse / Select Folder", command=self.select_folder).pack(side="left", padx=(8, 0))
        action = ttk.Frame(self)
        action.pack(fill="x")
        self.backup_button = ttk.Button(action, text="Backup Now", command=self.backup_now)
        self.backup_button.pack(side="left")
        ttk.Label(action, textvariable=self.status, style="Status.TLabel").pack(side="left", padx=14)
        self.summary = ttk.Label(self, text="", wraplength=850)
        self.summary.pack(anchor="w", pady=(14, 10))
        ttk.Label(self, text="Recent backup history", style="Header.TLabel").pack(anchor="w", pady=(12, 4))
        self.history = ttk.Treeview(self, columns=("when", "file", "size", "status", "integrity"), show="headings", height=10)
        for column, title, width in (("when", "Date / time", 170), ("file", "Filename", 280),
                                     ("size", "Size", 100), ("status", "Status", 90),
                                     ("integrity", "Integrity", 120)):
            self.history.heading(column, text=title)
            self.history.column(column, width=width, anchor="w")
        self.history.pack(fill="both", expand=True)
        self.refresh()

    def select_folder(self):
        selected = filedialog.askdirectory(parent=self, initialdir=self.folder.get() or None)
        if not selected:
            return
        try:
            self.service.configure_folder(Path(selected), self.session.user_id)
        except (OSError, ValueError) as error:
            messagebox.showerror("Backup folder", str(error), parent=self)
            return
        self.refresh()

    def backup_now(self, *, show_success=True):
        self.backup_button.configure(state="disabled")
        try:
            result = self.service.create_backup(self.session.user_id, self._progress)
        except Exception as error:
            self.status.set("Backup failed.")
            messagebox.showerror("Database backup", f"The backup could not be completed.\n\n{error}", parent=self)
            return False
        finally:
            self.backup_button.configure(state="normal")
            self.refresh()
        self.summary.configure(text=(f"Last successful backup: {format_display_datetime(result.completed_at)}\n"
                                     f"Filename: {result.filename}\nDestination: {result.destination.parent}\n"
                                     f"File size: {self._size(result.file_size)}"))
        if show_success:
            messagebox.showinfo("Database backup", self.summary.cget("text"), parent=self)
        return True

    def _progress(self, text):
        self.status.set(text)
        self.update_idletasks()

    @staticmethod
    def _size(value):
        return "—" if value is None else f"{value / 1024:,.1f} KB"

    def refresh(self):
        folder = self.service.backup_folder()
        self.folder.set(str(folder) if folder else "Not configured")
        for item in self.history.get_children():
            self.history.delete(item)
        rows = self.service.recent_history()
        for row in rows:
            self.history.insert("", "end", values=(format_display_datetime(row[0]), row[1], self._size(row[2]),
                                                     "Success" if row[3] else "Failed", row[4]))
        successful = next((row for row in rows if row[3]), None)
        if successful:
            self.summary.configure(text=f"Last successful backup: {format_display_datetime(successful[0])}\nFilename: {successful[1]}")
