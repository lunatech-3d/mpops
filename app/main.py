"""Primary Matterport Ops desktop application entry point."""
import logging
import tkinter as tk
import sqlite3
from tkinter import messagebox
from app.security.auth import AuthService
from app.security.login import show_login
from app.security.user_manager import UserManager
from app.ui.initial_admin import show_initial_admin_dialog
from app.ui.main_window import MainWindow
from app.ui.styles import configure_styles
from app.ui.text_context_menu import install_text_context_menu


logger = logging.getLogger(__name__)


def requires_initial_admin(users: UserManager) -> bool:
    """Keep the first-run startup decision independent from Tk for testing."""
    return users.count_users() == 0


def launch(auth: AuthService | None = None) -> None:
    try:
        auth = auth or AuthService()
    except (OSError, ValueError, sqlite3.Error) as error:
        logger.exception("Database initialization failed")
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "LunaTech 3D Ops",
            "The database could not be initialized.\n\n"
            f"{type(error).__name__}: {error}\n\n"
            "Run 'python -m app.verify_database' for database diagnostics.",
            parent=root,
        )
        root.destroy()
        return
    root = tk.Tk()
    root.withdraw()
    # Install before login/first-run dialogs; bind_all also covers every future
    # form and dynamically-created Toplevel owned by this interpreter.
    install_text_context_menu(root)
    configure_styles(root)
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
