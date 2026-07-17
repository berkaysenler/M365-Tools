"""User Manager section.

Pick a tenant, search a user by name or email via Microsoft Graph, view the
profile, edit the details and save back to M365. When the user's tenant
(matched by UPN domain) has a syncro_org_id, the Syncro contact is updated
too — only the fields Syncro receives: email, display name, job title, city.
Tenants without a syncro_org_id never touch Syncro. Graph failures block the
save; Syncro failures are warnings only.
"""

import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from app.config_loader import load_config
from app.graph import GraphManager
from app.syncro import SyncroClient
from app.ui.dialogs import DeviceCodeDialog
from app.ui.theme import (
    C_BG,
    C_BORDER,
    C_DIM,
    C_ERR,
    C_FIELD,
    C_OK,
    C_PANEL,
    C_SELECTED,
    C_TEXT,
    C_WARN,
)

FONT_MONO = ("Consolas", 10)

# Profile keys editable as plain entries: (field key, label)
_EDIT_FIELDS = [
    ("displayName", "Display name"),
    ("upn", "Email (UPN)"),
    ("jobTitle", "Job title"),
    ("department", "Department"),
    ("city", "City / office"),
    ("mobilePhone", "Mobile"),
    ("manager_upn", "Manager (username or email, blank = none)"),
]


class UserManagerSection(ctk.CTkFrame):
    def __init__(self, parent, session_states=None):
        super().__init__(parent, corner_radius=0, fg_color=C_BG)
        self.root = self.winfo_toplevel()

        self.config = load_config()
        self.graph_mgr = GraphManager(self.config)

        self._results: list[dict] = []
        self._result_rows: list[ctk.CTkFrame] = []
        self._profile: dict | None = None
        self._profile_rto: str = ""
        self._entries: dict[str, ctk.CTkEntry] = {}
        self._auth_dialog = None
        self._busy = False

        self._build()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            header, text="User Manager", text_color=C_TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="Search a user, edit their details, save to Microsoft 365 (and Syncro when configured).",
            text_color=C_DIM, font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(14, 0))

        # ---- Search card ------------------------------------------------
        card = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=10)
        card.grid(row=1, column=0, sticky="ew", padx=16, pady=6)
        card.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(card, text="Tenant:", text_color=C_TEXT).grid(
            row=0, column=0, padx=(14, 6), pady=12, sticky="w"
        )
        rtos = list(self.config.keys())
        self.tenant_combo = ctk.CTkOptionMenu(
            card, values=rtos or ["—"], width=150, height=30,
            fg_color=C_FIELD, button_color="#2a3042",
            button_hover_color=C_SELECTED, text_color=C_TEXT,
            dropdown_fg_color=C_PANEL, dropdown_text_color=C_TEXT,
            dropdown_hover_color="#2a3042",
        )
        if rtos:
            self.tenant_combo.set(rtos[0])
        self.tenant_combo.grid(row=0, column=1, pady=12, sticky="w")

        self.query_entry = ctk.CTkEntry(
            card, placeholder_text="name or email (prefix is enough)",
            fg_color=C_FIELD, border_color=C_BORDER, text_color=C_TEXT,
        )
        self.query_entry.grid(row=0, column=2, sticky="ew", padx=10, pady=12)
        self.query_entry.bind("<Return>", lambda _e: self._on_search())

        self.search_btn = ctk.CTkButton(
            card, text="Search", width=110,
            fg_color=C_SELECTED, hover_color="#1d4ed8",
            command=self._on_search,
        )
        self.search_btn.grid(row=0, column=3, padx=(0, 12), pady=12)

        self.search_status = ctk.CTkLabel(card, text="", text_color=C_DIM, anchor="w")
        self.search_status.grid(row=1, column=0, columnspan=4, sticky="ew",
                                padx=14, pady=(0, 4))

        self.results_frame = ctk.CTkScrollableFrame(card, fg_color=C_PANEL, height=100)
        self.results_frame.grid(row=2, column=0, columnspan=4, sticky="ew",
                                padx=10, pady=(0, 10))
        self.results_frame.grid_columnconfigure(0, weight=1)

        # ---- Profile card -----------------------------------------------
        profile_card = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=10)
        profile_card.grid(row=2, column=0, sticky="nsew", padx=16, pady=(6, 14))
        profile_card.grid_columnconfigure(0, weight=1, uniform="profcol")
        profile_card.grid_columnconfigure(1, weight=1, uniform="profcol")
        profile_card.grid_rowconfigure(8, weight=1)

        self.profile_title = ctk.CTkLabel(
            profile_card, text="No user loaded — search and select above.",
            text_color=C_DIM, anchor="w", font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.profile_title.grid(row=0, column=0, columnspan=2, sticky="ew",
                                padx=14, pady=(12, 2))
        self.profile_info = ctk.CTkLabel(
            profile_card, text="", text_color=C_DIM, anchor="w",
        )
        self.profile_info.grid(row=1, column=0, columnspan=2, sticky="ew",
                               padx=14, pady=(0, 8))

        # Editable fields, two uniform columns (the odd last field spans both)
        for i, (key, label) in enumerate(_EDIT_FIELDS):
            row, col = divmod(i, 2)
            last_and_odd = (i == len(_EDIT_FIELDS) - 1 and col == 0)
            holder = ctk.CTkFrame(profile_card, fg_color="transparent")
            holder.grid(
                row=2 + row, column=col,
                columnspan=2 if last_and_odd else 1,
                sticky="ew", padx=14, pady=(2, 6),
            )
            holder.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                holder, text=label, text_color=C_DIM, anchor="w",
                font=ctk.CTkFont(size=11),
            ).grid(row=0, column=0, sticky="ew", pady=(0, 1))
            entry = ctk.CTkEntry(
                holder, fg_color=C_FIELD, border_color=C_BORDER,
                text_color=C_TEXT, state="disabled", height=30,
            )
            entry.grid(row=1, column=0, sticky="ew")
            self._entries[key] = entry

        buttons = ctk.CTkFrame(profile_card, fg_color="transparent")
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 4))
        self.save_btn = ctk.CTkButton(
            buttons, text="Save changes", width=140,
            fg_color=C_SELECTED, hover_color="#1d4ed8",
            state="disabled", command=self._on_save,
        )
        self.save_btn.pack(side="left")
        self.reset_btn = ctk.CTkButton(
            buttons, text="Reset", width=80,
            fg_color=C_PANEL, hover_color="#2a3042", border_width=1,
            border_color=C_BORDER, state="disabled", command=self._reset_form,
        )
        self.reset_btn.pack(side="left", padx=(8, 0))
        self.syncro_lbl = ctk.CTkLabel(buttons, text="", text_color=C_DIM)
        self.syncro_lbl.pack(side="right")

        self.log = tk.Text(
            profile_card, wrap="word", font=FONT_MONO, state="disabled", height=6,
            bg=C_FIELD, fg=C_TEXT, insertbackground=C_TEXT,
            relief="flat", highlightthickness=0, padx=10, pady=6,
        )
        self.log.grid(row=8, column=0, columnspan=2, sticky="nsew",
                      padx=14, pady=(4, 12))
        for tag, color in (("ok", C_OK), ("fail", C_ERR), ("warn", C_WARN),
                           ("dim", C_DIM), ("head", C_SELECTED)):
            self.log.tag_configure(tag, foreground=color)

    # ------------------------------------------------------------------
    # Log + device-code helpers
    # ------------------------------------------------------------------

    def _append_log(self, text: str, tag: str | None = None):
        self.log.configure(state="normal")
        self.log.insert(tk.END, text + "\n", tag or ())
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _log_bg(self, text: str, tag: str | None = None):
        self.after(0, self._append_log, text, tag)

    def _show_device_code(self, user_code, verification_uri):
        if self._auth_dialog:
            try:
                self._auth_dialog.destroy()
            except tk.TclError:
                pass
        self._auth_dialog = DeviceCodeDialog(self.root, user_code, verification_uri)

    def _close_auth_dialog(self):
        if self._auth_dialog:
            try:
                self._auth_dialog.destroy()
            except tk.TclError:
                pass
            self._auth_dialog = None

    def _get_graph_token(self, rto: str):
        """Silent token, device-code fallback. Worker-thread only."""
        token = self.graph_mgr.get_token_silent(rto)
        if token:
            return token, None

        def on_code(code, url):
            self.after(0, self._show_device_code, code, url)

        token, err = self.graph_mgr.acquire_token(rto, on_code)
        self.after(0, self._close_auth_dialog)
        return token, err

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search(self):
        if self._busy:
            return
        query = self.query_entry.get().strip()
        rto = self.tenant_combo.get()
        if not query:
            messagebox.showwarning("Missing search", "Type a name or email first.", parent=self)
            return
        if not rto or rto == "—":
            messagebox.showwarning("No tenant", "Add tenants in Settings first.", parent=self)
            return

        self._busy = True
        self.search_btn.configure(state="disabled")
        self.search_status.configure(text=f"Searching {rto} for '{query}' ...")
        self._clear_results()
        threading.Thread(target=self._search_worker, args=(rto, query), daemon=True).start()

    def _search_worker(self, rto: str, query: str):
        token, err = self._get_graph_token(rto)
        if not token:
            self.after(0, self._search_done, f"Sign-in failed: {err}", 0)
            return
        try:
            users = self.graph_mgr.search_users(token, query)
        except Exception as exc:
            self.after(0, self._search_done, f"Search failed: {exc}", 0)
            return
        for user in users:
            user["rto"] = rto
            self.after(0, self._add_result_row, user)
        self.after(0, self._search_done,
                   f"{len(users)} user(s) found." if users else "No users found.",
                   len(users))

    def _search_done(self, status: str, _count: int):
        self._busy = False
        self.search_btn.configure(state="normal")
        self.search_status.configure(text=status)

    def _clear_results(self):
        self._results = []
        for row in self._result_rows:
            row.destroy()
        self._result_rows = []

    def _add_result_row(self, user: dict):
        index = len(self._results)
        self._results.append(user)

        row = ctk.CTkFrame(self.results_frame, fg_color=C_FIELD, corner_radius=8,
                           border_width=1, border_color=C_BORDER)
        row.grid(row=index, column=0, sticky="ew", pady=2, padx=2)
        row.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            row, text="Select", width=64,
            fg_color=C_SELECTED, hover_color="#1d4ed8",
            command=lambda i=index: self._load_profile(i),
        ).grid(row=0, column=0, padx=8, pady=6)
        job = f" — {user['jobTitle']}" if user.get("jobTitle") else ""
        ctk.CTkLabel(
            row, text=f"{user['displayName']}  <{user['upn']}>{job}",
            text_color=C_TEXT, anchor="w",
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8))

    # ------------------------------------------------------------------
    # Profile load / display
    # ------------------------------------------------------------------

    def _load_profile(self, index: int):
        if self._busy:
            return
        user = self._results[index]
        self._busy = True
        self.search_status.configure(text=f"Loading profile for {user['upn']} ...")
        threading.Thread(target=self._load_profile_worker, args=(user,), daemon=True).start()

    def _load_profile_worker(self, user: dict):
        rto = user["rto"]
        token, err = self._get_graph_token(rto)
        if not token:
            self.after(0, self._profile_load_done, None, rto, f"Sign-in failed: {err}", [])
            return
        try:
            profile = self.graph_mgr.get_user_profile(token, user["id"])
        except Exception as exc:
            self.after(0, self._profile_load_done, None, rto, f"Profile load failed: {exc}", [])
            return
        licenses = []
        if profile:
            try:
                licenses = self.graph_mgr.get_user_licenses(token, profile["id"])
            except Exception:
                pass
        self.after(0, self._profile_load_done, profile, rto, None, licenses)

    def _profile_load_done(self, profile, rto, error, licenses):
        self._busy = False
        if error or not profile:
            self.search_status.configure(text=error or "User not found.")
            return
        self.search_status.configure(text="")

        self._profile = profile
        self._profile_rto = rto
        signin = "Enabled" if profile.get("accountEnabled") else "Blocked"
        lic_names = ", ".join(l["name"] for l in licenses) or "none"
        self.profile_title.configure(
            text=f"{profile['displayName']}  <{profile['upn']}>", text_color=C_TEXT
        )
        self.profile_info.configure(
            text=f"Tenant: {rto}   Sign-in: {signin}   Licenses: {lic_names}"
        )

        syncro_rto = self._rto_for_domain(profile["upn"])
        org_id = self.config.get(syncro_rto, {}).get("syncro_org_id") if syncro_rto else None
        self.syncro_lbl.configure(
            text=(f"Syncro sync: on ({syncro_rto})" if org_id
                  else "Syncro sync: off for this tenant")
        )

        self._reset_form()
        self.save_btn.configure(state="normal")
        self.reset_btn.configure(state="normal")
        self._append_log(f"Loaded {profile['displayName']} <{profile['upn']}> from {rto}.", "head")

    def _form_value(self, key: str) -> str:
        if not self._profile:
            return ""
        if key == "manager_upn":
            manager = self._profile.get("manager")
            return manager["upn"] if manager else ""
        if key == "city":
            return self._profile.get("city") or self._profile.get("officeLocation") or ""
        return self._profile.get(key, "")

    def _reset_form(self):
        for key, entry in self._entries.items():
            entry.configure(state="normal")
            entry.delete(0, tk.END)
            entry.insert(0, self._form_value(key))

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _rto_for_domain(self, upn: str) -> str | None:
        """Config entry whose domain matches the UPN's domain."""
        domain = (upn.split("@", 1)[1] if "@" in upn else "").lower()
        if not domain:
            return None
        for rto, cfg in self.config.items():
            if (cfg.get("domain") or "").lower() == domain:
                return rto
        return None

    def _on_save(self):
        if self._busy or not self._profile:
            return

        edited = {key: entry.get().strip() for key, entry in self._entries.items()}
        changes = {
            key: value for key, value in edited.items()
            if value != self._form_value(key)
        }
        if not changes:
            self._append_log("No changes to save.", "dim")
            return

        for key in ("displayName", "upn"):
            if key in changes and not changes[key]:
                messagebox.showwarning(
                    "Required field",
                    "Display name and email can't be empty.", parent=self,
                )
                return

        if "upn" in changes:
            if "@" not in changes["upn"]:
                messagebox.showwarning(
                    "Invalid email", "Email must be a full address (user@domain).",
                    parent=self,
                )
                return
            if not messagebox.askyesno(
                "Change sign-in address?",
                f"You are renaming the sign-in address:\n\n"
                f"  {self._profile['upn']}\n  →  {changes['upn']}\n\n"
                "The user must sign in with the NEW address afterwards; the old "
                "one stops working unless kept as an alias. Continue?",
                parent=self,
            ):
                return

        self._busy = True
        self.save_btn.configure(state="disabled")
        threading.Thread(
            target=self._save_worker, args=(dict(self._profile), changes),
            daemon=True,
        ).start()

    def _save_worker(self, old_profile: dict, changes: dict):
        rto = self._profile_rto
        results_ok = True
        token, err = self._get_graph_token(rto)
        if not token:
            self._log_bg(f"[FAIL] Sign-in failed: {err}", "fail")
            self.after(0, self._save_done, False)
            return

        user_id = old_profile["id"]

        # --- Graph: simple fields ------------------------------------
        graph_fields = {}
        for key in ("displayName", "jobTitle", "department", "mobilePhone"):
            if key in changes:
                graph_fields[key] = changes[key] or None
        if "city" in changes:
            # Keep both Graph location fields in step with the one City box.
            graph_fields["city"] = changes["city"] or None
            graph_fields["officeLocation"] = changes["city"] or None
        if "upn" in changes:
            graph_fields["userPrincipalName"] = changes["upn"]

        if graph_fields:
            try:
                self.graph_mgr.update_user(token, user_id, graph_fields)
                pretty = ", ".join(sorted(k for k in graph_fields))
                self._log_bg(f"[ok]   Updated in Microsoft 365: {pretty}", "ok")
            except Exception as exc:
                results_ok = False
                self._log_bg(f"[FAIL] Microsoft 365 update: {exc}", "fail")

        # --- Graph: manager -------------------------------------------
        if "manager_upn" in changes:
            manager_raw = changes["manager_upn"]
            try:
                if not manager_raw:
                    self.graph_mgr.remove_manager_graph(token, user_id)
                    self._log_bg("[ok]   Manager removed", "ok")
                else:
                    domain = old_profile["upn"].split("@", 1)[1]
                    manager_upn = (manager_raw if "@" in manager_raw
                                   else f"{manager_raw}@{domain}")
                    manager = self.graph_mgr.get_user_offboard_info(token, manager_upn)
                    if not manager:
                        raise RuntimeError(f"manager '{manager_upn}' not found")
                    self.graph_mgr.set_manager(token, user_id, manager["id"])
                    self._log_bg(f"[ok]   Manager set to {manager['displayName']}", "ok")
            except Exception as exc:
                results_ok = False
                self._log_bg(f"[FAIL] Manager update: {exc}", "fail")

        # --- Syncro sync (email, display name, job title, city) --------
        syncro_changed = {
            k: v for k, v in changes.items()
            if k in ("displayName", "upn", "jobTitle", "city")
        }
        if syncro_changed:
            self._sync_to_syncro(old_profile, syncro_changed)

        self.after(0, self._save_done, results_ok)

    def _sync_to_syncro(self, old_profile: dict, changes: dict):
        syncro_rto = self._rto_for_domain(old_profile["upn"])
        org_id = self.config.get(syncro_rto, {}).get("syncro_org_id") if syncro_rto else None
        if not org_id:
            self._log_bg("[dim]  Syncro: tenant has no organization id — skipped.", "dim")
            return
        client = SyncroClient.from_env()
        if not client:
            self._log_bg("[warn] Syncro not configured (Settings) — skipped.", "warn")
            return

        contact, err = client.find_contact(org_id, old_profile["upn"])
        if err:
            self._log_bg(f"[warn] Syncro lookup failed: {err}", "warn")
            return
        if not contact:
            self._log_bg(
                f"[warn] Syncro: no contact with email {old_profile['upn']} "
                f"in organization {org_id} — nothing updated.", "warn",
            )
            return

        field_map = {"displayName": "name", "upn": "email",
                     "jobTitle": "title", "city": "city"}
        payload = {field_map[k]: v for k, v in changes.items()}
        err = client.update_contact(contact.get("id"), payload)
        if err:
            self._log_bg(f"[warn] Syncro update failed: {err}", "warn")
        else:
            self._log_bg(
                f"[ok]   Syncro contact updated: {', '.join(sorted(payload))}", "ok"
            )

    def _save_done(self, ok: bool):
        self._busy = False
        self.save_btn.configure(state="normal")
        if not self._profile:
            return
        # Reload the profile so the form reflects what actually saved.
        user = {"id": self._profile["id"], "rto": self._profile_rto,
                "upn": self._profile["upn"]}
        if ok:
            self._append_log("Save complete — reloading profile.", "head")
        else:
            self._append_log("Save finished with errors — reloading profile.", "warn")
        self._busy = True
        threading.Thread(target=self._load_profile_worker, args=(user,), daemon=True).start()
