"""Reusable modal login interface backed by the authentication service."""
import tkinter as tk
from tkinter import messagebox, ttk
from app.security.auth import AuthenticationError, AuthService, Session
from app.ui.styles import PADDING
from app.ui.dialog_utils import close_modal, prepare_modal_dialog


def show_login(root: tk.Tk, auth: AuthService | None = None) -> Session | None:
    auth = auth or AuthService()
    result = None
    dialog = tk.Toplevel(root); dialog.title("LunaTech 3D Ops Login"); dialog.resizable(False, False)
    frame=ttk.Frame(dialog,padding=PADDING*2);frame.pack()
    username,password=tk.StringVar(),tk.StringVar()
    ttk.Label(frame,text="LunaTech 3D Ops",style="Header.TLabel").grid(row=0,column=0,sticky="w",pady=(0,12))
    ttk.Label(frame,text="Username").grid(row=1,column=0,sticky="w"); user_entry=ttk.Entry(frame,textvariable=username,width=32);user_entry.grid(row=2,column=0,pady=(2,10))
    ttk.Label(frame,text="Password").grid(row=3,column=0,sticky="w");ttk.Entry(frame,textvariable=password,width=32,show="•").grid(row=4,column=0,pady=(2,14))
    def submit():
        nonlocal result
        try: result=auth.authenticate(username.get(),password.get())
        except AuthenticationError: messagebox.showerror("Sign in failed","Invalid username or password.",parent=dialog);password.set("");return
        result.apply_to_environment();close_modal(dialog)
    buttons=ttk.Frame(frame);buttons.grid(row=5,column=0,sticky="e")
    ttk.Button(buttons,text="Login",command=submit).pack(side="left",padx=3);ttk.Button(buttons,text="Cancel",command=lambda:close_modal(dialog)).pack(side="left",padx=3)
    dialog.protocol("WM_DELETE_WINDOW",lambda:close_modal(dialog));dialog.bind("<Return>",lambda _e:submit());dialog.bind("<Escape>",lambda _e:close_modal(dialog))
    prepare_modal_dialog(dialog,root);user_entry.focus_set()
    try:
        root.wait_window(dialog)
    finally:
        if dialog.winfo_exists():close_modal(dialog)
    return result


def main():
    root=tk.Tk();root.withdraw();session=show_login(root)
    if session: messagebox.showinfo("LunaTech 3D Ops",f"Authenticated as {session.username}.",parent=root)
    root.destroy()

if __name__ == "__main__": main()
