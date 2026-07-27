"""Administrator user-management window."""
import tkinter as tk
from tkinter import messagebox, ttk
from app.security.user_manager import AuthorizationError, UserManager
from app.ui.password_reset import show_password_reset
from app.ui.user_form import show_user_form
from app.ui.styles import PADDING


def open_user_manager(parent, auth, session):
    if session.role != "admin":
        messagebox.showerror("Access denied", "Administrator role required.", parent=parent)
        return None
    manager = UserManager(auth)
    window = tk.Toplevel(parent)
    window.title("Matterport Ops — Users")
    window.geometry("980x520")
    controls = ttk.Frame(window, padding=PADDING); controls.pack(fill="x")
    search, status = tk.StringVar(), tk.StringVar(value="All")
    ttk.Label(controls, text="Search").pack(side="left")
    ttk.Entry(controls, textvariable=search, width=28).pack(side="left", padx=6)
    ttk.Combobox(controls, textvariable=status, values=("All", "Active", "Inactive"), state="readonly", width=10).pack(side="left")
    columns = ("username", "display_name", "role", "active", "created", "last_login", "updated")
    tree = ttk.Treeview(window, columns=columns, show="headings", selectmode="browse")
    for col, title, width in zip(columns, ("Username", "Display name", "Role", "Active", "Created", "Last login", "Updated"), (120,150,80,60,145,145,145)):
        tree.heading(col, text=title); tree.column(col, width=width, anchor="w")
    tree.pack(fill="both", expand=True, padx=PADDING)

    def selected():
        items = tree.selection()
        if not items:
            raise LookupError("Select a user first")
        return int(items[0])

    def refresh(*_):
        tree.delete(*tree.get_children())
        active = {"All": None, "Active": True, "Inactive": False}[status.get()]
        for user in manager.list_users(session, search.get(), active):
            tree.insert("", "end", iid=str(user["id"]), values=(user["username"], user["display_name"] or "", user["role"],
                "Yes" if user["is_active"] else "No", user["created_at"], user["last_login_at"] or "—", user["updated_at"] or "—"))

    def user_form(user=None):
        values = show_user_form(window, user)
        if not values: return
        try:
            if user:
                manager.update_user(user["id"], display_name=values["display_name"], role=values["role"], actor=session)
                if bool(user["is_active"]) != values["is_active"]:
                    manager.set_active(user["id"], values["is_active"], session)
            else:
                manager.create_user(values["username"], values["password"], values["role"], session,
                                    values["display_name"], values["is_active"])
        except (ValueError, LookupError, AuthorizationError) as exc:
            messagebox.showerror("Unable to save", str(exc), parent=window); return
        refresh()

    def edit():
        try: user_form(manager.get_user(selected(), session))
        except LookupError as exc: messagebox.showwarning("Users", str(exc), parent=window)

    def reset():
        try: uid = selected()
        except LookupError as exc: messagebox.showwarning("Users", str(exc), parent=window); return
        password = show_password_reset(window)
        if password is None: return
        try: manager.reset_password(uid, password, session)
        except (ValueError, LookupError) as exc: messagebox.showerror("Password",str(exc),parent=window);return
        refresh()

    def toggle():
        try:
            uid=selected(); user=manager.get_user(uid,session); manager.set_active(uid,not bool(user["is_active"]),session); refresh()
        except (ValueError,LookupError) as exc: messagebox.showerror("Users",str(exc),parent=window)
    buttons=ttk.Frame(window,padding=PADDING);buttons.pack(fill="x")
    for label,command in (("Add user",lambda:user_form()),("Edit user",edit),("Reset password",reset),("Activate / Deactivate",toggle),("Close",window.destroy)):
        ttk.Button(buttons,text=label,command=command).pack(side="left",padx=3)
    search.trace_add("write", refresh); status.trace_add("write", refresh); refresh()
    return window
