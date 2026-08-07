"""Grouped administration business area."""
from tkinter import ttk

from app.ui.backup_manager import BackupManager
from app.ui.market_manager import MarketManager
from app.ui.styles import PADDING


class AdministrationWorkspace(ttk.Frame):
    def __init__(self,parent,auth,session,open_users):
        super().__init__(parent,padding=PADDING,style="App.TFrame")
        ttk.Label(self,text="Administration",style="Header.TLabel").pack(anchor="w",pady=(0,8))
        book=ttk.Notebook(self);book.pack(fill="both",expand=True)
        markets,clients,users,backup=(ttk.Frame(book) for _ in range(4))
        for frame,name in ((markets,"Markets"),(clients,"Clients"),(users,"Users"),(backup,"Database Backup")):
            book.add(frame,text=name)
        MarketManager(markets,auth,session).pack(fill="both",expand=True)
        ttk.Label(clients,text="Client configuration remains available through job and lookup workflows.",padding=PADDING).pack(anchor="w")
        button=ttk.Button(users,text="Open User Administration",command=open_users);button.pack(anchor="w",padx=PADDING,pady=PADDING)
        if session.role != "admin": button.configure(state="disabled")
        BackupManager(backup,auth,session).pack(fill="both",expand=True)
