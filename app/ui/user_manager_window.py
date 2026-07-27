"""Administrator user-management window."""
import tkinter as tk
from tkinter import messagebox, ttk
from app.security.user_manager import AuthorizationError, UserManager, VALID_ROLES
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
        dialog = tk.Toplevel(window); dialog.title("Edit user" if user else "Add user"); dialog.transient(window); dialog.grab_set()
        fields = ttk.Frame(dialog, padding=PADDING); fields.pack()
        username = tk.StringVar(value=user["username"] if user else "")
        display = tk.StringVar(value=(user["display_name"] or "") if user else "")
        role = tk.StringVar(value=user["role"] if user else "operator")
        password = tk.StringVar()
        entries = (("Username", username), ("Display name", display), ("Password", password))
        for row, (label, variable) in enumerate(entries):
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(fields, textvariable=variable, show="•" if label == "Password" else "", width=30)
            entry.grid(row=row, column=1, pady=4); entry.configure(state="disabled" if user and label in ("Username", "Password") else "normal")
        ttk.Label(fields, text="Role").grid(row=3, column=0, sticky="w")
        ttk.Combobox(fields, textvariable=role, values=sorted(VALID_ROLES), state="readonly").grid(row=3, column=1)
        def save():
            try:
                if user: manager.update_user(user["id"], display_name=display.get(), role=role.get(), actor=session)
                else: manager.create_user(username.get(), password.get(), role.get(), session, display.get())
            except (ValueError, LookupError, AuthorizationError) as exc: messagebox.showerror("Unable to save", str(exc), parent=dialog); return
            dialog.destroy(); refresh()
        ttk.Button(fields, text="Save", command=save).grid(row=4, column=1, sticky="e", pady=10)

    def edit():
        try: user_form(manager.get_user(selected(), session))
        except LookupError as exc: messagebox.showwarning("Users", str(exc), parent=window)

    def reset():
        try: uid = selected()
        except LookupError as exc: messagebox.showwarning("Users", str(exc), parent=window); return
        dialog = tk.Toplevel(window); dialog.title("Reset password"); dialog.transient(window); dialog.grab_set(); body=ttk.Frame(dialog,padding=PADDING);body.pack()
        first, second = tk.StringVar(), tk.StringVar()
        for row,(label,var) in enumerate((("New password",first),("Confirm password",second))):
            ttk.Label(body,text=label).grid(row=row,column=0,sticky="w");ttk.Entry(body,textvariable=var,show="•").grid(row=row,column=1,pady=4)
        def save():
            if first.get()!=second.get(): messagebox.showerror("Password", "Passwords do not match", parent=dialog); return
            try: manager.reset_password(uid, first.get(), session)
            except (ValueError, LookupError) as exc: messagebox.showerror("Password",str(exc),parent=dialog);return
            dialog.destroy();refresh()
        ttk.Button(body,text="Reset",command=save).grid(row=2,column=1,sticky="e")

    def toggle():
        try:
            uid=selected(); user=manager.get_user(uid,session); manager.set_active(uid,not bool(user["is_active"]),session); refresh()
        except (ValueError,LookupError) as exc: messagebox.showerror("Users",str(exc),parent=window)
    buttons=ttk.Frame(window,padding=PADDING);buttons.pack(fill="x")
    for label,command in (("Add user",lambda:user_form()),("Edit user",edit),("Reset password",reset),("Activate / Deactivate",toggle),("Close",window.destroy)):
        ttk.Button(buttons,text=label,command=command).pack(side="left",padx=3)
    search.trace_add("write", refresh); status.trace_add("write", refresh); refresh()
    return window
