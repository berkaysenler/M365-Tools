import subprocess
import tkinter as tk
import json
from tkinter import messagebox, ttk

from app.ui.theme import C_BG, C_DIM, C_FIELD, C_TEXT
from portable_paths import (
    APP_DATA_DIR,
    CONFIG_FILE,
    LAST_IDS_FILE,
    LOGS_DIR,
    STUDENT_ACCOUNTS_FILE,
    initialize_user_data,
)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SetupWizard(ttk.Frame):
    def __init__(self, parent, on_complete):
        super().__init__(parent, padding=24)
        self.on_complete = on_complete
        self.columnconfigure(0, weight=1)
        self._build()

    def _build(self):
        ttk.Label(
            self,
            text="First-time setup",
            font=("Segoe UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(
            self,
            text=(
                "This setup creates the user data folders and lets you add the "
                "M365 tenants you manage. Sign-in uses a one-time device code per "
                "tenant — Microsoft handles the password and MFA, the app never "
                "sees them."
            ),
            wraplength=760,
        ).grid(row=1, column=0, sticky="ew", pady=(0, 18))

        paths = ttk.LabelFrame(self, text="User data location", padding=12)
        paths.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        paths.columnconfigure(1, weight=1)
        for row, (label, path) in enumerate([
            ("App data", APP_DATA_DIR),
            ("Config", CONFIG_FILE),
            ("Logs", LOGS_DIR),
            ("Last student IDs", LAST_IDS_FILE),
        ]):
            ttk.Label(paths, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=2)
            ttk.Label(paths, text=str(path)).grid(row=row, column=1, sticky="w", pady=2)

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        ttk.Button(actions, text="Create folders and config", command=self._initialize).pack(side="left")
        ttk.Button(actions, text="Add account", command=self._add_account_credential).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open app data folder", command=self._open_app_data).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Check PowerShell module", command=self._check_module).pack(side="left", padx=(8, 0))

        guide = ttk.LabelFrame(self, text="Getting started", padding=12)
        guide.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        guide.columnconfigure(0, weight=1)
        self.command_text = tk.Text(
            guide, height=6, wrap="word", font=("Segoe UI", 9),
            bg=C_FIELD, fg=C_TEXT, insertbackground=C_TEXT,
            relief="flat", highlightthickness=0,
        )
        self.command_text.grid(row=0, column=0, sticky="ew")
        self.command_text.insert(
            "1.0",
            "1. Click Create folders and config.\n"
            "2. Click Add account for each tenant you manage (RTO name, admin email, domain).\n"
            "3. Open the app and click Connect on each tenant — sign in with a one-time device code in any browser.\n"
            "4. After the first sign-in you stay connected silently for ~90 days.\n"
            "Make sure ExchangeOnlineManagement is installed: Install-Module ExchangeOnlineManagement -Scope CurrentUser",
        )
        self.command_text.configure(state="disabled")

        self.status_var = tk.StringVar(value="Create the folders first, then add an account per tenant.")
        ttk.Label(self, textvariable=self.status_var, foreground=C_DIM).grid(
            row=5, column=0, sticky="ew", pady=(0, 12)
        )

        finish = ttk.Frame(self)
        finish.grid(row=6, column=0, sticky="e")
        ttk.Button(finish, text="Open application", command=self._finish).pack(side="right")

    def _initialize(self):
        initialize_user_data(copy_default_config=True)
        self.status_var.set("Setup files created. Click 'Add account' for each tenant you manage.")

    def _add_account_credential(self):
        initialize_user_data(copy_default_config=True)
        dlg = CredentialDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return

        try:
            _upsert_config_account(dlg.result)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
            return

        self.status_var.set(
            f"Saved {dlg.result['rto'].upper()}. Sign in with device code on first Connect."
        )

    def _open_app_data(self):
        initialize_user_data(copy_default_config=True)
        try:
            os_startfile = getattr(__import__("os"), "startfile")
            os_startfile(APP_DATA_DIR)
        except Exception as exc:
            messagebox.showerror("Could not open folder", str(exc), parent=self)

    def _check_module(self):
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "if (Get-Module -ListAvailable ExchangeOnlineManagement) "
                    "{ 'ExchangeOnlineManagement installed' } else { 'ExchangeOnlineManagement missing' }",
                ],
                capture_output=True,
                text=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=20,
            )
            self.status_var.set((result.stdout or result.stderr).strip())
        except Exception as exc:
            self.status_var.set(f"PowerShell check failed: {exc}")

    def _finish(self):
        initialize_user_data(copy_default_config=True)
        self.on_complete()


class CredentialDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Add account")
        self.geometry("520x380")
        self.resizable(False, False)
        self.configure(bg=C_BG)
        self.transient(parent)
        self.grab_set()
        self.result = None

        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        self.vars = {
            "rto": tk.StringVar(),
            "admin": tk.StringVar(),
            "domain": tk.StringVar(),
            "prefix": tk.StringVar(),
            "default_group": tk.StringVar(),
            "syncro_org_id": tk.StringVar(),
        }

        fields = [
            ("RTO name", "rto", "e.g. ORG1"),
            ("Admin email", "admin", "admin@example.com"),
            ("Domain", "domain", "example.edu.au"),
            ("Prefix", "prefix", "e.g. ORG1"),
            ("Default group", "default_group", "e.g. All Students"),
            ("Syncro org ID", "syncro_org_id", "optional"),
        ]
        for row, (label, key, placeholder) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
            entry = ttk.Entry(body, textvariable=self.vars[key])
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            if placeholder:
                ttk.Label(body, text=placeholder, foreground=C_DIM).grid(row=row, column=2, sticky="w", padx=(8, 0))

        self.vars["rto"].trace_add("write", self._autofill)

        info = ttk.Label(
            body,
            text=(
                "Sign-in uses a one-time device code per tenant — Microsoft handles the "
                "password and MFA. After signing in once you stay connected silently for ~90 days."
            ),
            wraplength=460,
            foreground=C_DIM,
        )
        info.grid(row=len(fields), column=0, columnspan=3, sticky="ew", pady=(12, 6))

        buttons = ttk.Frame(body)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right")

    def _autofill(self, *_):
        rto = self.vars["rto"].get().strip()
        if rto and not self.vars["prefix"].get().strip():
            self.vars["prefix"].set(rto.upper())

    def _save(self):
        data = {key: var.get().strip() for key, var in self.vars.items()}
        required = ["rto", "admin", "domain"]
        if [key for key in required if not data[key]]:
            messagebox.showwarning("Missing fields", "RTO, admin email and domain are required.", parent=self)
            return
        self.result = data
        self.destroy()


def _upsert_config_account(data: dict):
    """Add/update an RTO entry in config.json. Always device-code auth."""
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    else:
        config = {}

    rto = data["rto"].upper()
    entry = config.get(rto, {})
    entry.update({
        "domain": data["domain"],
        "admin": data["admin"],
        "prefix": data.get("prefix") or rto,
        "default_group": data.get("default_group", ""),
    })
    if data.get("syncro_org_id"):
        try:
            entry["syncro_org_id"] = int(data["syncro_org_id"])
        except ValueError:
            entry["syncro_org_id"] = data["syncro_org_id"]
    entry.setdefault("syncro_org_id", entry.get("syncro_org_id", ""))
    entry.setdefault("notification_recipients", entry.get("notification_recipients", []))
    config[rto] = entry
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    _upsert_student_account(data)


def _upsert_student_account(data: dict):
    if STUDENT_ACCOUNTS_FILE.exists():
        try:
            accounts = json.loads(STUDENT_ACCOUNTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            accounts = []
    else:
        accounts = []

    rto = data["rto"].upper()
    account = {
        "rto": rto,
        "upn": data["admin"],
        "group": data.get("default_group") or "All Students",
        "id_col": "StudentID",
    }

    for index, existing in enumerate(accounts):
        if existing.get("rto", "").upper() == rto:
            accounts[index] = account
            break
    else:
        accounts.append(account)

    STUDENT_ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2), encoding="utf-8")
