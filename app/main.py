"""Primary Matterport Ops desktop application entry point."""
import tkinter as tk
import sqlite3
from tkinter import messagebox
from app.security.auth import AuthService
from app.security.login import show_login
from app.security.user_manager import UserManager
from app.ui.initial_admin import show_initial_admin_dialog
from app.ui.main_window import MainWindow
from app.ui.styles import configure_styles


def requires_initial_admin(users: UserManager) -> bool:
    """Keep the first-run startup decision independent from Tk for testing."""
    return users.count_users() == 0


def launch(auth: AuthService | None = None) -> None:
    try: auth=auth or AuthService()
    except (OSError, ValueError, sqlite3.Error):
        root=tk.Tk();root.withdraw();messagebox.showerror("Matterport Ops","The database could not be initialized.",parent=root);root.destroy();return
    root=tk.Tk();root.withdraw();configure_styles(root)
    users = UserManager(auth)
    if requires_initial_admin(users) and not show_initial_admin_dialog(root, users):
        root.destroy(); return
    running = True
    def login():
        nonlocal running
        session=show_login(root,auth)
        if session is None: running=False;root.destroy();return
        MainWindow(root,auth,session,login)
    login()
    if running: root.mainloop()

if __name__ == "__main__": launch()
