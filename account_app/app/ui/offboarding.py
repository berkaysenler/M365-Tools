"""Staff Offboarding section.

Search a staff alias across every configured tenant via Microsoft Graph,
pick the matching account, choose which offboarding steps to run (including
selective license removal and an optional password reset), then execute:

  Exchange Online : display-name suffix, auto-reply, forwarding,
                    remove manager, remove all groups, mail-protocol lockout
  Microsoft Graph : block sign-in (accountEnabled=false), remove licenses,
                    optional random password reset

Reuses the shared per-tenant Exchange sessions (SharedRTOState) when a
tenant is already connected in Account Creation; otherwise connects on
demand and stores the session back for the other sections.
"""

import csv
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

from app.account import generate_password
from app.auth import TokenManager
from app.config_loader import load_config
from app.exchange import ExchangeSession
from app.graph import GraphManager
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
from portable_paths import LOGS_DIR

OFFBOARD_LOG_PATH = LOGS_DIR / "offboarding_log.csv"
OFFBOARD_LOG_FIELDS = [
    "Date", "Tenant", "Domain", "Mailbox", "DisplayName",
    "StepsDone", "RemovedLicenses", "PasswordReset", "Failures",
]

FONT_MONO = ("Consolas", 10)


def _normalize_alias(raw: str) -> str:
    """Accept 'alias', '@alias' or a full pasted address."""
    return (raw or "").strip().lstrip("@").split("@")[0].strip()


class _State:
    """Fallback shared-session record when the shell didn't pass one."""

    def __init__(self):
        self.session = None
        self.connected = False


class OffboardingSection(ctk.CTkFrame):
    def __init__(self, parent, session_states=None):
        super().__init__(parent, corner_radius=0, fg_color=C_BG)
        self.root = self.winfo_toplevel()

        self.config = load_config()
        self.token_mgr = TokenManager(self.config)
        self.graph_mgr = GraphManager(self.config)
        self._states = session_states if session_states is not None else {}

        self._results: list[dict] = []
        self._result_rows: list[ctk.CTkFrame] = []
        self._result_buttons: list[ctk.CTkButton] = []
        self._selected: set[int] = set()
        # index -> [(BooleanVar, license dict), ...] for each selected account
        self._license_vars: dict[int, list[tuple[tk.BooleanVar, dict]]] = {}
        self._auth_dialog = None
        self._busy = False
        self._summary_text = ""

        self._build()

    # ------------------------------------------------------------------
    # Tenant helpers (managed RTOs are extra domains inside the parent
    # tenant — one connection per top-level tenant covers them all)
    # ------------------------------------------------------------------

    def _top_level_tenants(self) -> dict:
        managed = {
            child for cfg in self.config.values()
            for child in cfg.get("manages", [])
        }
        return {
            rto: cfg for rto, cfg in self.config.items()
            if rto not in managed and cfg.get("domain")
        }

    def _domains_for(self, rto: str, cfg: dict) -> list[tuple[str, str]]:
        pairs = [(rto, cfg["domain"])]
        for child in cfg.get("manages", []):
            child_cfg = self.config.get(child, {})
            if child_cfg.get("domain"):
                pairs.append((child, child_cfg["domain"]))
        return pairs

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ---- Header ----------------------------------------------------
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            header, text="Staff Offboarding", text_color=C_TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="Search the alias across all tenants, confirm the account, choose the steps, run.",
            text_color=C_DIM, font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(14, 0))

        # ---- Search card -----------------------------------------------
        search_card = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=10)
        search_card.grid(row=1, column=0, sticky="ew", padx=16, pady=6)
        search_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            search_card, text="Staff alias (the part before @):",
            text_color=C_TEXT,
        ).grid(row=0, column=0, padx=(14, 8), pady=12, sticky="w")
        self.alias_entry = ctk.CTkEntry(
            search_card, placeholder_text="e.g. j.smith",
            fg_color=C_FIELD, border_color=C_BORDER, text_color=C_TEXT,
        )
        self.alias_entry.grid(row=0, column=1, sticky="ew", pady=12)
        self.alias_entry.bind("<Return>", lambda _e: self._on_search())
        self.search_btn = ctk.CTkButton(
            search_card, text="Search all tenants", width=150,
            fg_color=C_SELECTED, hover_color="#1d4ed8",
            command=self._on_search,
        )
        self.search_btn.grid(row=0, column=2, padx=12, pady=12)

        self.search_status = ctk.CTkLabel(
            search_card, text="", text_color=C_DIM, anchor="w",
        )
        self.search_status.grid(row=1, column=0, columnspan=3, sticky="ew",
                                padx=14, pady=(0, 4))

        self.results_frame = ctk.CTkScrollableFrame(
            search_card, fg_color=C_PANEL, height=110,
        )
        self.results_frame.grid(row=2, column=0, columnspan=3, sticky="ew",
                                padx=10, pady=(0, 10))
        self.results_frame.grid_columnconfigure(0, weight=1)

        # ---- Options card (left: steps, right: licenses + progress log) --
        options_card = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=10)
        options_card.grid(row=2, column=0, sticky="nsew", padx=16, pady=(6, 14))
        options_card.grid_columnconfigure(0, weight=3)
        options_card.grid_columnconfigure(1, weight=2)
        options_card.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(options_card, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(14, 8), pady=10)
        left.grid_columnconfigure(0, weight=1)

        self.opt_suffix = tk.BooleanVar(value=True)
        self.opt_autoreply = tk.BooleanVar(value=True)
        self.opt_forward = tk.BooleanVar(value=False)
        self.opt_deliver = tk.BooleanVar(value=True)
        self.opt_manager = tk.BooleanVar(value=True)
        self.opt_groups = tk.BooleanVar(value=True)
        self.opt_block = tk.BooleanVar(value=True)
        self.opt_password = tk.BooleanVar(value=False)

        def check(parent, text, var, row):
            box = ctk.CTkCheckBox(
                parent, text=text, variable=var, text_color=C_TEXT,
                fg_color=C_SELECTED, hover_color="#1d4ed8",
                border_color=C_BORDER, checkbox_height=18, checkbox_width=18,
            )
            box.grid(row=row, column=0, sticky="w", pady=3)
            return box

        check(left, 'Append " --Offboarding" to display name', self.opt_suffix, 0)
        check(left, "Set auto-reply (message below)", self.opt_autoreply, 1)
        self.autoreply_box = ctk.CTkTextbox(
            left, height=70, fg_color=C_FIELD, text_color=C_TEXT,
            border_color=C_BORDER, border_width=1, wrap="word",
        )
        self.autoreply_box.grid(row=2, column=0, sticky="ew", pady=(2, 6), padx=(24, 0))

        check(left, "Forward mail to:", self.opt_forward, 3)
        fwd_row = ctk.CTkFrame(left, fg_color="transparent")
        fwd_row.grid(row=4, column=0, sticky="ew", padx=(24, 0), pady=(2, 2))
        fwd_row.grid_columnconfigure(0, weight=1)
        self.forward_entry = ctk.CTkEntry(
            fwd_row, placeholder_text="alias, or a full external address",
            fg_color=C_FIELD, border_color=C_BORDER, text_color=C_TEXT,
        )
        self.forward_entry.grid(row=0, column=0, sticky="ew")
        self.forward_domain_lbl = ctk.CTkLabel(
            fwd_row,
            text="alias → @account's own domain; full address (with @) used as-is",
            text_color=C_DIM,
        )
        self.forward_domain_lbl.grid(row=0, column=1, padx=(6, 0))
        deliver_box = ctk.CTkCheckBox(
            left, text="Keep a copy in the mailbox (deliver and forward)",
            variable=self.opt_deliver, text_color=C_DIM,
            fg_color=C_SELECTED, hover_color="#1d4ed8",
            border_color=C_BORDER, checkbox_height=18, checkbox_width=18,
        )
        deliver_box.grid(row=5, column=0, sticky="w", padx=(24, 0), pady=(0, 6))

        check(left, "Remove manager", self.opt_manager, 6)
        check(left, "Remove all group memberships", self.opt_groups, 7)
        check(left, "Block sign-in + disable mail protocols", self.opt_block, 8)
        check(left, "Reset password to a random value (shown once in the summary)",
              self.opt_password, 9)

        right = ctk.CTkFrame(options_card, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 14), pady=10)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(
            right, text="Licenses to remove, per selected account (untick any to keep):",
            text_color=C_TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        self.licenses_frame = ctk.CTkScrollableFrame(
            right, fg_color=C_FIELD, height=140,
        )
        self.licenses_frame.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self._license_placeholder = ctk.CTkLabel(
            self.licenses_frame,
            text="Select one or more accounts above to load their licenses.",
            text_color=C_DIM,
        )
        self._license_placeholder.pack(anchor="w", padx=8, pady=8)

        ctk.CTkLabel(
            right, text="Progress:", text_color=C_TEXT, anchor="w",
        ).grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.log = tk.Text(
            right, wrap="word", font=FONT_MONO, state="disabled", height=8,
            bg=C_FIELD, fg=C_TEXT, insertbackground=C_TEXT,
            relief="flat", highlightthickness=0, padx=10, pady=8,
        )
        self.log.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
        for tag, color in (
            ("ok", C_OK), ("fail", C_ERR), ("warn", C_WARN),
            ("dim", C_DIM), ("head", C_SELECTED),
        ):
            self.log.tag_configure(tag, foreground=color)

        run_row = ctk.CTkFrame(options_card, fg_color="transparent")
        run_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 12))
        self.run_btn = ctk.CTkButton(
            run_row, text="Run Offboarding", width=170,
            fg_color="#b91c1c", hover_color="#991b1b",
            state="disabled", command=self._on_run,
        )
        self.run_btn.pack(side="left")
        ctk.CTkButton(
            run_row, text="Clear", width=80,
            fg_color=C_PANEL, hover_color="#2a3042", border_width=1,
            border_color=C_BORDER, command=self._clear,
        ).pack(side="left", padx=(8, 0))
        self.copy_btn = ctk.CTkButton(
            run_row, text="Copy Summary", width=120,
            fg_color=C_PANEL, hover_color="#2a3042", border_width=1,
            border_color=C_BORDER, state="disabled", command=self._copy_summary,
        )
        self.copy_btn.pack(side="right")

    # ------------------------------------------------------------------
    # Logging helpers (UI thread only)
    # ------------------------------------------------------------------

    def _append_log(self, text: str, tag: str | None = None):
        self.log.configure(state="normal")
        self.log.insert(tk.END, text + "\n", tag or ())
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _log_bg(self, text: str, tag: str | None = None):
        """Append to the log from a worker thread."""
        self.after(0, self._append_log, text, tag)

    # ------------------------------------------------------------------
    # Device-code dialog plumbing
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_alias(self, alias: str):
        """Prefill the alias box and run the search (used by HR Tasks)."""
        self.alias_entry.delete(0, tk.END)
        self.alias_entry.insert(0, alias)
        self._on_search()

    def _on_search(self):
        if self._busy:
            return
        alias = _normalize_alias(self.alias_entry.get())
        if not alias:
            messagebox.showwarning("Missing alias", "Enter the staff alias first.", parent=self)
            return
        tenants = self._top_level_tenants()
        if not tenants:
            messagebox.showerror(
                "No tenants",
                "No tenants with a domain configured. Add accounts in Settings first.",
                parent=self,
            )
            return

        self._clear_results()
        self._busy = True
        self.search_btn.configure(state="disabled")
        self.search_status.configure(text=f"Searching for '{alias}' ...")
        self._append_log(f"Searching all tenants for alias '{alias}' ...", "head")
        threading.Thread(target=self._search_worker, args=(alias, tenants),
                         daemon=True).start()

    def _search_worker(self, alias: str, tenants: dict):
        found = 0
        for rto, cfg in tenants.items():
            token = self.graph_mgr.get_token_silent(rto)
            if not token:
                def on_code(code, url):
                    self.after(0, self._show_device_code, code, url)
                token, err = self.graph_mgr.acquire_token(rto, on_code)
                self.after(0, self._close_auth_dialog)
                if err or not token:
                    self._log_bg(f"[{rto}] Skipped — sign-in unavailable: {err}", "warn")
                    continue

            for label, domain in self._domains_for(rto, cfg):
                target = f"{alias}@{domain}"
                try:
                    info = self.graph_mgr.get_user_offboard_info(token, target)
                except Exception as exc:
                    self._log_bg(f"[{label}] Lookup failed for {target}: {exc}", "warn")
                    continue
                if not info:
                    self._log_bg(f"[{label}] Not found: {target}", "dim")
                    continue
                try:
                    info["licenses"] = self.graph_mgr.get_user_licenses(token, info["id"])
                except Exception as exc:
                    info["licenses"] = []
                    self._log_bg(f"[{label}] Could not read licenses: {exc}", "warn")
                info.update(tenant=label, auth_rto=rto, domain=domain)
                found += 1
                self._log_bg(
                    f"[{label}] FOUND {info['displayName']} <{info['upn']}>", "ok"
                )
                self.after(0, self._add_result_row, info)

        self.after(0, self._search_done, alias, found)

    def _search_done(self, alias: str, found: int):
        self._busy = False
        self.search_btn.configure(state="normal")
        if found:
            self.search_status.configure(
                text=f"{found} account(s) found — select one below, then choose the steps."
            )
        else:
            self.search_status.configure(
                text=f"No account found for '{alias}' in any tenant."
            )
            self._append_log(f"No account found for '{alias}'.", "warn")

    def _clear_results(self):
        self._results = []
        self._selected = set()
        for row in self._result_rows:
            row.destroy()
        self._result_rows = []
        self._result_buttons = []
        self._reset_license_list()
        self.run_btn.configure(state="disabled")

    def _add_result_row(self, info: dict):
        index = len(self._results)
        self._results.append(info)

        enabled = info.get("accountEnabled")
        signin = "Enabled" if enabled else ("Blocked" if enabled is not None else "?")
        lic_names = ", ".join(l["name"] for l in info.get("licenses", [])) or "none"
        job = f" — {info['jobTitle']}" if info.get("jobTitle") else ""

        row = ctk.CTkFrame(self.results_frame, fg_color=C_FIELD, corner_radius=8,
                           border_width=1, border_color=C_BORDER)
        row.grid(row=index, column=0, sticky="ew", pady=3, padx=2)
        row.grid_columnconfigure(1, weight=1)

        pick = ctk.CTkButton(
            row, text="Select", width=76,
            fg_color=C_PANEL, hover_color="#1d4ed8",
            border_width=1, border_color=C_BORDER,
            command=lambda i=index: self._toggle_result(i),
        )
        pick.grid(row=0, column=0, rowspan=2, padx=8, pady=8)
        self._result_buttons.append(pick)

        ctk.CTkLabel(
            row,
            text=f"{info['displayName']}  <{info['upn']}>{job}",
            text_color=C_TEXT, anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(6, 0))
        ctk.CTkLabel(
            row,
            text=(f"Tenant: {info['tenant']} ({info['domain']})   "
                  f"Sign-in: {signin}   Licenses: {lic_names}"),
            text_color=C_DIM, anchor="w", font=ctk.CTkFont(size=11),
        ).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 6))

        self._result_rows.append(row)

    def _toggle_result(self, index: int):
        """Toggle an account in/out of the selection (multi-select)."""
        if index in self._selected:
            self._selected.discard(index)
        else:
            self._selected.add(index)

        for i, (row, btn) in enumerate(zip(self._result_rows, self._result_buttons)):
            selected = i in self._selected
            row.configure(border_color=C_SELECTED if selected else C_BORDER)
            btn.configure(
                text="Selected ✓" if selected else "Select",
                fg_color=C_SELECTED if selected else C_PANEL,
            )

        self._populate_license_list()
        self.run_btn.configure(state="normal" if self._selected else "disabled")

        info = self._results[index]
        verb = "Selected" if index in self._selected else "Deselected"
        self._append_log(
            f"{verb} {info['displayName']} <{info['upn']}> in {info['tenant']} "
            f"({len(self._selected)} account(s) selected).",
            "head",
        )

    # ------------------------------------------------------------------
    # License checkboxes
    # ------------------------------------------------------------------

    def _reset_license_list(self):
        for child in self.licenses_frame.winfo_children():
            child.destroy()
        self._license_vars = {}
        self._license_placeholder = ctk.CTkLabel(
            self.licenses_frame,
            text="Select one or more accounts above to load their licenses.",
            text_color=C_DIM,
        )
        self._license_placeholder.pack(anchor="w", padx=8, pady=8)

    def _populate_license_list(self):
        """One checkbox group per selected account. Existing tick states are
        kept when re-rendering after a selection change."""
        previous = {
            idx: {lic["skuId"]: var.get() for var, lic in vars_}
            for idx, vars_ in self._license_vars.items()
        }
        for child in self.licenses_frame.winfo_children():
            child.destroy()
        self._license_vars = {}

        if not self._selected:
            self._reset_license_list()
            return

        for idx in sorted(self._selected):
            info = self._results[idx]
            ctk.CTkLabel(
                self.licenses_frame,
                text=f"{info['displayName']}  <{info['upn']}>",
                text_color=C_TEXT, font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(anchor="w", padx=8, pady=(8, 2))
            licenses = info.get("licenses", [])
            if not licenses:
                ctk.CTkLabel(
                    self.licenses_frame, text="No licenses assigned.",
                    text_color=C_DIM,
                ).pack(anchor="w", padx=20, pady=(0, 4))
                self._license_vars[idx] = []
                continue
            vars_ = []
            for lic in licenses:
                kept = previous.get(idx, {}).get(lic["skuId"], True)
                var = tk.BooleanVar(value=kept)
                box = ctk.CTkCheckBox(
                    self.licenses_frame, text=lic["name"], variable=var,
                    text_color=C_TEXT, fg_color=C_SELECTED, hover_color="#1d4ed8",
                    border_color=C_BORDER, checkbox_height=18, checkbox_width=18,
                )
                box.pack(anchor="w", padx=20, pady=2)
                vars_.append((var, lic))
            self._license_vars[idx] = vars_

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_forward(raw: str, domain: str) -> str:
        """Full address (contains @) is used as-is — external domains OK.
        A bare alias gets the account's own tenant domain appended."""
        raw = (raw or "").strip()
        if not raw:
            return ""
        if "@" in raw:
            return raw.lstrip("@") if raw.startswith("@") else raw
        return f"{raw}@{domain}"

    def _on_run(self):
        if self._busy or not self._selected:
            return
        targets = []
        for idx in sorted(self._selected):
            info = dict(self._results[idx])
            info["remove_skus"] = [
                lic for var, lic in self._license_vars.get(idx, []) if var.get()
            ]
            targets.append(info)

        forward_raw = self.forward_entry.get().strip() if self.opt_forward.get() else ""
        opts = {
            "suffix": self.opt_suffix.get(),
            "auto_reply": (self.autoreply_box.get("1.0", tk.END).strip()
                           if self.opt_autoreply.get() else ""),
            "forward_raw": forward_raw,
            "deliver_and_forward": self.opt_deliver.get(),
            "remove_manager": self.opt_manager.get(),
            "remove_groups": self.opt_groups.get(),
            "block_signin": self.opt_block.get(),
            "reset_password": self.opt_password.get(),
        }

        if self.opt_autoreply.get() and not opts["auto_reply"]:
            messagebox.showwarning(
                "Auto-reply empty",
                "Auto-reply is ticked but the message is empty. "
                "Type a message or untick auto-reply.",
                parent=self,
            )
            return
        if self.opt_forward.get() and not forward_raw:
            messagebox.showwarning(
                "Forwarding address missing",
                "Forwarding is ticked but no alias or address was entered.",
                parent=self,
            )
            return

        lines = [f"Offboard {len(targets)} account(s):", ""]
        for info in targets:
            lic_names = ", ".join(l["name"] for l in info["remove_skus"]) or "none"
            fwd = self._resolve_forward(forward_raw, info["domain"]) or "no"
            lines.append(f"• {info['displayName']} <{info['upn']}> — {info['tenant']}")
            lines.append(f"    forward: {fwd} | remove licenses: {lic_names}")
        lines += [
            "",
            f"Steps for every account: "
            f"suffix={'yes' if opts['suffix'] else 'no'}, "
            f"auto-reply={'yes' if opts['auto_reply'] else 'no'}, "
            f"manager={'remove' if opts['remove_manager'] else 'keep'}, "
            f"groups={'remove' if opts['remove_groups'] else 'keep'}, "
            f"block sign-in={'yes' if opts['block_signin'] else 'no'}, "
            f"password reset={'yes' if opts['reset_password'] else 'no'}",
            "",
            "This cannot be undone from here. Proceed?",
        ]
        if not messagebox.askyesno("Confirm offboarding", "\n".join(lines), parent=self):
            return

        self._busy = True
        self._summary_text = ""
        self.copy_btn.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.search_btn.configure(state="disabled")
        threading.Thread(target=self._execute_all_worker, args=(targets, opts),
                         daemon=True).start()

    # ------------------------------------------------------------------
    # Execution (worker thread)
    # ------------------------------------------------------------------

    def _ensure_exchange_session(self, auth_rto: str):
        """Return (session, error). Reuses a shared connected session or
        connects on demand (device code + EXO connect, both awaited)."""
        state = self._states.get(auth_rto)
        if state is not None and getattr(state, "connected", False) and state.session:
            self._log_bg(f"[{auth_rto}] Using shared Exchange connection.", "dim")
            return state.session, None

        cfg = self.config.get(auth_rto, {})
        admin = cfg.get("admin", "").strip()
        if not admin:
            return None, f"No admin email configured for {auth_rto}."

        self._log_bg(f"[{auth_rto}] Connecting to Exchange Online as {admin} ...", "dim")

        token_result = {}
        got_token = threading.Event()

        def on_code(code, url):
            self.after(0, self._show_device_code, code, url)

        def on_token(token, error):
            token_result["token"] = token
            token_result["error"] = error
            got_token.set()

        self.token_mgr.acquire_token_device_code(auth_rto, on_code, on_token)
        got_token.wait(timeout=600)
        self.after(0, self._close_auth_dialog)
        token = token_result.get("token")
        if not token:
            return None, token_result.get("error") or "Sign-in timed out."

        session = ExchangeSession(hide_window=True)
        conn_result = {}
        connected = threading.Event()
        session.connect(
            admin,
            lambda code, url: None,
            lambda err: (conn_result.update(err=err), connected.set()),
            access_token=token,
        )
        connected.wait(timeout=600)
        if conn_result.get("err"):
            try:
                session.disconnect()
            except Exception:
                pass
            return None, conn_result["err"]
        if not connected.is_set():
            return None, "Exchange Online connection timed out."

        def _store():
            st = self._states.get(auth_rto)
            if st is None:
                new_state = _State()
                new_state.session = session
                new_state.connected = True
                self._states[auth_rto] = new_state
            else:
                st.session = session
                st.connected = True

        self.after(0, _store)
        return session, None

    def _execute_all_worker(self, targets: list[dict], opts: dict):
        """Offboard every selected account in sequence. Exchange sessions and
        Graph tokens are cached per top-level tenant so accounts in the same
        tenant share one connection/sign-in."""
        sessions: dict[str, object] = {}
        session_errors: dict[str, str] = {}
        graph_tokens: dict[str, str | None] = {}
        outcomes: list[tuple[dict, list, str | None]] = []

        for info in targets:
            self._log_bg("", None)
            self._log_bg(
                f"=== Offboarding {info['displayName']} <{info['upn']}> "
                f"({info['tenant']}) ===", "head",
            )
            results, password = self._execute_one(
                info, opts, sessions, session_errors, graph_tokens
            )
            self._write_csv_log(info, results, password is not None)
            outcomes.append((info, results, password))

        self.after(0, self._show_summary, outcomes)

    def _get_graph_token_cached(self, auth_rto: str, cache: dict) -> str | None:
        if auth_rto in cache:
            return cache[auth_rto]
        token = self.graph_mgr.get_token_silent(auth_rto)
        if not token:
            def on_code(code, url):
                self.after(0, self._show_device_code, code, url)
            token, err = self.graph_mgr.acquire_token(auth_rto, on_code)
            self.after(0, self._close_auth_dialog)
            if not token:
                self._log_bg(f"[FAIL] Graph sign-in for {auth_rto}: {err}", "fail")
        cache[auth_rto] = token
        return token

    def _execute_one(self, info: dict, opts: dict, sessions: dict,
                     session_errors: dict, graph_tokens: dict):
        results: list[tuple[str, bool, str]] = []  # (label, ok, detail)
        password: str | None = None

        def step(label, fn):
            try:
                detail = fn()
                results.append((label, True, str(detail) if detail else ""))
                self._log_bg(f"[ok]   {label}", "ok")
            except Exception as exc:
                results.append((label, False, str(exc)))
                self._log_bg(f"[FAIL] {label}: {exc}", "fail")

        auth_rto = info["auth_rto"]
        upn = info["upn"]
        domain = info["domain"]
        remove_skus = info.get("remove_skus", [])
        forward_to = self._resolve_forward(opts.get("forward_raw", ""), domain)

        # --- Exchange Online steps ---------------------------------------
        needs_exchange = any([
            opts["suffix"], opts["auto_reply"], forward_to,
            opts["remove_manager"], opts["remove_groups"], opts["block_signin"],
        ])
        session = None
        if needs_exchange:
            if auth_rto in sessions:
                session = sessions[auth_rto]
            elif auth_rto in session_errors:
                results.append(("Exchange connection", False, session_errors[auth_rto]))
            else:
                session, err = self._ensure_exchange_session(auth_rto)
                if session:
                    sessions[auth_rto] = session
                else:
                    session_errors[auth_rto] = str(err)
                    self._log_bg(f"[FAIL] Exchange connection: {err}", "fail")
                    results.append(("Exchange connection", False, str(err)))

        identity = upn
        dn = None
        display = info.get("displayName", "")
        if session:
            mbx = session.get_mailbox(upn)
            if mbx:
                identity = mbx.get("primarySmtp") or upn
                dn = mbx.get("dn")
                display = mbx.get("displayName") or display
            else:
                self._log_bg(f"[warn] Mailbox not found for {upn}; "
                             "Exchange steps may fail.", "warn")

            if opts["suffix"]:
                step("Append ' --Offboarding' to display name",
                     lambda: session.append_display_name_suffix(identity, display))
            if opts["auto_reply"]:
                step("Set auto-reply",
                     lambda: session.set_auto_reply(identity, opts["auto_reply"]))
            if forward_to:
                step(f"Forward mail to {forward_to}",
                     lambda: session.set_forwarding(
                         identity, forward_to, opts["deliver_and_forward"]))
            if opts["remove_manager"]:
                step("Remove manager", lambda: session.remove_manager(identity))
            if opts["remove_groups"]:
                def _groups():
                    out = session.remove_all_groups(
                        identity, dn=dn,
                        progress=lambda m: self._log_bg(f"      {m}", "dim"),
                    )
                    for line in out:
                        tag = "fail" if line.startswith("FAILED") else "dim"
                        self._log_bg(f"      {line}", tag)
                    removed = sum(1 for l in out if l.startswith("Removed"))
                    return f"{removed} group(s) removed"
                step("Remove distribution/security groups (Exchange)", _groups)

        # --- Graph steps --------------------------------------------------
        needs_graph = (opts["block_signin"] or remove_skus
                       or opts["reset_password"] or opts["remove_groups"])
        graph_token = None
        if needs_graph:
            graph_token = self._get_graph_token_cached(auth_rto, graph_tokens)
            if not graph_token:
                results.append(("Microsoft Graph sign-in", False, "sign-in unavailable"))

        if graph_token:
            if opts["remove_groups"]:
                def _graph_groups():
                    # Microsoft 365 and plain security groups — EXO cmdlets
                    # either can't see these or (for Unified) needed a
                    # per-group scan that skipped silently on any error.
                    # One memberOf call, then direct membership deletes.
                    groups = self.graph_mgr.list_user_groups(graph_token, info["id"])
                    removed = 0
                    failed = 0
                    for g in groups:
                        gname = g.get("displayName") or g.get("id")
                        unified = "Unified" in (g.get("groupTypes") or [])
                        plain_security = g.get("securityEnabled") and not g.get("mailEnabled")
                        if not unified and not plain_security:
                            continue  # DLs/mail-security handled by the Exchange step
                        if g.get("membershipRule"):
                            self._log_bg(
                                f"      Skipped dynamic group (rule-based): {gname}", "dim")
                            continue
                        try:
                            self.graph_mgr.remove_group_member(
                                graph_token, g["id"], info["id"])
                            removed += 1
                            self._log_bg(f"      Removed from group: {gname}", "dim")
                        except Exception as exc:
                            failed += 1
                            self._log_bg(f"      FAILED to remove from {gname}: {exc}", "fail")
                    if failed:
                        raise RuntimeError(f"{removed} removed, {failed} failed")
                    return f"{removed} group(s) removed"
                step("Remove Microsoft 365/security groups (Graph)", _graph_groups)
            if opts["block_signin"]:
                step("Block sign-in (Entra accountEnabled=false)",
                     lambda: self.graph_mgr.set_account_enabled(graph_token, upn, False))
                if session:
                    step("Disable mail protocols (Exchange)",
                         lambda: session.block_protocols(identity))
            if remove_skus:
                sku_ids = [l["skuId"] for l in remove_skus]
                names = ", ".join(l["name"] for l in remove_skus)
                step(f"Remove licenses: {names}",
                     lambda: self.graph_mgr.remove_licenses(graph_token, upn, sku_ids))
            if opts["reset_password"]:
                new_pw = generate_password()
                def _reset():
                    self.graph_mgr.reset_password(graph_token, upn, new_pw)
                    return "password set"
                step("Reset password to a random value", _reset)
                if any(ok for label, ok, _ in results
                       if label.startswith("Reset password") and ok):
                    password = new_pw

        return results, password

    # ------------------------------------------------------------------
    # Summary + CSV log
    # ------------------------------------------------------------------

    def _show_summary(self, outcomes: list[tuple[dict, list, str | None]]):
        self._busy = False
        self.run_btn.configure(state="normal")
        self.search_btn.configure(state="normal")

        text_lines: list[str] = []
        self._append_log("", None)
        self._append_log(f"SUMMARY — {len(outcomes)} account(s)", "head")

        for info, results, password in outcomes:
            header = (f"{info['displayName']} <{info['upn']}> — "
                      f"{info['tenant']} ({info['domain']})")
            self._append_log(f"  {header}", "head")
            text_lines.append(header)

            ok_steps = [(l, d) for l, ok, d in results if ok]
            failures = [(l, d) for l, ok, d in results if not ok]
            for label, detail in ok_steps:
                suffix = f" — {detail}" if detail else ""
                self._append_log(f"    [ok]   {label}{suffix}", "ok")
                text_lines.append(f"  [ok]   {label}{suffix}")
            if password:
                self._append_log(
                    f"    [ok]   New password (copy now — shown once): {password}",
                    "warn",
                )
                text_lines.append(f"  New password: {password}")
            for label, detail in failures:
                self._append_log(f"    [FAIL] {label}: {detail}", "fail")
                text_lines.append(f"  [FAIL] {label}: {detail}")
            if not failures:
                self._append_log("    All requested steps completed.", "ok")
                text_lines.append("  All requested steps completed.")
            text_lines.append("")

        self._append_log(f"  Log: {OFFBOARD_LOG_PATH}", "dim")
        self._summary_text = "\n".join(text_lines).strip()
        self.copy_btn.configure(state="normal")

    def _copy_summary(self):
        if not self._summary_text:
            return
        self.clipboard_clear()
        self.clipboard_append(self._summary_text)

    def _write_csv_log(self, info: dict, results, password_reset: bool):
        """CSV audit row. Never logs the password itself, never raises."""
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            new_file = not OFFBOARD_LOG_PATH.exists()
            with OFFBOARD_LOG_PATH.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=OFFBOARD_LOG_FIELDS)
                if new_file:
                    writer.writeheader()
                done = " | ".join(l for l, ok, _ in results if ok)
                fails = " | ".join(f"{l}: {d}" for l, ok, d in results if not ok)
                removed = ""
                for label, ok, _ in results:
                    if ok and label.startswith("Remove licenses:"):
                        removed = label[len("Remove licenses:"):].strip()
                writer.writerow({
                    "Date": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "Tenant": info.get("tenant", ""),
                    "Domain": info.get("domain", ""),
                    "Mailbox": info.get("upn", ""),
                    "DisplayName": info.get("displayName", ""),
                    "StepsDone": done,
                    "RemovedLicenses": removed,
                    "PasswordReset": "yes" if password_reset else "",
                    "Failures": fails,
                })
        except Exception as exc:
            self._log_bg(f"[warn] Could not write offboarding log: {exc}", "warn")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _clear(self):
        if self._busy:
            return
        self.alias_entry.delete(0, tk.END)
        self.forward_entry.delete(0, tk.END)
        self.autoreply_box.delete("1.0", tk.END)
        self._clear_results()
        self.search_status.configure(text="")
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.configure(state="disabled")
        self._summary_text = ""
        self.copy_btn.configure(state="disabled")
