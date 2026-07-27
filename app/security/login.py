"""Reusable modal login interface backed by the authentication service."""
import tkinter as tk
from tkinter import messagebox, ttk
from app.security.auth import AuthenticationError, AuthService, Session
from app.ui.styles import PADDING


def show_login(root: tk.Tk, auth: AuthService | None = None) -> Session | None:
    auth = auth or AuthService()
    result = None
    dialog = tk.Toplevel(root); dialog.title("Matterport Ops — Sign in"); dialog.resizable(False, False); dialog.transient(root); dialog.grab_set()
    frame=ttk.Frame(dialog,padding=PADDING*2);frame.pack()
    username,password=tk.StringVar(),tk.StringVar()
    ttk.Label(frame,text="Matterport Ops",style="Header.TLabel").grid(row=0,column=0,sticky="w",pady=(0,12))
    ttk.Label(frame,text="Username").grid(row=1,column=0,sticky="w"); user_entry=ttk.Entry(frame,textvariable=username,width=32);user_entry.grid(row=2,column=0,pady=(2,10))
    ttk.Label(frame,text="Password").grid(row=3,column=0,sticky="w");ttk.Entry(frame,textvariable=password,width=32,show="•").grid(row=4,column=0,pady=(2,14))
    def submit():
        nonlocal result
        try: result=auth.authenticate(username.get(),password.get())
        except AuthenticationError as exc: messagebox.showerror("Sign in failed",str(exc),parent=dialog);password.set("");return
        result.apply_to_environment();dialog.destroy()
    ttk.Button(frame,text="Sign in",command=submit).grid(row=5,column=0,sticky="e")
    dialog.protocol("WM_DELETE_WINDOW",dialog.destroy);dialog.bind("<Return>",lambda _e:submit());user_entry.focus_set();root.wait_window(dialog)
    return result


def main():
    root=tk.Tk();root.withdraw();session=show_login(root)
    if session: messagebox.showinfo("Matterport Ops",f"Authenticated as {session.username}.",parent=root)
    root.destroy()

if __name__ == "__main__": main()
