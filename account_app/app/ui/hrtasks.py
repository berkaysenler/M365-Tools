"""HR Tasks section.

Shows recent rows from the HR SharePoint sheets (app/hr_feed.py) as cards
with status/account tags — Onboarding and Offboarding feeds side by side
behind a segmented switch. Pick a task to see the HR-entered details, then:

  Onboarding : "Create Account" prefills the Account Creation form
               (manager name is resolved to a real account via Graph in
               the tenant guessed from "Account Required"; groups are
               shown but never auto-filled)
  Offboarding: "Find User" jumps to the Offboarding section and runs the
               all-tenant search for the staff email

First run shows a small setup card; the choices are saved to hr_feed.json
in the local app-data folder — never in the repo.
"""

import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from app import hr_feed
from app.config_loader import load_config
from app.graph import GraphManager
from app.ui.dialogs import DeviceCodeDialog
from app.ui.theme import (
    C_BG,
    C_BORDER,
    C_DIM,
    C_FIELD,
    C_OK,
    C_PANEL,
    C_PANEL_HOVER,
    C_SELECTED,
    C_TEXT,
    C_WARN,
)

# Tag chip palettes (bg, fg)
TAG_PENDING = ("#78350f", "#fcd34d")
TAG_DONE = ("#14532d", "#86efac")
TAG_EMAIL = ("#1e3a5f", "#93c5fd")
TAG_MOODLE = ("#3b1d5f", "#c4b5fd")
TAG_MISC = ("#334155", "#cbd5e1")


def _chip_palette(entry: str) -> tuple[str, str]:
    low = entry.casefold()
    if low.startswith("email"):
        return TAG_EMAIL
    if low.startswith("moodle"):
        return TAG_MOODLE
    return TAG_MISC


class HRTasksSection(ctk.CTkFrame):
    def __init__(self, parent, session_states=None, on_badge=None,
                 on_prefill=None, on_offboard=None, on_bulk=None):
        super().__init__(parent, corner_radius=0, fg_color=C_BG)
        self.root = self.winfo_toplevel()

        self.config = load_config()
        self.graph_mgr = GraphManager(self.config)
        self.on_badge = on_badge
        self.on_prefill = on_prefill
        self.on_offboard = on_offboard
        self.on_bulk = on_bulk

        self._feed_cfg = hr_feed.load_feed_config()
        self._kind = "onboarding"
        self._tasks: dict[str, list[dict]] = {"onboarding": [], "offboarding": []}
        self._selected_key: str | None = None
        self._cards: list[tuple[ctk.CTkFrame, dict]] = []
        self._busy = False
        self._auth_dialog = None

        self._build()

    # ------------------------------------------------------------------
    # UI shell
    # ------------------------------------------------------------------

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            header, text="HR Tasks", text_color=C_TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")

        self.kind_switch = ctk.CTkSegmentedButton(
            header, values=["Onboarding", "Offboarding"],
            command=self._on_kind_change,
            fg_color=C_PANEL, selected_color=C_SELECTED,
            selected_hover_color="#1d4ed8", unselected_color=C_PANEL,
            unselected_hover_color=C_PANEL_HOVER, text_color=C_TEXT,
        )
        self.kind_switch.set("Onboarding")
        self.kind_switch.pack(side="left", padx=(16, 0))

        self.status = ctk.CTkLabel(header, text="", text_color=C_DIM)
        self.status.pack(side="left", padx=(14, 0))

        self.refresh_btn = ctk.CTkButton(
            header, text="Refresh", width=110,
            fg_color=C_SELECTED, hover_color="#1d4ed8",
            command=self._on_refresh,
        )
        self.refresh_btn.pack(side="right")
        ctk.CTkButton(
            header, text="Mark All Done", width=120,
            fg_color="transparent", border_width=1, border_color=C_BORDER,
            text_color=C_TEXT, hover_color=C_PANEL_HOVER,
            command=self._mark_all_done,
        ).pack(side="right", padx=(0, 8))
        ctk.CTkButton(
            header, text="Setup", width=70,
            fg_color="transparent", border_width=1, border_color=C_BORDER,
            text_color=C_DIM, hover_color=C_PANEL_HOVER,
            command=self._build_setup_ui,
        ).pack(side="right", padx=(0, 8))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(4, 14))

        if self._feed_cfg:
            self._build_tasks_ui()
            self.after(300, lambda: self._on_refresh(interactive=False))
        else:
            self._build_setup_ui()

    # ---- Setup card (no hr_feed.json yet) -----------------------------

    def _build_setup_ui(self):
        for w in self.body.winfo_children():
            w.destroy()
        self.body.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(self.body, fg_color=C_PANEL, corner_radius=10)
        card.grid(row=0, column=0, sticky="new")
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="Connect the HR sheets", text_color=C_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            card,
            text=(
                "Paste the SharePoint links to the HR Excel files and pick "
                "which tenant's sign-in can open them. Settings are stored "
                "locally on this machine only."
            ),
            text_color=C_DIM, wraplength=680, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 10))

        def entry_row(r, label):
            ctk.CTkLabel(card, text=label, text_color=C_TEXT).grid(
                row=r, column=0, sticky="w", padx=(16, 8), pady=6
            )
            e = ctk.CTkEntry(
                card, placeholder_text="https://...sharepoint.com/...xlsx...",
                fg_color=C_FIELD, border_color=C_BORDER, text_color=C_TEXT,
            )
            e.grid(row=r, column=1, sticky="ew", padx=(0, 16), pady=6)
            return e

        self.onboard_url_entry = entry_row(2, "Onboarding file URL:")
        self.offboard_url_entry = entry_row(3, "Offboarding file URL (optional):")

        ctk.CTkLabel(card, text="Sign-in tenant:", text_color=C_TEXT).grid(
            row=4, column=0, sticky="w", padx=(16, 8), pady=6
        )
        managed = {
            child for cfg in self.config.values() for child in cfg.get("manages", [])
        }
        rtos = [r for r in self.config if r not in managed] or list(self.config)
        self.rto_menu = ctk.CTkOptionMenu(
            card, values=rtos or ["(no tenants configured)"],
            fg_color=C_FIELD, button_color=C_SELECTED, text_color=C_TEXT,
        )
        self.rto_menu.grid(row=4, column=1, sticky="w", padx=(0, 16), pady=6)

        ctk.CTkButton(
            card, text="Save & Load Tasks", width=170,
            fg_color=C_SELECTED, hover_color="#1d4ed8",
            command=self._on_save_setup,
        ).grid(row=5, column=1, sticky="w", pady=(8, 16))

        # Reconfiguring: show what is currently saved.
        if self._feed_cfg:
            feeds = self._feed_cfg["feeds"]
            if "onboarding" in feeds:
                self.onboard_url_entry.insert(0, feeds["onboarding"]["share_url"])
            if "offboarding" in feeds:
                self.offboard_url_entry.insert(0, feeds["offboarding"]["share_url"])
            if self._feed_cfg["rto"] in rtos:
                self.rto_menu.set(self._feed_cfg["rto"])

    def _on_save_setup(self):
        on_url = self.onboard_url_entry.get().strip()
        off_url = self.offboard_url_entry.get().strip()
        rto = self.rto_menu.get()

        def looks_ok(url):
            return url.lower().startswith("https://") and "sharepoint.com" in url.lower()

        if not looks_ok(on_url):
            messagebox.showerror("HR Tasks", "The onboarding URL doesn't look like a SharePoint link.")
            return
        if off_url and not looks_ok(off_url):
            messagebox.showerror("HR Tasks", "The offboarding URL doesn't look like a SharePoint link.")
            return
        if not rto or rto not in self.config:
            messagebox.showerror("HR Tasks", "Pick a tenant to sign in with.")
            return

        # Merge over the existing config so custom columns/org_codes survive
        # a URL or tenant change.
        cfg = self._feed_cfg or {
            "fetch_last": hr_feed.DEFAULT_FETCH_LAST,
            "poll_minutes": hr_feed.DEFAULT_POLL_MINUTES,
            "org_codes": hr_feed.DEFAULT_ORG_CODES,
        }
        cfg["rto"] = rto
        feeds = cfg.setdefault("feeds", {})
        feeds.setdefault("onboarding", {})["share_url"] = on_url
        if off_url:
            feeds.setdefault("offboarding", {})["share_url"] = off_url
        else:
            feeds.pop("offboarding", None)
        hr_feed.save_feed_config(cfg)
        self._feed_cfg = hr_feed.load_feed_config()
        self._tasks = {"onboarding": [], "offboarding": []}
        self._build_tasks_ui()
        self._on_refresh()

    # ---- Task list + detail -------------------------------------------

    def _build_tasks_ui(self):
        for w in self.body.winfo_children():
            w.destroy()
        self.body.grid_columnconfigure(0, weight=0, minsize=330)
        self.body.grid_columnconfigure(1, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.task_list = ctk.CTkScrollableFrame(
            self.body, fg_color=C_PANEL, corner_radius=10, width=330,
        )
        self.task_list.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.detail = ctk.CTkFrame(self.body, fg_color=C_PANEL, corner_radius=10)
        self.detail.grid(row=0, column=1, sticky="nsew")
        self.detail.grid_columnconfigure(0, weight=1)
        self.detail.grid_rowconfigure(0, weight=1)
        self._show_detail_placeholder("Select a task on the left.")

    def _on_kind_change(self, value):
        self._kind = value.lower()
        self._selected_key = None
        self._populate_tasks()

    def _show_detail_placeholder(self, text):
        for w in self.detail.winfo_children():
            w.destroy()
        self.detail.grid_rowconfigure(0, weight=1)
        ctk.CTkLabel(self.detail, text=text, text_color=C_DIM).grid(
            row=0, column=0, padx=20, pady=20
        )

    def _account_entries(self, row) -> list[str]:
        col = ("Account Required" if self._kind == "onboarding"
               else "What accounts should be closed")
        return hr_feed.split_multi(hr_feed.get_col(row, col))

    def _populate_tasks(self):
        for w in self.task_list.winfo_children():
            w.destroy()
        self._cards = []
        tasks = self._tasks[self._kind]
        if not tasks:
            self._show_detail_placeholder("No tasks fetched yet — press Refresh.")
            ctk.CTkLabel(
                self.task_list, text="Nothing here.", text_color=C_DIM
            ).pack(pady=16)
            return
        self._show_detail_placeholder("Select a task on the left.")

        for row in tasks:
            card = self._make_card(row)
            card.pack(fill="x", padx=8, pady=4)
            if row["_key"] == self._selected_key:
                self._highlight(card, True)
                self._show_detail(row)

    def _make_card(self, row) -> ctk.CTkFrame:
        first = hr_feed.get_col(row, "Preferred First Name").strip() \
            or hr_feed.get_col(row, "First Name").strip()
        last = hr_feed.get_col(row, "Preferred Last Name").strip() \
            or hr_feed.get_col(row, "Last Name").strip()
        name = f"{first} {last}".strip() or f"Row {row.get('_row', '?')}"
        org = hr_feed.get_col(row, "Organization")
        date = hr_feed.pretty_date(
            hr_feed.get_col(row, "Start Date")
            or hr_feed.get_col(row, "Date of closing account")
            or hr_feed.get_col(row, "Start time")
        )

        card = ctk.CTkFrame(
            self.task_list, fg_color=C_FIELD, corner_radius=8,
            border_width=1, border_color=C_BORDER,
        )
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text=name, text_color=C_TEXT, anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(7, 0))
        status_bg, status_fg = TAG_DONE if row["_done"] else TAG_PENDING
        ctk.CTkLabel(
            card, text="DONE" if row["_done"] else "PENDING",
            fg_color=status_bg, text_color=status_fg,
            corner_radius=6, font=ctk.CTkFont(size=10, weight="bold"),
            padx=7, pady=1, height=18,
        ).grid(row=0, column=1, sticky="e", padx=(0, 10), pady=(7, 0))
        ctk.CTkLabel(
            card, text=f"{org}   ·   {date}", text_color=C_DIM, anchor="w",
            font=ctk.CTkFont(size=11),
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=10)

        tags = ctk.CTkFrame(card, fg_color="transparent")
        tags.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(3, 7))

        def chip(text, palette):
            bg, fg = palette
            ctk.CTkLabel(
                tags, text=text, fg_color=bg, text_color=fg,
                corner_radius=6, font=ctk.CTkFont(size=10, weight="bold"),
                padx=7, pady=1, height=18,
            ).pack(side="left", padx=(2, 2))

        # Shorten "Email - Reach" -> "Reach"; colour keeps the service
        # (blue = email, purple = moodle) so many chips fit the card.
        entries = self._account_entries(row)
        for entry in entries[:6]:
            palette = _chip_palette(entry)
            label = entry.rsplit("-", 1)[-1].strip() if "-" in entry else entry
            chip(label[:14], palette)
        if len(entries) > 6:
            chip(f"+{len(entries) - 6}", TAG_MISC)

        for w in (card, *card.winfo_children(), *tags.winfo_children()):
            w.bind("<Button-1>", lambda _e, r=row, c=card: self._select_task(r, c))
        self._cards.append((card, row))
        return card

    @staticmethod
    def _highlight(card, on):
        card.configure(
            border_color=C_SELECTED if on else C_BORDER,
            fg_color=C_PANEL_HOVER if on else C_FIELD,
        )

    def _select_task(self, row, card):
        for c, _r in self._cards:
            self._highlight(c, c is card)
        self._selected_key = row["_key"]
        self._show_detail(row)

    def _show_detail(self, row):
        for w in self.detail.winfo_children():
            w.destroy()
        self.detail.grid_rowconfigure(0, weight=1)
        self.detail.grid_rowconfigure(1, weight=0)

        fields = ctk.CTkScrollableFrame(self.detail, fg_color="transparent")
        fields.grid(row=0, column=0, sticky="nsew", padx=14, pady=(14, 4))
        fields.grid_columnconfigure(1, weight=1)

        for r, col in enumerate(self._feed_cfg["feeds"][self._kind]["columns"]):
            ctk.CTkLabel(
                fields, text=col, text_color=C_DIM, anchor="w", width=190,
                wraplength=185, justify="left", font=ctk.CTkFont(size=12),
            ).grid(row=r, column=0, sticky="nw", pady=3)
            value = hr_feed.get_col(row, col)
            if "date" in col.casefold() or "time" in col.casefold():
                value = hr_feed.pretty_date(value)
            ctk.CTkLabel(
                fields, text=value or "—", text_color=C_TEXT,
                anchor="w", justify="left", wraplength=500,
                font=ctk.CTkFont(size=12),
            ).grid(row=r, column=1, sticky="ew", pady=3)

        bar = ctk.CTkFrame(self.detail, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=14, pady=(4, 14))

        if self._kind == "onboarding":
            n_tenants = len(self._email_rtos(row))
            label = (f"Add to Bulk ({n_tenants} tenants)" if n_tenants > 1
                     else "Create Account")
            ctk.CTkButton(
                bar, text=label, width=160,
                fg_color=C_SELECTED, hover_color="#1d4ed8",
                command=lambda: self._create_account(row),
            ).pack(side="left")
        else:
            ctk.CTkButton(
                bar, text="Find User", width=160,
                fg_color="#b91c1c", hover_color="#991b1b",
                command=lambda: self._find_user(row),
            ).pack(side="left")

        if row["_done"]:
            ctk.CTkLabel(
                bar, text="Done", text_color=TAG_DONE[1],
            ).pack(side="left", padx=(12, 0))
        else:
            ctk.CTkButton(
                bar, text="Mark Done", width=120,
                fg_color="transparent", border_width=1, border_color=C_BORDER,
                text_color=C_TEXT, hover_color=C_PANEL_HOVER,
                command=lambda: self._mark_done(row),
            ).pack(side="left", padx=(10, 0))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _email_rtos(self, row) -> list[str]:
        """Tenants needing an M365 account, from the 'Email - X' entries in
        Account Required (Moodle entries are not M365 accounts)."""
        rtos = []
        for entry in self._account_entries(row):
            if entry.casefold().startswith("email"):
                rto = hr_feed.org_code_to_rto(
                    entry, self.config, self._feed_cfg.get("org_codes", {})
                )
                if rto and rto not in rtos:
                    rtos.append(rto)
        return rtos

    def _create_account(self, row):
        if not self.on_prefill:
            return
        feed = self._feed_cfg["feeds"]["onboarding"]
        form_fields = {}
        for form_key, column in feed["form_map"].items():
            value = (hr_feed.get_col(row, column) or "").strip()
            if value:
                form_fields[form_key] = value

        rtos = self._email_rtos(row)
        if len(rtos) > 1 and self.on_bulk:
            self._create_bulk(form_fields, rtos)
            return

        manager_name = form_fields.get("manager", "")
        rto = rtos[0] if rtos else None
        if not manager_name or not rto:
            self.on_prefill(form_fields)
            return

        # Resolve the manager's display name to a real account in the
        # target tenant so the form gets a UPN instead of "Bobby Xu".
        self.status.configure(
            text=f"Finding manager '{manager_name}' in {rto}...", text_color=C_DIM
        )

        def _run():
            upn, candidates = self._resolve_manager(rto, manager_name)

            def _finish():
                if upn:
                    form_fields["manager"] = upn
                    self.status.configure(text=f"Manager resolved: {upn}", text_color=C_OK)
                    self.on_prefill(form_fields)
                elif candidates:
                    # Same person exists under several org domains — let the
                    # user pick instead of guessing the wrong tenant.
                    self.status.configure(
                        text=f"Multiple accounts for '{manager_name}' — pick one",
                        text_color=C_WARN,
                    )
                    self._pick_manager(manager_name, candidates, form_fields)
                else:
                    self.status.configure(
                        text=f"Manager '{manager_name}' not found in {rto} — check the field",
                        text_color=C_WARN,
                    )
                    self.on_prefill(form_fields)

            self.after(0, _finish)

        threading.Thread(target=_run, daemon=True).start()

    def _create_bulk(self, form_fields, rtos):
        """Multi-tenant hire: queue one editable Bulk row per 'Email - X'
        tenant. Managers resolve silently per tenant (only on a clean
        domain match) — anything unresolved stays as the typed name and the
        user fixes it in the Bulk grid."""
        manager_name = form_fields.get("manager", "")
        self.status.configure(
            text=f"Preparing {len(rtos)} accounts ({', '.join(rtos)})...",
            text_color=C_DIM,
        )

        def _run():
            entries = []
            for rto in rtos:
                cfg = self.config.get(rto, {})
                upn = None
                if manager_name:
                    upn, _cands = self._resolve_manager(rto, manager_name)
                entries.append({
                    "first_name": form_fields.get("first_name", ""),
                    "last_name": form_fields.get("last_name", ""),
                    "rto": rto,
                    "domain": cfg.get("domain", ""),
                    "prefix": cfg.get("prefix", ""),
                    "title": form_fields.get("title", ""),
                    "department": form_fields.get("department", ""),
                    "office_location": form_fields.get("office_location", ""),
                    "manager_display": manager_name,
                    "manager_upn": upn or "",
                })
            self.after(0, lambda: self.on_bulk(entries))

        threading.Thread(target=_run, daemon=True).start()

    def _pick_manager(self, name, candidates, form_fields):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Pick manager account")
        dlg.configure(fg_color=C_BG)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry("520x340")

        ctk.CTkLabel(
            dlg, text=f"Which account is the manager '{name}'?",
            text_color=C_TEXT, font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 8))

        box = ctk.CTkScrollableFrame(dlg, fg_color=C_PANEL, corner_radius=10)
        box.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        def choose(upn):
            dlg.destroy()
            if upn:
                form_fields["manager"] = upn
                self.status.configure(text=f"Manager set: {upn}", text_color=C_OK)
            self.on_prefill(form_fields)

        for user in candidates:
            ctk.CTkButton(
                box, anchor="w", fg_color=C_FIELD, hover_color=C_PANEL_HOVER,
                text_color=C_TEXT, corner_radius=8, height=40,
                text=f"{user['displayName']}   —   {user['upn']}",
                command=lambda u=user["upn"]: choose(u),
            ).pack(fill="x", padx=6, pady=3)

        ctk.CTkButton(
            dlg, text=f"Keep '{name}' as typed", fg_color="transparent",
            border_width=1, border_color=C_BORDER, text_color=C_TEXT,
            hover_color=C_PANEL_HOVER, command=lambda: choose(None),
        ).pack(anchor="e", padx=16, pady=(0, 14))

    def _resolve_manager(self, rto: str, manager_name: str):
        """Find the manager's account in the target tenant.

        The tenant hosts several org domains, so the same person exists as
        j.smith@org1, j.smith@org2, ... Search by display name AND the alias
        convention (first-initial.lastname). Auto-resolve only when an
        account matches the target RTO's own domain; otherwise return the
        ranked candidate list so the user picks.

        Returns (upn_or_None, candidates).
        """
        token = self.graph_mgr.get_token_silent(rto)
        if not token:
            return None, []

        candidates: dict[str, dict] = {}

        def search(query):
            try:
                for m in self.graph_mgr.search_users(token, query):
                    if m.get("upn"):
                        candidates.setdefault(m["upn"].casefold(), m)
            except Exception:
                pass

        search(manager_name)
        parts = manager_name.split()
        if len(parts) >= 2:
            search(f"{parts[0][0]}.{parts[-1]}".lower())

        if not candidates:
            return None, []

        target_domain = self.config.get(rto, {}).get("domain", "").casefold()
        auth_domain = self.config.get(
            self.graph_mgr.get_auth_rto(rto), {}
        ).get("domain", "").casefold()
        wanted = manager_name.casefold()

        expected_alias = (
            f"{parts[0][0]}.{parts[-1]}".casefold() if len(parts) >= 2 else ""
        )

        def rank(user):
            local, _, domain = user["upn"].casefold().partition("@")
            name_hit = wanted in user["displayName"].strip().casefold()
            domain_rank = (
                0 if domain == target_domain
                else 1 if domain == auth_domain
                else 3 if domain.endswith("onmicrosoft.com")
                else 2
            )
            # Prefer the staff alias convention (j.smith) over e.g. the same
            # person's student-number account (0000012345@...).
            alias_rank = 0 if local == expected_alias else 1
            return (0 if name_hit else 1, domain_rank, alias_rank)

        ranked = sorted(candidates.values(), key=rank)
        best = ranked[0]
        best_domain = best["upn"].split("@")[-1].casefold()
        if target_domain and best_domain == target_domain \
                and wanted in best["displayName"].strip().casefold():
            return best["upn"], ranked
        if len(ranked) == 1:
            return best["upn"], ranked
        return None, ranked[:8]

    def _find_user(self, row):
        if not self.on_offboard:
            return
        email = hr_feed.get_col(row, "Email ID").strip()
        if not email:
            messagebox.showwarning(
                "HR Tasks", "This row has no staff email to search for.", parent=self
            )
            return
        self.on_offboard(email)

    def _mark_all_done(self):
        pending = [t for t in self._tasks[self._kind] if not t["_done"]]
        if not pending:
            return
        if not messagebox.askyesno(
            "HR Tasks",
            f"Mark all {len(pending)} pending {self._kind} task(s) as done?\n"
            "Use this once to clear rows that were already handled before "
            "the app started tracking them.",
        ):
            return
        for task in pending:
            hr_feed.mark_done(task["_key"])
            task["_done"] = True
        self._populate_tasks()
        self._push_badge()
        self.status.configure(text="All caught up", text_color=C_OK)

    def _mark_done(self, row):
        hr_feed.mark_done(row["_key"])
        row["_done"] = True
        self._populate_tasks()
        self._push_badge()

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def _on_refresh(self, interactive=True):
        if self._busy or not self._feed_cfg:
            return
        self._busy = True
        self.refresh_btn.configure(state="disabled")
        self.status.configure(text="Fetching from SharePoint...", text_color=C_DIM)
        cfg = self._feed_cfg
        rto = cfg["rto"]

        def _run():
            token = self.graph_mgr.get_token_silent(rto)
            if not token and interactive:
                def on_code(user_code, verification_uri):
                    self.after(0, lambda: self._show_device_code(user_code, verification_uri))

                token, err = self.graph_mgr.acquire_token(rto, on_code)
                self.after(0, self._close_auth_dialog)
                if err:
                    self.after(0, lambda: self._fetch_done(None, f"Sign-in failed: {err}"))
                    return
            if not token:
                self.after(0, lambda: self._fetch_done(None, "Sign-in needed — press Refresh"))
                return

            results = {}
            errors = []
            for kind, feed in cfg["feeds"].items():
                client = hr_feed.HRFeedClient(kind, feed, cfg["fetch_last"])
                try:
                    results[kind] = client.fetch(token)
                except Exception as exc:
                    errors.append(f"{kind}: {exc}")
            self.after(0, lambda: self._fetch_done(results, "; ".join(errors) or None))

        threading.Thread(target=_run, daemon=True).start()

    def _fetch_done(self, results, error):
        self._busy = False
        self.refresh_btn.configure(state="normal")
        if results:
            for kind, rows in results.items():
                self._tasks[kind] = rows
            self._populate_tasks()
            self._push_badge()
        if error:
            self.status.configure(text=error, text_color=C_WARN)
            return
        pending = sum(
            1 for rows in self._tasks.values() for t in rows if not t["_done"]
        )
        self.status.configure(
            text=f"{pending} pending" if pending else "All caught up",
            text_color=C_DIM if pending else C_OK,
        )

    def _push_badge(self):
        if self.on_badge:
            self.on_badge(sum(
                1 for rows in self._tasks.values() for t in rows if not t["_done"]
            ))

    def _show_device_code(self, user_code, verification_uri):
        self._close_auth_dialog()
        self._auth_dialog = DeviceCodeDialog(self.root, user_code, verification_uri)

    def _close_auth_dialog(self):
        if self._auth_dialog:
            try:
                self._auth_dialog.destroy()
            except tk.TclError:
                pass
            self._auth_dialog = None
