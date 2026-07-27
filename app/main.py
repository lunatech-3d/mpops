"""Primary Matterport Ops desktop application entry point."""
import tkinter as tk
import sqlite3
from tkinter import messagebox
from app.security.auth import AuthService
from app.security.login import show_login
from app.ui.main_window import MainWindow
from app.ui.styles import configure_styles


def launch(auth: AuthService | None = None) -> None:
    try: auth=auth or AuthService()
    except (OSError, ValueError, sqlite3.Error):
        root=tk.Tk();root.withdraw();messagebox.showerror("Matterport Ops","The database could not be initialized.",parent=root);root.destroy();return
    root=tk.Tk();root.withdraw();configure_styles(root)
    running = True
    def login():
        nonlocal running
        session=show_login(root,auth)
        if session is None: running=False;root.destroy();return
        MainWindow(root,auth,session,login)
    login()
    if running: root.mainloop()

if __name__ == "__main__": launch()
