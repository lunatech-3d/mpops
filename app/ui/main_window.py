"""Main Matterport Ops application shell."""
from tkinter import messagebox, ttk
from app.security.auth import Session
from app.ui.dashboard import build_dashboard
from app.ui.user_manager_window import open_user_manager
from app.ui.styles import NAV_BACKGROUND, PADDING


class MainWindow:
    def __init__(self, root, auth, session, on_logout):
        self.root, self.auth, self.session, self.on_logout = root, auth, session, on_logout
        root.title("Matterport Ops"); root.geometry("1100x700"); root.minsize(850, 550); root.deiconify()
        self.secondary_windows = []
        header=ttk.Frame(root,padding=PADDING);header.pack(fill="x")
        ttk.Label(header,text="Matterport Ops",style="Header.TLabel").pack(side="left")
        ttk.Label(header,text=f"Signed in as {session.username} | {session.role.title()}").pack(side="right")
        body=ttk.Frame(root);body.pack(fill="both",expand=True)
        nav=ttk.Frame(body,padding=PADDING);nav.pack(side="left",fill="y")
        self.content=ttk.Frame(body,style="App.TFrame");self.content.pack(side="left",fill="both",expand=True)
        for name in ("Dashboard","Jobs","Technicians","Markets","Clients","Payments","Reports"):
            command=self.show_dashboard if name=="Dashboard" else lambda n=name:self.show_placeholder(n)
            ttk.Button(nav,text=name,style="Nav.TButton",command=command,width=18).pack(fill="x",pady=2)
        ttk.Separator(nav).pack(fill="x",pady=8)
        ttk.Button(nav,text="Administration → Users",command=self.open_users).pack(fill="x",pady=2)
        ttk.Button(nav,text="Log Out",command=self.logout).pack(fill="x",pady=(20,2))
        ttk.Button(nav,text="Exit",command=root.destroy).pack(fill="x",pady=2)
        self.show_dashboard()

    def clear(self):
        for child in self.content.winfo_children(): child.destroy()
    def show_dashboard(self):
        self.clear();build_dashboard(self.content,self.session,self.auth.settings.database_path).pack(fill="both",expand=True)
    def show_placeholder(self,name):
        self.clear(); frame=ttk.Frame(self.content,padding=PADDING*2,style="App.TFrame");frame.pack(fill="both",expand=True)
        ttk.Label(frame,text=name,style="Header.TLabel").pack(anchor="w");ttk.Label(frame,text="This module has not yet been implemented.",style="Status.TLabel").pack(anchor="w",pady=12)
    def open_users(self):
        window=open_user_manager(self.root,self.auth,self.session)
        if window:self.secondary_windows.append(window)
    def logout(self):
        for window in self.secondary_windows:
            if window.winfo_exists():window.destroy()
        Session.clear_environment()
        for child in self.root.winfo_children():child.destroy()
        self.root.withdraw();self.on_logout()
