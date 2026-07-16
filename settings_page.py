import subprocess
import tkinter as tk
from tkinter import messagebox, ttk

from portable_paths import APP_DATA_DIR, CONFIG_FILE, LAST_IDS_FILE, LOGS_DIR, STUDENT_ACCOUNTS_FILE, initialize_user_data
from setup_wizard import CredentialDialog, _upsert_config_account

ENV_FILE = APP_DATA_DIR / ".env"


def _read_env() -> dict:
    """Return SYNCRO_SUBDOMAIN and SYNCRO_API_KEY from the .env file if it exists."""
    values = {"SYNCRO_SUBDOMAIN": "", "SYNCRO_API_KEY": ""}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            key = key.strip()
            if key in values:
                values[key] = val.strip()
    return values

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SettingsSection(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=20)
        self.columnconfigure(0, weight=1)
        self._build()

    def _build(self):
        ttk.Label(
            self,
            text="Settings",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(
            self,
            text="Add accounts (device-code sign-in), open the user data folders, and check required PowerShell modules.",
            wraplength=760,
        ).grid(row=1, column=0, sticky="ew", pady=(0, 16))

        actions = ttk.Frame(self)
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        ttk.Button(actions, text="Add account", command=self._add_credential).pack(side="left")
        ttk.Button(actions, text="Open Last Student CSV", command=self._open_last_ids).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open Account Log CSV", command=self._open_account_log).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open app data folder", command=lambda: self._open_path(APP_DATA_DIR)).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Check PowerShell module", command=self._check_module).pack(side="left", padx=(8, 0))

        paths = ttk.LabelFrame(self, text="Current user data", padding=12)
        paths.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        paths.columnconfigure(1, weight=1)
        for row, (label, path) in enumerate([
            ("App data", APP_DATA_DIR),
            ("Config", CONFIG_FILE),
            ("Student accounts", STUDENT_ACCOUNTS_FILE),
            ("Last student CSV", LAST_IDS_FILE),
            ("Account log CSV", LOGS_DIR / "onboarding_log.csv"),
        ]):
            ttk.Label(paths, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
            ttk.Label(paths, text=str(path)).grid(row=row, column=1, sticky="w", pady=3)

        # Syncro integration
        syncro = ttk.LabelFrame(self, text="Syncro integration", padding=12)
        syncro.grid(row=4, column=0, sticky="ew", pady=(0, 14))
        syncro.columnconfigure(1, weight=1)

        ttk.Label(syncro, text="Subdomain").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        self._syncro_subdomain = tk.StringVar()
        ttk.Entry(syncro, textvariable=self._syncro_subdomain).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(syncro, text="e.g. vconsultancy", foreground="#777").grid(row=0, column=2, sticky="w", padx=(8, 0))

        ttk.Label(syncro, text="API Key").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        self._syncro_api_key = tk.StringVar()
        ttk.Entry(syncro, textvariable=self._syncro_api_key, show="*").grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(syncro, text="Show / Hide", command=self._toggle_api_key).grid(row=1, column=2, padx=(8, 0))

        syncro_btn_row = ttk.Frame(syncro)
        syncro_btn_row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(syncro_btn_row, text="Save Syncro settings", command=self._save_syncro).pack(side="left")
        ttk.Button(syncro_btn_row, text="Clear", command=self._clear_syncro).pack(side="left", padx=(8, 0))

        self._api_key_entry = syncro.grid_slaves(row=1, column=1)[0]
        self._load_syncro()

        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var, foreground="#555").grid(
            row=5, column=0, sticky="ew"
        )

    def _load_syncro(self):
        env = _read_env()
        self._syncro_subdomain.set(env["SYNCRO_SUBDOMAIN"])
        self._syncro_api_key.set(env["SYNCRO_API_KEY"])

    def _save_syncro(self):
        subdomain = self._syncro_subdomain.get().strip()
        api_key = self._syncro_api_key.get().strip()
        if not subdomain and not api_key:
            messagebox.showwarning("Empty", "Enter a subdomain and API key.", parent=self)
            return
        initialize_user_data(copy_default_config=False)
        lines = []
        if subdomain:
            lines.append(f"SYNCRO_SUBDOMAIN={subdomain}")
        if api_key:
            lines.append(f"SYNCRO_API_KEY={api_key}")
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.status_var.set("Syncro settings saved. Restart the app to apply.")

    def _clear_syncro(self):
        if not messagebox.askyesno("Clear Syncro settings", "Remove Syncro subdomain and API key?", parent=self):
            return
        self._syncro_subdomain.set("")
        self._syncro_api_key.set("")
        if ENV_FILE.exists():
            ENV_FILE.unlink()
        self.status_var.set("Syncro settings cleared.")

    def _toggle_api_key(self):
        current = self._api_key_entry.cget("show")
        self._api_key_entry.configure(show="" if current else "*")

    def _add_credential(self):
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

    def _open_path(self, path):
        initialize_user_data(copy_default_config=True)
        try:
            path.mkdir(parents=True, exist_ok=True)
            getattr(__import__("os"), "startfile")(path)
        except Exception as exc:
            messagebox.showerror("Could not open folder", str(exc), parent=self)

    def _open_last_ids(self):
        initialize_user_data(copy_default_config=True)
        if not LAST_IDS_FILE.exists():
            LAST_IDS_FILE.write_text("Date,Last Student IDs\n", encoding="utf-8")
        try:
            getattr(__import__("os"), "startfile")(LAST_IDS_FILE)
        except Exception as exc:
            messagebox.showerror("Could not open CSV", str(exc), parent=self)

    def _open_account_log(self):
        initialize_user_data(copy_default_config=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / "onboarding_log.csv"
        if not log_path.exists():
            log_path.write_text(
                "Date,RTO,DisplayName,UPN,Password,JobTitle,Department,Location,Manager,Groups,Status\n",
                encoding="utf-8",
            )
        try:
            getattr(__import__("os"), "startfile")(log_path)
        except Exception as exc:
            messagebox.showerror("Could not open CSV", str(exc), parent=self)

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
