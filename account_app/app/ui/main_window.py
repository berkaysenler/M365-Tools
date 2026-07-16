import csv
import os
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from app.auth import TokenManager
from app.exchange import ExchangeSession, _log
from app.config_loader import load_config, load_credentials
from app.graph import GraphManager
from app.syncro import SyncroClient
from app.ui.bulk import BulkCreationFrame
from app.ui.dialogs import DeviceCodeDialog
from app.ui.form import FormFrame
from app.ui.output import OutputPanel
from app.ui.sidebar import SidebarFrame
from app.ui.syncro_test import SyncroTestFrame
from portable_paths import LOGS_DIR

LOG_PATH = LOGS_DIR / "onboarding_log.csv"
LOG_FIELDS = [
    "Date", "RTO", "DisplayName", "UPN", "Password",
    "JobTitle", "Department", "Location", "Manager",
    "Groups", "Status",
]


@dataclass
class RTOState:
    session: ExchangeSession | None = None
    connected: bool = False


def _format_result(account, temp_password, status, warnings=None, error=None):
    """Copy-friendly result block — the same shape for Single and Bulk."""
    lines = [
        f"Status:   {status}",
        f"Login:    https://portal.office.com/",
        f"Display:  {account.display_name}",
        f"UPN:      {account.upn}",
        f"Password: {temp_password if status.startswith('Created') else 'N/A'}",
    ]
    if account.licenses:
        lines.append(
            "Licenses: " + ", ".join(l.get("name", "") for l in account.licenses)
        )
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    if error:
        lines.append(f"Error: {error}")
    return "\n".join(lines)


class MainWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("M365 Account Creation - VCIT")
        self.root.geometry("1280x800")
        self.root.minsize(960, 640)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.config = load_config()
        self.credentials = load_credentials(list(self.config.keys()))
        self.syncro = SyncroClient.from_env()
        self.token_mgr = TokenManager(self.config)
        self.graph_mgr = GraphManager(self.config)
        self.visible_rtos = self._visible_rtos()

        self._active_rto: str | None = None
        self._states: dict[str, RTOState] = {rto: RTOState() for rto in self.config}
        self._license_catalog: dict[str, list[dict]] = {}  # auth_rto -> options
        self._group_catalog: dict[str, list[dict]] = {}    # auth_rto -> groups
        self._auth_dialog = None

        self._build_ui()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)

        self.sidebar = SidebarFrame(
            self.root,
            self.visible_rtos,
            on_select=self._on_rto_select,
            on_connect=self._on_connect_click,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)

        single_tab = ttk.Frame(self.notebook)
        single_tab.columnconfigure(0, weight=3)
        single_tab.columnconfigure(1, weight=2)
        single_tab.rowconfigure(0, weight=1)
        self.notebook.add(single_tab, text="Single")

        self.form = FormFrame(
            single_tab,
            on_change=self._on_form_change,
            on_submit=self._on_submit,
        )
        self.form.grid(row=0, column=0, sticky="nsew")
        self.form.set_enabled(False)

        self.single_output = OutputPanel(single_tab)
        self.single_output.grid(row=0, column=1, sticky="nsew", padx=(8, 4), pady=4)

        self.bulk = BulkCreationFrame(self.notebook, on_submit=self._on_bulk_submit)
        self.notebook.add(self.bulk, text="Bulk")

        self.syncro_test = SyncroTestFrame(self.notebook, self.syncro, self.config)
        self.notebook.add(self.syncro_test, text="Syncro")

    # ------------------------------------------------------------------
    # RTO page switching
    # ------------------------------------------------------------------

    def _visible_rtos(self):
        managed = {
            child
            for cfg in self.config.values()
            for child in cfg.get("manages", [])
        }
        return [rto for rto in self.config if rto not in managed]

    def _on_rto_select(self, rto):
        self._active_rto = rto
        state = self._states[rto]

        self.sidebar.set_connect_state(state.connected)

        if state.connected:
            self._apply_state_to_form(rto)
            self.sidebar.set_status(f"{rto} — connected")
            # Covers sessions opened elsewhere (Student Checker) and earlier
            # failed loads — both loaders no-op instantly when already cached.
            self._load_licenses(rto)
            self._load_groups(rto)
        else:
            self.form.set_enabled(False)
            self.bulk.set_enabled(False)
            self.form.clear_live_data()
            self.sidebar.set_status(
                f"{rto} — {'connecting...' if state.session else 'not connected'}"
            )

    def _apply_state_to_form(self, rto):
        cfg = self.config[rto]
        domain_options = self._domain_options_for(rto)
        self.form.set_rto(
            rto=rto,
            domain=cfg["domain"],
            prefix=cfg.get("prefix", ""),
            default_group_name=cfg.get("default_group", ""),
            domain_options=domain_options,
        )
        self.form.set_enabled(True)
        auth_rto = self.token_mgr.get_auth_rto(rto)
        self.form.set_license_options(self._license_catalog.get(auth_rto, []))
        self.form.set_group_options(self._group_catalog.get(auth_rto, []))
        self.bulk.set_rto(
            rto=rto,
            domain=cfg["domain"],
            prefix=cfg.get("prefix", ""),
            default_group_name=cfg.get("default_group", ""),
            domain_options=domain_options,
        )
        self.bulk.set_enabled(True)

    def _domain_options_for(self, rto):
        rtos = [rto] + list(self.config[rto].get("manages", []))
        options = []
        for managed_rto in rtos:
            cfg = self.config.get(managed_rto, {})
            domain = cfg.get("domain", "")
            if not domain:
                continue
            options.append({
                "rto": managed_rto,
                "domain": domain,
                "prefix": cfg.get("prefix", ""),
                "default_group": cfg.get("default_group", ""),
                # which tenant's session/licenses this RTO uses
                "auth_rto": self.token_mgr.get_auth_rto(managed_rto),
                "label": f"{managed_rto} ({domain})",
            })
        return options

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    def _on_connect_click(self):
        if not self._active_rto:
            return
        state = self._states[self._active_rto]
        if state.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        rto = self._active_rto
        state = self._states[rto]
        rto_cfg = self.config.get(rto, {})
        admin_upn = rto_cfg.get("admin", "").strip()

        if not admin_upn:
            messagebox.showerror(
                "Config Missing",
                f"No admin UPN configured for {rto}.\n"
                f"Add the 'admin' field in config.json for {rto}.",
            )
            return

        state.session = ExchangeSession(hide_window=True)

        # MSAL device-code → cached refresh token → EXO -AccessToken.
        self.sidebar.set_status("Checking cached token...")

        def on_code_ready(user_code, verification_uri):
            # Background thread → marshal to UI thread.
            self.root.after(0, lambda: self._show_device_code(user_code, verification_uri))

        def on_token(token, error):
            if error or not token:
                self.root.after(0, lambda: self._on_auth_done(rto, error or "No token returned"))
                return
            self.root.after(0, lambda: self._connect_with_token(rto, admin_upn, token))

        self.token_mgr.acquire_token_device_code(rto, on_code_ready, on_token)

    def _show_device_code(self, user_code, verification_uri):
        if self._auth_dialog:
            try:
                self._auth_dialog.destroy()
            except tk.TclError:
                pass
        self._auth_dialog = DeviceCodeDialog(self.root, user_code, verification_uri)
        self.sidebar.set_status("Waiting for device-code sign-in...")

    def _connect_with_token(self, rto, admin_upn, token):
        state = self._states.get(rto)
        if not state or not state.session:
            return
        self.sidebar.set_status("Connecting to Exchange Online...")
        state.session.connect(
            admin_upn,
            lambda code, url: None,
            lambda err: self.root.after(0, lambda: self._on_auth_done(rto, err)),
            access_token=token,
        )

    def _close_auth_dialog(self):
        if self._auth_dialog:
            try:
                self._auth_dialog.destroy()
            except tk.TclError:
                pass
            self._auth_dialog = None

    def _on_auth_done(self, rto, error):
        self._close_auth_dialog()

        state = self._states[rto]

        if error:
            state.session = None
            if self._active_rto == rto:
                self.sidebar.set_connect_state(False)
                self.sidebar.set_status("Authentication failed")
            messagebox.showerror("Authentication Failed", error)
            return

        state.connected = True
        if self._active_rto == rto:
            self.sidebar.set_connect_state(True)
            self._apply_state_to_form(rto)
            self.sidebar.set_status(f"{rto} — connected, loading licenses...")
        self._load_licenses(rto)
        self._load_groups(rto)

    # ------------------------------------------------------------------
    # Licenses (Microsoft Graph)
    # ------------------------------------------------------------------

    def _load_licenses(self, rto):
        """Fetch the tenant's SKUs in the background and fill the dropdowns.

        Uses a separate Graph token (Azure CLI client) — the first ever load
        needs one extra device-code sign-in, then it's silent from cache.
        """
        auth_rto = self.graph_mgr.get_auth_rto(rto)
        if auth_rto in self._license_catalog:
            self.root.after(0, lambda: self._push_licenses(auth_rto))
            return

        def _run():
            def on_code(user_code, verification_uri):
                self.root.after(
                    0, lambda: self._show_device_code(user_code, verification_uri)
                )
                self.root.after(
                    0,
                    lambda: self.sidebar.set_status(
                        "Sign in once more to load licenses (Microsoft Graph)"
                    ),
                )

            token, err = self.graph_mgr.acquire_token(rto, on_code)
            self.root.after(0, self._close_auth_dialog)
            if err or not token:
                self.root.after(
                    0,
                    lambda: self.sidebar.set_status(
                        f"{rto} — connected (licenses unavailable)"
                    ),
                )
                return
            try:
                skus = self.graph_mgr.list_skus(token)
            except Exception:
                self.root.after(
                    0,
                    lambda: self.sidebar.set_status(
                        f"{rto} — connected (license fetch failed)"
                    ),
                )
                return
            self._license_catalog[auth_rto] = [
                {
                    "skuId": s["skuId"],
                    "name": s["name"],
                    "label": f"{s['name']} ({s['available']} available)",
                }
                for s in skus
            ]
            self.root.after(0, lambda: self._push_licenses(auth_rto))

        threading.Thread(target=_run, daemon=True).start()

    def _push_licenses(self, auth_rto):
        options = self._license_catalog.get(auth_rto, [])
        self.bulk.set_license_options(auth_rto, options)
        if (
            self._active_rto
            and self.graph_mgr.get_auth_rto(self._active_rto) == auth_rto
        ):
            self.form.set_license_options(options)
            self.sidebar.set_status(
                f"{self._active_rto} — connected, "
                f"{len(options)} license type(s) available"
            )

    def _load_groups(self, rto):
        """Fetch the tenant's mail-enabled groups once per run (background,
        over the existing EXO session) and fill the group search pickers."""
        auth_rto = self.token_mgr.get_auth_rto(rto)
        if auth_rto in self._group_catalog:
            self.root.after(0, lambda: self._push_groups(auth_rto))
            return
        state = self._states.get(rto)
        session = state.session if state and state.connected else None
        if not session:
            return

        def _run():
            _log(f"Group fetch start for {auth_rto}")
            try:
                groups = session.get_distribution_groups()
            except Exception as exc:
                _log(f"Group fetch FAILED for {auth_rto}: {exc}")
                self.root.after(
                    0,
                    lambda: self.sidebar.set_status(
                        f"{rto} — group load failed, reconnect to retry"
                    ),
                )
                return  # not cached — next connect/select retries
            _log(f"Group fetch OK for {auth_rto}: {len(groups)} groups")
            if not groups:
                self.root.after(
                    0,
                    lambda: self.sidebar.set_status(
                        f"{rto} — no groups returned (see debug.log)"
                    ),
                )
                return  # not cached — next connect/select retries
            self._group_catalog[auth_rto] = groups
            self.root.after(0, lambda: self._push_groups(auth_rto))

        threading.Thread(target=_run, daemon=True).start()

    def _push_groups(self, auth_rto):
        options = self._group_catalog.get(auth_rto, [])
        self.bulk.set_group_options(auth_rto, options)
        if (
            self._active_rto
            and self.token_mgr.get_auth_rto(self._active_rto) == auth_rto
        ):
            self.form.set_group_options(options)
            self.sidebar.set_status(
                f"{self._active_rto} — connected, {len(options)} groups loaded"
            )

    def _assign_license(self, account) -> str | None:
        """Assign the selected licenses via Graph. Returns a warning or None.

        Runs on a worker thread after the mailbox exists. Retries the user
        lookup because a freshly created account can take a little while to
        become visible to Graph.
        """
        if not account.licenses:
            return None
        names = ", ".join(l.get("name", "") for l in account.licenses)
        token = self.graph_mgr.get_token_silent(account.rto)
        if not token:
            return (
                f"License(s) '{names}' not assigned: Graph sign-in "
                "expired. Reconnect the tenant and assign them manually."
            )
        try:
            user = None
            for delay in (0, 10, 20, 30):
                if delay:
                    time.sleep(delay)
                user = self.graph_mgr.get_user_basic(token, account.upn)
                if user:
                    break
            if not user:
                return (
                    f"License(s) '{names}' not assigned: "
                    f"{account.upn} is not visible in Entra ID yet. Assign them "
                    "manually in a few minutes."
                )
            if not user.get("usageLocation"):
                self.graph_mgr.set_usage_location(
                    token, account.upn, account.usage_location or "AU"
                )
            self.graph_mgr.assign_licenses(
                token, account.upn,
                [l.get("skuId", "") for l in account.licenses],
            )
            return None
        except Exception as exc:
            return f"License(s) '{names}' not assigned: {exc}"

    def _disconnect(self):
        rto = self._active_rto
        state = self._states[rto]

        if state.session:
            threading.Thread(target=state.session.disconnect, daemon=True).start()

        self._states[rto] = RTOState()

        if self._active_rto == rto:
            self.sidebar.set_connect_state(False)
            self.sidebar.set_status(f"{rto} — disconnected")
            self.form.set_enabled(False)
            self.bulk.set_enabled(False)
            self.form.clear_live_data()

    # ------------------------------------------------------------------
    # Form callbacks
    # ------------------------------------------------------------------

    def _on_form_change(self, account):
        pass  # summary panel removed — results go to the output panel

    def _on_submit(self, account, temp_password):
        if not self._active_rto:
            return
        state = self._states[self._active_rto]
        if not state.session:
            return

        self.form.set_submit_state(False)
        session = state.session

        def _create():
            try:
                if session.account_exists(account.upn):
                    self.root.after(0, lambda: self._on_account_exists(account))
                    return
            except Exception:
                pass  # non-fatal — proceed and let New-Mailbox surface conflicts

            post_errors = []
            try:
                post_errors.extend(session.create_mailbox(account, temp_password) or [])
            except Exception as exc:
                err = str(exc)
                self.root.after(0, lambda: self._on_create_error(account, err))
                return

            post_errors.extend(
                session.add_to_groups(account.upn, account.distribution_groups)
            )
            license_warn = self._assign_license(account)
            if license_warn:
                post_errors.append(license_warn)
            syncro_err = self._sync_to_syncro(account)
            if syncro_err:
                post_errors.append(syncro_err)
            self.root.after(
                0,
                lambda: self._on_create_success(account, temp_password, post_errors),
            )

        threading.Thread(target=_create, daemon=True).start()

    def _on_bulk_submit(self, rows):
        self.form.set_submit_state(False)
        self.bulk.set_submit_state(False)

        def _create_all():
            created = 0
            skipped = 0
            failed = 0
            not_connected = 0
            warnings_count = 0

            for index, (account, temp_password) in enumerate(rows, start=1):
                self.root.after(
                    0,
                    lambda i=index, total=len(rows), upn=account.upn:
                        self.bulk.set_status(f"Creating {i}/{total}: {upn}"),
                )

                # Each row is created through its own tenant's session —
                # managed RTOs (e.g. AIBT, CG) resolve to the managing
                # tenant's connection (VC). Rows for tenants that are not
                # connected stay in the pending list.
                session_rto = self.token_mgr.get_auth_rto(account.rto)
                state = self._states.get(session_rto)
                session = state.session if state and state.connected else None
                if not session:
                    not_connected += 1
                    msg = (
                        f"{session_rto} is not connected. Select {session_rto} "
                        "in the sidebar, connect, then press Create All "
                        "Accounts again — this row stays in the pending list."
                    )
                    self.root.after(
                        0,
                        lambda acc=account, pw=temp_password, err=msg:
                            self.bulk.append_output(_format_result(acc, pw, "Skipped - tenant not connected", error=err)),
                    )
                    continue

                try:
                    if session.account_exists(account.upn):
                        skipped += 1
                        self._log_result(account, password="N/A", status="Skipped - already exists")
                        self.root.after(
                            0,
                            lambda acc=account, pw=temp_password:
                                self.bulk.append_output(_format_result(acc, pw, "Skipped - already exists")),
                        )
                        self.root.after(0, lambda acc=account: self.bulk.mark_created(acc))
                        continue

                    post_errors = session.create_mailbox(account, temp_password) or []
                    post_errors.extend(
                        session.add_to_groups(account.upn, account.distribution_groups)
                    )
                    license_warn = self._assign_license(account)
                    if license_warn:
                        post_errors.append(license_warn)
                    syncro_err = self._sync_to_syncro(account)
                    if syncro_err:
                        post_errors.append(syncro_err)
                    warnings_count += len(post_errors)
                    status = "Created" + (f" (warnings: {len(post_errors)})" if post_errors else "")
                    self._log_result(account, password=temp_password, status=status)
                    created += 1
                    self.root.after(
                        0,
                        lambda acc=account, pw=temp_password, warns=list(post_errors):
                            self.bulk.append_output(_format_result(acc, pw, "Created", warnings=warns)),
                    )
                    self.root.after(0, lambda acc=account: self.bulk.mark_created(acc))
                except Exception as exc:
                    failed += 1
                    self._log_result(account, password="N/A", status=f"Failed: {exc}")
                    self.root.after(
                        0,
                        lambda acc=account, pw=temp_password, err=str(exc):
                            self.bulk.append_output(_format_result(acc, pw, "Failed", error=err)),
                    )

            self.root.after(
                0,
                lambda: self._on_bulk_done(created, skipped, failed, not_connected, warnings_count),
            )

        threading.Thread(target=_create_all, daemon=True).start()

    def _on_bulk_done(self, created, skipped, failed, not_connected, warnings_count):
        self.form.set_submit_state(True)
        self.bulk.set_submit_state(True)
        summary = (
            f"Done. Created: {created}, skipped: {skipped}, failed: {failed}, "
            f"warnings: {warnings_count}."
        )
        if not_connected:
            summary += (
                f" {not_connected} row(s) need another tenant — connect it and "
                "press Create All Accounts again."
            )
        self.bulk.set_status(summary)

    def _on_account_exists(self, account):
        self.form.set_submit_state(True)
        self._log_result(account, password="N/A", status="Skipped - already exists")
        self.single_output.append(
            _format_result(account, "N/A", "Skipped - already exists")
        )

    def _on_create_success(self, account, temp_password, warnings):
        self.form.set_submit_state(True)
        status = "Created" + (f" (warnings: {len(warnings)})" if warnings else "")
        self._log_result(account, password=temp_password, status=status)
        self.single_output.append(
            _format_result(account, temp_password, "Created", warnings=warnings)
        )

    def _on_create_error(self, account, error):
        self.form.set_submit_state(True)
        self._log_result(account, password="N/A", status=f"Failed: {error}")
        self.single_output.append(
            _format_result(account, "N/A", "Failed", error=error)
        )

    def _sync_to_syncro(self, account) -> str | None:
        if not self.syncro:
            return None
        org_id = self.config.get(account.rto, {}).get("syncro_org_id")
        if not org_id:
            return None
        try:
            return self.syncro.create_contact(org_id, account)
        except Exception as exc:
            return f"Syncro sync failed: {exc}"

    def _log_result(self, account, password, status):
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            new_file = not LOG_PATH.exists()
            with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
                if new_file:
                    w.writeheader()
                w.writerow({
                    "Date": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "RTO": account.rto,
                    "DisplayName": account.display_name,
                    "UPN": account.upn,
                    "Password": password,
                    "JobTitle": account.title,
                    "Department": account.department,
                    "Location": account.office_location,
                    "Manager": account.manager_upn or account.manager_display,
                    "Groups": "; ".join(
                        g.get("displayName", "") for g in account.distribution_groups
                    ),
                    "Status": status,
                })
        except Exception:
            pass  # logging must never block account creation

    # ------------------------------------------------------------------

    def _on_close(self):
        for state in self._states.values():
            if state.session:
                threading.Thread(target=state.session.disconnect, daemon=True).start()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


class AccountCreationSection(tk.Frame):
    def __init__(self, parent, session_states=None):
        super().__init__(parent, bg="#f0f0f0")
        self.root = self.winfo_toplevel()

        self.config = load_config()
        self.credentials = load_credentials(list(self.config.keys()))
        self.syncro = SyncroClient.from_env()
        self.token_mgr = TokenManager(self.config)
        self.graph_mgr = GraphManager(self.config)
        self.visible_rtos = self._visible_rtos()

        self._active_rto: str | None = None
        self._states = session_states if session_states is not None else {}
        for rto in self.config:
            self._states.setdefault(rto, RTOState())
        self._license_catalog: dict[str, list[dict]] = {}
        self._group_catalog: dict[str, list[dict]] = {}
        self._auth_dialog = None

        self._build_ui()

    def _build_ui(self):
        self.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        self.sidebar = SidebarFrame(
            self,
            self.visible_rtos,
            on_select=self._on_rto_select,
            on_connect=self._on_connect_click,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)

        single_tab = ttk.Frame(self.notebook)
        single_tab.columnconfigure(0, weight=3)
        single_tab.columnconfigure(1, weight=2)
        single_tab.rowconfigure(0, weight=1)
        self.notebook.add(single_tab, text="Single")

        self.form = FormFrame(
            single_tab,
            on_change=self._on_form_change,
            on_submit=self._on_submit,
        )
        self.form.grid(row=0, column=0, sticky="nsew")
        self.form.set_enabled(False)

        self.single_output = OutputPanel(single_tab)
        self.single_output.grid(row=0, column=1, sticky="nsew", padx=(8, 4), pady=4)

        self.bulk = BulkCreationFrame(self.notebook, on_submit=self._on_bulk_submit)
        self.notebook.add(self.bulk, text="Bulk")

        self.syncro_test = SyncroTestFrame(self.notebook, self.syncro, self.config)
        self.notebook.add(self.syncro_test, text="Syncro")

    _visible_rtos = MainWindow._visible_rtos
    _on_rto_select = MainWindow._on_rto_select
    _apply_state_to_form = MainWindow._apply_state_to_form
    _domain_options_for = MainWindow._domain_options_for
    _on_connect_click = MainWindow._on_connect_click
    _connect = MainWindow._connect
    _show_device_code = MainWindow._show_device_code
    _connect_with_token = MainWindow._connect_with_token
    _close_auth_dialog = MainWindow._close_auth_dialog
    _on_auth_done = MainWindow._on_auth_done
    _load_licenses = MainWindow._load_licenses
    _push_licenses = MainWindow._push_licenses
    _load_groups = MainWindow._load_groups
    _push_groups = MainWindow._push_groups
    _assign_license = MainWindow._assign_license
    _disconnect = MainWindow._disconnect
    _on_form_change = MainWindow._on_form_change
    _on_submit = MainWindow._on_submit
    _on_bulk_submit = MainWindow._on_bulk_submit
    _on_bulk_done = MainWindow._on_bulk_done
    _on_account_exists = MainWindow._on_account_exists
    _on_create_success = MainWindow._on_create_success
    _on_create_error = MainWindow._on_create_error
    _sync_to_syncro = MainWindow._sync_to_syncro
    _log_result = MainWindow._log_result
    _on_close = MainWindow._on_close
