import sys
import tkinter as tk
import threading
from pathlib import Path

import customtkinter as ctk

BASE_DIR = Path(__file__).resolve().parent
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(BASE_DIR / "account_app"))
    sys.path.insert(0, str(BASE_DIR / "student_app"))

from app.ui.main_window import AccountCreationSection
from app.ui.theme import (
    C_BG,
    C_DIM,
    C_PANEL,
    C_PANEL_HOVER,
    C_SELECTED,
    C_TEXT,
    apply_dark_theme,
)
from portable_paths import initialize_user_data, is_first_run
from settings_page import SettingsSection
from shared_sessions import SharedRTOState
from setup_wizard import SetupWizard
from student_checker import StudentCheckerSection


class CombinedM365App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("M365 Tools")
        self.geometry("1400x860")
        self.minsize(1100, 680)
        self.configure(fg_color=C_BG)
        apply_dark_theme(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._sections: dict[str, tk.Widget] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._active_section = ""
        self.session_states: dict[str, SharedRTOState] = {}
        self._hr_graph = None  # lazy GraphManager for the background HR poll

        if is_first_run():
            self._build_setup()
        else:
            initialize_user_data(copy_default_config=False)
            self._build_shell()
            self._show_section("accounts")
            self.after(3000, self._start_hr_poll)

    def _build_setup(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.setup = SetupWizard(self, on_complete=self._setup_complete)
        self.setup.grid(row=0, column=0, sticky="nsew")

    def _setup_complete(self):
        self.setup.destroy()
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self._build_shell()
        self._show_section("accounts")
        self.after(3000, self._start_hr_poll)

    def _build_shell(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=C_PANEL)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar,
            text="M365 Tools",
            text_color=C_TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=18, pady=(22, 4))

        ctk.CTkLabel(
            sidebar,
            text="SECTIONS",
            text_color=C_DIM,
            font=ctk.CTkFont(size=10, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=18, pady=(20, 8))

        self._add_nav(sidebar, "accounts", "Account Creation")
        self._add_nav(sidebar, "offboarding", "Offboarding")
        self._add_nav(sidebar, "usermanager", "User Manager")
        self._add_nav(sidebar, "hrtasks", "HR Tasks")
        self._add_nav(sidebar, "students", "Student Checker")
        self._add_nav(sidebar, "settings", "Settings")

        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color=C_BG)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

    def _add_nav(self, parent, key: str, label: str):
        btn = ctk.CTkButton(
            parent,
            text=label,
            anchor="w",
            height=38,
            corner_radius=8,
            fg_color="transparent",
            hover_color=C_PANEL_HOVER,
            text_color=C_TEXT,
            command=lambda: self._show_section(key),
        )
        btn.pack(fill="x", padx=10, pady=3)
        self._nav_buttons[key] = btn

    def _show_section(self, key: str):
        if key == self._active_section:
            return

        current = self._sections.get(self._active_section)
        if current:
            current.grid_remove()

        section = self._sections.get(key)
        if not section:
            if key == "accounts":
                section = AccountCreationSection(self.content, session_states=self.session_states)
            elif key == "offboarding":
                from app.ui.offboarding import OffboardingSection
                section = OffboardingSection(self.content, session_states=self.session_states)
            elif key == "usermanager":
                from app.ui.usermanager import UserManagerSection
                section = UserManagerSection(self.content, session_states=self.session_states)
            elif key == "hrtasks":
                from app.ui.hrtasks import HRTasksSection
                section = HRTasksSection(
                    self.content,
                    session_states=self.session_states,
                    on_badge=self._set_hr_badge,
                    on_prefill=self._prefill_account,
                    on_offboard=self._offboard_from_task,
                    on_bulk=self._bulk_from_task,
                )
            elif key == "students":
                section = StudentCheckerSection(self.content, session_states=self.session_states)
            elif key == "settings":
                section = SettingsSection(self.content)
            else:
                raise KeyError(key)
            section.grid(row=0, column=0, sticky="nsew")
            self._sections[key] = section
        else:
            section.grid()

        self._active_section = key
        self._refresh_nav()

    def _refresh_nav(self):
        for key, btn in self._nav_buttons.items():
            selected = key == self._active_section
            btn.configure(fg_color=C_SELECTED if selected else "transparent")

    # ------------------------------------------------------------------
    # HR Tasks feed — sidebar badge + prefill into Account Creation
    # ------------------------------------------------------------------

    def _set_hr_badge(self, count: int):
        btn = self._nav_buttons.get("hrtasks")
        if btn:
            btn.configure(text=f"HR Tasks ({count})" if count else "HR Tasks")

    def _prefill_account(self, fields: dict):
        self._show_section("accounts")
        self._sections["accounts"].form.prefill(fields)

    def _offboard_from_task(self, email: str):
        self._show_section("offboarding")
        self._sections["offboarding"].search_alias(email)

    def _bulk_from_task(self, entries: list[dict]):
        self._show_section("accounts")
        section = self._sections["accounts"]
        section.notebook.select(section.bulk)
        section.bulk.add_prefilled_rows(entries)

    def _start_hr_poll(self):
        from app import hr_feed

        cfg = hr_feed.load_feed_config()
        minutes = cfg.get("poll_minutes", 5) if cfg else 5

        def _run():
            feed_cfg = hr_feed.load_feed_config()
            if not feed_cfg:
                return
            # Silent-token only in the background — never pop a sign-in
            # dialog unprompted. The HR Tasks section handles interactive
            # sign-in when opened.
            if self._hr_graph is None:
                from app.config_loader import load_config
                from app.graph import GraphManager
                self._hr_graph = GraphManager(load_config())
            token = self._hr_graph.get_token_silent(feed_cfg["rto"])
            if not token:
                return
            try:
                pending = hr_feed.count_pending(token, feed_cfg)
            except Exception:
                return
            self.after(0, lambda: self._set_hr_badge(pending))

        threading.Thread(target=_run, daemon=True).start()
        self.after(max(1, minutes) * 60_000, self._start_hr_poll)

    def _on_close(self):
        for state in self.session_states.values():
            if getattr(state, "session", None):
                threading.Thread(target=state.session.disconnect, daemon=True).start()
        self.destroy()


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = CombinedM365App()
    app.mainloop()
