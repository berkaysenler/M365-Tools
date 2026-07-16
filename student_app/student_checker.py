"""
VCIT Student Checker
Multi-account M365 student checker — each account runs in its own tab, in parallel.

Checks run over Microsoft Graph: one paged bulk fetch of all users (with
department + assignedLicenses) and one group-member fetch, then all student
IDs are matched locally — seconds instead of 2-3 Exchange round-trips per
student. The Graph token cache is shared with the Account Creation section
and the offboarding tool, so a sign-in in any of them unlocks this too.
"""
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import json
import csv
import re
import subprocess
import sys
import threading
from pathlib import Path
import datetime
from portable_paths import (
    APP_DATA_DIR,
    CONFIG_FILE,
    LAST_IDS_FILE,
    STUDENT_ACCOUNTS_FILE,
)
from app.auth import TokenManager
from app.graph import GraphManager
from app.ui.dialogs import DeviceCodeDialog
from setup_wizard import CredentialDialog, _upsert_config_account

APP_ID        = "VCIT_StudentChecker"
CONFIG_DIR    = APP_DATA_DIR
ACCOUNTS_FILE = STUDENT_ACCOUNTS_FILE
APP_DIR       = (
    Path(sys._MEIPASS) / "student_app"
    if getattr(sys, "frozen", False)
    else Path(__file__).parent
)
SCRIPT_PATH   = APP_DIR / "check_students.ps1"
LAST_ID_COLUMNS = ["Date", "Last Student IDs"]
LAST_ID_RTO_LABELS = {"PV": "PE"}
LAST_IDS_LOCK = threading.Lock()
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Log colours
COLORS = {
    "green":   "#4CAF50",
    "yellow":  "#FFC107",
    "red":     "#EF5350",
    "magenta": "#CE93D8",
    "cyan":    "#4DD0E1",
    "white":   "#E0E0E0",
    "gray":    "#9E9E9E",
}

# UI palette
C_SIDEBAR    = "#1c1f2e"
C_CARD       = "#252840"
C_CARD_HOV   = "#2d3154"
C_CARD_SEL   = "#1a3a6e"
C_HEADER     = "#13151f"
C_ACCENT     = "#4361ee"
C_ACCENT_HOV = "#3451d1"
C_BG         = "#1a1d2e"
C_FRAME      = "#20233a"
C_LOG        = "#0d0f1a"
C_TEXT       = "#e2e2f0"
C_DIM        = "#6b7090"


def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Edit dialog — student-checker-specific fields only.
# New accounts are added via setup_wizard.CredentialDialog (writes config.json
# and student_accounts.json together), so this dialog only edits the per-RTO
# Student Checker overrides.
# ---------------------------------------------------------------------------
class EditAccountDialog(ctk.CTkToplevel):
    def __init__(self, parent, account):
        super().__init__(parent)
        self.title("Edit Student Checker Settings")
        self.geometry("520x440")
        self.resizable(False, False)
        self.grab_set()
        self.result = None
        self.configure(fg_color=C_BG)

        self._header(f"Edit {account.get('rto', '')}")

        pad = {"padx": 24, "pady": (10, 0)}

        self._label("RTO").pack(fill="x", **pad)
        ctk.CTkLabel(self, text=account.get("rto", ""), text_color=C_TEXT,
                     anchor="w").pack(fill="x", padx=24)

        self._label("Admin UPN").pack(fill="x", **pad)
        ctk.CTkLabel(self, text=account.get("upn", ""), text_color=C_TEXT,
                     anchor="w").pack(fill="x", padx=24)

        self._label("Distribution Group").pack(fill="x", **pad)
        self.group = self._entry()
        self.group.insert(0, account.get("group", "All Students"))
        self.group.pack(fill="x", padx=24)

        self._label("Student ID Column (CSV header)").pack(fill="x", **pad)
        self.id_col = self._entry(placeholder="StudentID")
        self.id_col.insert(0, account.get("id_col", "StudentID"))
        self.id_col.pack(fill="x", padx=24)

        ctk.CTkLabel(
            self,
            text=(
                "To change RTO name, admin UPN or domain, use Settings → "
                "Add account. Sign-in is device-code only — no cred files."
            ),
            text_color=C_DIM,
            wraplength=460,
            justify="left",
            font=ctk.CTkFont(size=10),
        ).pack(fill="x", padx=24, pady=(16, 0))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(pady=20)
        ctk.CTkButton(btns, text="Save", width=120, height=36,
                      fg_color=C_ACCENT, hover_color=C_ACCENT_HOV,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self._save).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Cancel", width=100, height=36,
                      fg_color=C_CARD, hover_color=C_CARD_HOV,
                      command=self.destroy).pack(side="left", padx=6)

        self._account = account

    def _header(self, text):
        h = ctk.CTkFrame(self, fg_color=C_HEADER, height=52, corner_radius=0)
        h.pack(fill="x")
        h.pack_propagate(False)
        ctk.CTkLabel(h, text=text,
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=C_TEXT).pack(side="left", padx=18, pady=14)

    def _label(self, text):
        return ctk.CTkLabel(self, text=text, anchor="w",
                            text_color=C_DIM, font=ctk.CTkFont(size=11))

    def _entry(self, placeholder=""):
        return ctk.CTkEntry(self, placeholder_text=placeholder,
                            fg_color=C_CARD, border_color="#3a3d5c",
                            text_color=C_TEXT, placeholder_text_color=C_DIM)

    def _save(self):
        group = self.group.get().strip() or "All Students"
        id_col = self.id_col.get().strip() or "StudentID"
        result = dict(self._account)
        result["group"] = group
        result["id_col"] = id_col
        # Legacy keys we no longer use — drop them on save.
        for legacy in ("cred_file", "disable_wam", "welcome_url"):
            result.pop(legacy, None)
        self.result = result
        self.destroy()


# ---------------------------------------------------------------------------
# Sidebar account card
# ---------------------------------------------------------------------------
class AccountCard(ctk.CTkFrame):
    def __init__(self, parent, account: dict, on_click):
        super().__init__(parent, fg_color=C_CARD, corner_radius=10,
                         border_width=1, border_color="#2e3150")
        self.account  = account
        self._selected = False
        self._running  = False

        self.pack(fill="x", padx=10, pady=4)

        self._dot = ctk.CTkLabel(self, text="●", width=18,
                                  font=ctk.CTkFont(size=11),
                                  text_color="#444466")
        self._dot.pack(side="left", padx=(12, 4), pady=12)

        col = ctk.CTkFrame(self, fg_color="transparent")
        col.pack(side="left", fill="both", expand=True, pady=10)
        self._name_lbl = ctk.CTkLabel(col, text=account["rto"],
                                       font=ctk.CTkFont(size=13, weight="bold"),
                                       text_color=C_TEXT, anchor="w")
        self._name_lbl.pack(fill="x")
        self._upn_lbl = ctk.CTkLabel(col, text=account["upn"],
                                      font=ctk.CTkFont(size=10),
                                      text_color=C_DIM, anchor="w")
        self._upn_lbl.pack(fill="x")

        for w in [self, col, self._dot, self._name_lbl, self._upn_lbl]:
            w.bind("<Button-1>", lambda e, rto=account["rto"]: on_click(rto))
            w.bind("<Enter>",    lambda e: self._hover(True))
            w.bind("<Leave>",    lambda e: self._hover(False))

    def _hover(self, entering: bool):
        if self._selected:
            return
        self.configure(fg_color=C_CARD_HOV if entering else C_CARD)

    def set_selected(self, sel: bool):
        self._selected = sel
        self.configure(
            fg_color=C_CARD_SEL if sel else C_CARD,
            border_color=C_ACCENT if sel else "#2e3150"
        )

    def set_running(self, running: bool):
        self._running = running
        self._dot.configure(text_color="#4CAF50" if running else "#444466")

    def update_account(self, account: dict):
        self.account = account
        self._name_lbl.configure(text=account["rto"])
        self._upn_lbl.configure(text=account["upn"])


# ---------------------------------------------------------------------------
# Per-account tab
# ---------------------------------------------------------------------------
class AccountTab:
    def __init__(self, tabview: ctk.CTkTabview, account: dict, root, card: AccountCard):
        self.account    = account
        self.root       = root
        self.card       = card
        self.process    = None
        self.running    = False
        self._stop_requested = False
        self._stats     = {}
        self._log_lines = []
        self._issue_ids = {"missing": [], "nolic": [], "nogroup": []}
        self._last_student_id = ""

        tabview.add(account["rto"])
        self.frame = tabview.tab(account["rto"])
        self.frame.configure(fg_color=C_BG)
        self._build()

    def _build(self):
        f = self.frame

        info = ctk.CTkFrame(f, fg_color=C_HEADER, corner_radius=0, height=42)
        info.pack(fill="x")
        info.pack_propagate(False)
        ctk.CTkLabel(info, text="UPN:", text_color=C_DIM,
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=(14, 4), pady=12)
        ctk.CTkLabel(info, text=self.account["upn"], text_color=C_TEXT,
                     font=ctk.CTkFont(size=11)).pack(side="left", pady=12)
        ctk.CTkLabel(info, text="  |  Group:", text_color=C_DIM,
                     font=ctk.CTkFont(size=11)).pack(side="left", pady=12)
        ctk.CTkLabel(info, text=self.account["group"], text_color=C_TEXT,
                     font=ctk.CTkFont(size=11)).pack(side="left", pady=12)
        self._status_lbl = ctk.CTkLabel(info, text="", text_color="#4CAF50",
                                         font=ctk.CTkFont(size=11, weight="bold"))
        self._status_lbl.pack(side="right", padx=14)

        content = ctk.CTkFrame(f, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=14, pady=10)

        csv_row = ctk.CTkFrame(content, fg_color=C_FRAME, corner_radius=10)
        csv_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(csv_row, text="CSV File", width=68, text_color=C_DIM,
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=(14, 0), pady=10)
        self.csv_var = tk.StringVar(value=self._default_csv())
        ctk.CTkEntry(csv_row, textvariable=self.csv_var, height=32,
                     fg_color=C_CARD, border_color="#3a3d5c",
                     text_color=C_TEXT).pack(side="left", fill="x", expand=True, padx=8, pady=8)
        ctk.CTkButton(csv_row, text="Browse…", width=90, height=32,
                      fg_color=C_CARD, hover_color=C_CARD_HOV,
                      border_width=1, border_color="#3a3d5c",
                      command=self._browse).pack(side="left", padx=(0, 10), pady=8)

        ctrl = ctk.CTkFrame(content, fg_color="transparent")
        ctrl.pack(fill="x", pady=(0, 8))

        self.run_btn = ctk.CTkButton(
            ctrl, text="▶   Run Check", width=148, height=38,
            fg_color=C_ACCENT, hover_color=C_ACCENT_HOV,
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, command=self.run
        )
        self.run_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            ctrl, text="■   Stop", width=100, height=38,
            fg_color="#8b1a1a", hover_color="#a93226",
            font=ctk.CTkFont(size=13), corner_radius=8,
            state="disabled", command=self.stop
        )
        self.stop_btn.pack(side="left", padx=(0, 8))

        ctk.CTkButton(ctrl, text="Clear", width=80, height=38,
                      fg_color=C_CARD, hover_color=C_CARD_HOV,
                      corner_radius=8, command=self.clear).pack(side="left", padx=(0, 8))

        ctk.CTkButton(ctrl, text="Export…", width=90, height=38,
                      fg_color=C_CARD, hover_color=C_CARD_HOV,
                      corner_radius=8, command=self.export).pack(side="left")

        self._stats_frame = ctk.CTkFrame(content, fg_color=C_FRAME, corner_radius=10, height=38)
        self._stats_frame.pack(fill="x", pady=(0, 8))
        self._stats_frame.pack_propagate(False)
        self._stats_labels: dict[str, ctk.CTkLabel] = {}
        for key, color, label in [
            ("total",   C_TEXT,      "Total"),
            ("ok",      "#4CAF50",   "✓ OK"),
            ("nogroup", "#FFC107",   "No Group"),
            ("nolic",   "#CE93D8",   "No License"),
            ("missing", "#EF5350",   "Missing"),
        ]:
            lbl = ctk.CTkLabel(self._stats_frame, text=f"{label}: —",
                               text_color=color, font=ctk.CTkFont(size=11))
            lbl.pack(side="left", padx=14)
            self._stats_labels[key] = lbl
        ctk.CTkFrame(self._stats_frame, fg_color="transparent").pack(side="left", fill="x", expand=True)

        log_wrap = ctk.CTkFrame(content, fg_color=C_FRAME, corner_radius=10)
        log_wrap.pack(fill="both", expand=True)

        ctk.CTkLabel(log_wrap, text="Output", text_color=C_DIM,
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=14, pady=(8, 0))

        log_inner = ctk.CTkFrame(log_wrap, fg_color="transparent")
        log_inner.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        self.log = tk.Text(
            log_inner, bg=C_LOG, fg=C_TEXT,
            font=("Consolas", 10), relief="flat",
            wrap="none", state="disabled", bd=0,
            highlightthickness=0, insertbackground=C_TEXT,
            selectbackground="#2a3f6e",
        )
        vsb = ctk.CTkScrollbar(log_inner, command=self.log.yview)
        hsb = ctk.CTkScrollbar(log_inner, command=self.log.xview, orientation="horizontal")
        self.log.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.log.pack(fill="both", expand=True)

        for tag, color in COLORS.items():
            self.log.tag_configure(tag, foreground=color)

    # ------------------------------------------------------------------
    def _default_csv(self):
        return str(Path.home() / "Downloads" / f"{self.account['rto'].lower()}.csv")

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select student CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=str(Path.home() / "Downloads"),
        )
        if path:
            self.csv_var.set(path)

    def run(self):
        csv_path = self.csv_var.get().strip()
        if not csv_path or not Path(csv_path).exists():
            messagebox.showwarning("CSV not found", f"File not found:\n{csv_path}")
            return

        acc = self.account
        self.running = True
        self._stop_requested = False
        self._stats  = {"total": 0, "ok": 0, "missing": 0, "nolic": 0, "nogroup": 0}
        self._log_lines.clear()
        self._issue_ids = {"missing": [], "nolic": [], "nogroup": []}
        self._last_student_id = ""
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal", fg_color="#c0392b")
        self._status_lbl.configure(text="● Running", text_color="#4CAF50")
        self.card.set_running(True)
        self._reset_stats_display()

        threading.Thread(
            target=self._worker,
            args=(acc["rto"], csv_path, acc["group"], acc["upn"],
                  acc.get("id_col", "StudentID")),
            daemon=True,
        ).start()

    def _worker(self, rto, csv_path, group, upn, id_col="StudentID"):
        try:
            student_ids = _student_ids_from_csv(csv_path, id_col)
            self._last_student_id = student_ids[-1] if student_ids else ""
            if self._last_student_id:
                self.root.after(0, self._append, f"[{rto}] Last CSV student ID: {self._last_student_id}", "gray")
            else:
                self.root.after(0, self._append, f"[{rto}] WARNING: No student ID found in CSV column '{id_col}'", "yellow")
        except Exception as exc:
            self.root.after(0, self._append, f"[{rto}] WARNING: Could not read last student ID from CSV - {exc}", "yellow")
            self.root.after(0, self._done)
            return

        self.root.after(0, self._append, f"[{rto}] Acquiring Microsoft Graph token for {upn} ...")
        token = self.root.graph_mgr.get_token_silent(rto)
        if not token:
            token = self._graph_device_code_blocking(rto)
            if not token:
                self.root.after(0, self._append, f"[{rto}] ERROR: Sign-in cancelled or failed.", "red")
                self.root.after(0, self._done)
                return

        try:
            _graph_check_students(
                self.root.graph_mgr,
                token,
                rto,
                student_ids,
                group,
                lambda line: self.root.after(0, self._append, line),
                should_stop=lambda: self._stop_requested,
            )
        except Exception as exc:
            self.root.after(0, self._append, f"[ERROR] {exc}", "red")
        finally:
            self.root.after(0, self._done)

    def _graph_device_code_blocking(self, rto: str) -> str | None:
        """Run the Graph device-code flow, blocking until done (or failed).

        GraphManager.acquire_token is already blocking, so this just marshals
        the dialog to the UI thread and closes it afterwards.
        """
        dialog_holder = {"dlg": None}

        def on_code_ready(user_code, verification_uri):
            def show():
                dialog_holder["dlg"] = DeviceCodeDialog(self.root, user_code, verification_uri)
            self.root.after(0, show)

        token, error = self.root.graph_mgr.acquire_token(rto, on_code_ready)

        def close():
            dlg = dialog_holder.get("dlg")
            if dlg is not None:
                try:
                    dlg.destroy()
                except tk.TclError:
                    pass
        self.root.after(0, close)

        if error:
            self.root.after(0, self._append, f"[{rto}] Sign-in error: {error}", "red")
        return token

    def _append(self, line: str, force_tag: str = None):
        tag = force_tag or _color_tag(line)
        self._log_lines.append(line)
        self.log.configure(state="normal")
        self.log.insert(tk.END, line + "\n", tag)
        self.log.see(tk.END)
        self.log.configure(state="disabled")
        _tally(self._stats, line)
        student_id = _student_id_from_line(line)
        if student_id:
            self._last_student_id = student_id
            self._track_issue_id(line, student_id)
        self._refresh_stats()

    def _track_issue_id(self, line: str, student_id: str):
        u = line.upper()
        if "ACCOUNT: MISSING" in u:
            self._issue_ids["missing"].append(student_id)
        elif "NO LICENSE" in u:
            self._issue_ids["nolic"].append(student_id)
        elif "HAS GROUP: NO" in u:
            self._issue_ids["nogroup"].append(student_id)

    def _refresh_stats(self):
        s = self._stats
        if not s.get("total"):
            return
        self._stats_labels["total"].configure(text=f"Total: {s['total']}")
        self._stats_labels["ok"].configure(text=f"✓ OK: {s['ok']}")
        self._stats_labels["nogroup"].configure(text=f"No Group: {s['nogroup']}")
        self._stats_labels["nolic"].configure(text=f"No License: {s['nolic']}")
        self._stats_labels["missing"].configure(text=f"Missing: {s['missing']}")

    def _reset_stats_display(self):
        for key, lbl in self._stats_labels.items():
            texts = {"total": "Total: 0", "ok": "✓ OK: 0",
                     "nogroup": "No Group: 0", "nolic": "No License: 0",
                     "missing": "Missing: 0"}
            lbl.configure(text=texts[key])

    def _done(self):
        self._save_last_student_id()
        self.running = False
        self.process = None
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled", fg_color="#8b1a1a")
        self._status_lbl.configure(text="● Done", text_color=C_DIM)
        self.card.set_running(False)
        self.root.record_rto_report(self.account["rto"], self._issue_ids)
        self._append("─── Finished ───", "cyan")

    def _save_last_student_id(self):
        if not self._last_student_id:
            return
        try:
            value = _format_last_id_value(self.account["rto"], self._last_student_id)
            _upsert_last_student_id(self.account["rto"], value)
            self._append(f"[{self.account['rto']}] Last student ID saved: {value}", "gray")
        except Exception as exc:
            self._append(f"[{self.account['rto']}] WARNING: Could not save last student ID - {exc}", "yellow")

    def stop(self):
        self._stop_requested = True
        if self.process:
            self.process.terminate()
        self.running = False
        self.process = None
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled", fg_color="#8b1a1a")
        self._status_lbl.configure(text="● Stopped", text_color="#FFC107")
        self.card.set_running(False)
        self._append("─── Stopped ───", "yellow")

    def clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.configure(state="disabled")
        self._log_lines.clear()
        self._stats = {}
        self._issue_ids = {"missing": [], "nolic": [], "nogroup": []}
        self.root.clear_rto_report(self.account["rto"])
        self._reset_stats_display()
        self._status_lbl.configure(text="")

    def export(self):
        if not self._log_lines:
            messagebox.showinfo("Nothing to export", "Run a check first.")
            return
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=f"check_{self.account['rto']}_{ts}.txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if path:
            Path(path).write_text("\n".join(self._log_lines), encoding="utf-8")
            messagebox.showinfo("Exported", f"Saved to:\n{path}")


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------
class StudentCheckerSection(ctk.CTkFrame):
    def __init__(self, parent, session_states=None):
        super().__init__(parent)
        self.configure(fg_color=C_BG)
        self.root = self.winfo_toplevel()
        self.session_states = session_states if session_states is not None else {}

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.config = _load_config()
        # Reuse the parent's TokenManager if one exists, otherwise build our
        # own. Either way the on-disk MSAL cache is shared with the Account
        # Creation section, so a sign-in in one section unlocks the other.
        existing = getattr(self.root, "token_mgr", None)
        if existing is None:
            existing = TokenManager(self.config)
            self.root.token_mgr = existing
        self.root.token_mgr = existing
        self.token_mgr = existing
        # Graph is what the checks actually run over. AccountTab reaches it
        # via self.root, which is THIS section (tabs get root=self).
        self.graph_mgr = GraphManager(self.config)

        self.accounts: list[dict]              = self._load_accounts()
        self.tabs:     dict[str, AccountTab]   = {}
        self.cards:    dict[str, AccountCard]  = {}
        self._selected_rto: str | None         = None
        self._rto_reports: dict[str, dict[str, list[str]]] = {}
        self._combined_report_text = ""

        self._build_ui()
        self._sync_all()

    # ------------------------------------------------------------------
    def _load_accounts(self):
        if ACCOUNTS_FILE.exists():
            try:
                return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    def _save_accounts(self):
        ACCOUNTS_FILE.write_text(json.dumps(self.accounts, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    def _build_ui(self):
        sidebar = ctk.CTkFrame(self, width=230, fg_color=C_SIDEBAR,
                               corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        hdr = ctk.CTkFrame(sidebar, fg_color=C_HEADER, height=64, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Student Checker",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=C_TEXT).pack(side="left", padx=16, pady=18)

        ctk.CTkLabel(sidebar, text="ACCOUNTS",
                     text_color=C_DIM, font=ctk.CTkFont(size=10, weight="bold")
                     ).pack(anchor="w", padx=14, pady=(14, 4))

        self.account_scroll = ctk.CTkScrollableFrame(
            sidebar, fg_color="transparent", corner_radius=0
        )
        self.account_scroll.pack(fill="both", expand=True, pady=(0, 4))

        sep = ctk.CTkFrame(sidebar, fg_color="#2a2d42", height=1, corner_radius=0)
        sep.pack(fill="x", padx=10)

        btn_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=12)

        ctk.CTkButton(btn_row, text="＋ Add Account", height=34,
                      fg_color=C_ACCENT, hover_color=C_ACCENT_HOV,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._add).pack(fill="x", pady=(0, 6))

        edit_del = ctk.CTkFrame(btn_row, fg_color="transparent")
        edit_del.pack(fill="x")
        ctk.CTkButton(edit_del, text="Edit", height=30, fg_color=C_CARD,
                      hover_color=C_CARD_HOV, command=self._edit
                      ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(edit_del, text="Delete", height=30,
                      fg_color="#3d1515", hover_color="#5a1f1f",
                      command=self._delete
                      ).pack(side="left", fill="x", expand=True)

        right = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        right.pack(side="left", fill="both", expand=True)

        report_wrap = ctk.CTkFrame(right, fg_color=C_FRAME, corner_radius=10)
        report_wrap.pack(fill="x", padx=10, pady=(10, 0))

        report_head = ctk.CTkFrame(report_wrap, fg_color="transparent")
        report_head.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(
            report_head,
            text="Combined Issue Report",
            text_color=C_DIM,
            font=ctk.CTkFont(size=11),
        ).pack(side="left")
        self.copy_report_btn = ctk.CTkButton(
            report_head,
            text="Copy Report",
            width=104,
            height=28,
            fg_color=C_CARD,
            hover_color=C_CARD_HOV,
            state="disabled",
            command=self.copy_combined_report,
        )
        self.copy_report_btn.pack(side="right", padx=(6, 0))
        self.clear_report_btn = ctk.CTkButton(
            report_head,
            text="Clear",
            width=70,
            height=28,
            fg_color=C_CARD,
            hover_color=C_CARD_HOV,
            command=self.clear_combined_report,
        )
        self.clear_report_btn.pack(side="right")

        self.report = tk.Text(
            report_wrap,
            bg=C_LOG,
            fg=C_TEXT,
            font=("Segoe UI", 10),
            relief="flat",
            wrap="word",
            state="disabled",
            bd=0,
            height=7,
            highlightthickness=0,
            insertbackground=C_TEXT,
            selectbackground="#2a3f6e",
        )
        self.report.pack(fill="x", padx=10, pady=(0, 10))
        self._set_combined_report_text("")

        self.tabview = ctk.CTkTabview(right, fg_color=C_BG,
                                      segmented_button_fg_color=C_SIDEBAR,
                                      segmented_button_selected_color=C_ACCENT,
                                      segmented_button_selected_hover_color=C_ACCENT_HOV,
                                      segmented_button_unselected_color=C_SIDEBAR,
                                      segmented_button_unselected_hover_color=C_CARD_HOV)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=10)

        self._empty_lbl = ctk.CTkLabel(
            right,
            text="No accounts yet.\nClick  ＋ Add Account  to get started.",
            text_color=C_DIM, font=ctk.CTkFont(size=14),
            justify="center",
        )

    # ------------------------------------------------------------------
    def _sync_all(self):
        existing = {a["rto"] for a in self.accounts}

        for rto in list(self.tabs):
            if rto not in existing:
                try:
                    self.tabview.delete(rto)
                except Exception:
                    pass
                del self.tabs[rto]

        for rto in list(self.cards):
            if rto not in existing:
                self.cards[rto].destroy()
                del self.cards[rto]

        for acc in self.accounts:
            rto = acc["rto"]
            if rto not in self.cards:
                card = AccountCard(self.account_scroll, acc,
                                   on_click=self._select_by_rto)
                self.cards[rto] = card
            if rto not in self.tabs:
                self.tabs[rto] = AccountTab(self.tabview, acc, self, self.cards[rto])

        if self.accounts:
            self._empty_lbl.place_forget()
        else:
            self._empty_lbl.place(relx=0.5, rely=0.5, anchor="center")

    def _select_by_rto(self, rto: str):
        if self._selected_rto and self._selected_rto in self.cards:
            self.cards[self._selected_rto].set_selected(False)
        self._selected_rto = rto
        if rto in self.cards:
            self.cards[rto].set_selected(True)
        try:
            self.tabview.set(rto)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _selected_account(self) -> dict | None:
        if self._selected_rto:
            for a in self.accounts:
                if a["rto"] == self._selected_rto:
                    return a
        return None

    def _add(self):
        """Add a tenant via the shared setup wizard dialog. Writes both
        config.json and student_accounts.json so the new RTO appears in
        the Account Creation section as well."""
        dlg = CredentialDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return
        try:
            _upsert_config_account(dlg.result)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
            return
        # Reload from disk — setup_wizard wrote student_accounts.json for us.
        self.accounts = self._load_accounts()
        self.config = _load_config()
        self.token_mgr = TokenManager(self.config)
        self.root.token_mgr = self.token_mgr
        self.graph_mgr = GraphManager(self.config)
        self._sync_all()
        rto = dlg.result["rto"].upper()
        self._select_by_rto(rto)

    def record_rto_report(self, rto: str, issues: dict[str, list[str]]):
        self._rto_reports[rto] = {
            "nolic": list(dict.fromkeys(issues.get("nolic", []))),
            "nogroup": list(dict.fromkeys(issues.get("nogroup", []))),
            "missing": list(dict.fromkeys(issues.get("missing", []))),
        }
        self._refresh_combined_report()

    def clear_rto_report(self, rto: str):
        if rto in self._rto_reports:
            del self._rto_reports[rto]
            self._refresh_combined_report()

    def clear_combined_report(self):
        self._rto_reports.clear()
        self._refresh_combined_report()

    def copy_combined_report(self):
        if not self._combined_report_text:
            messagebox.showinfo("No report", "Run at least one RTO check first.", parent=self)
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._combined_report_text)
        messagebox.showinfo("Report copied", "Combined report copied to clipboard.", parent=self)

    def _refresh_combined_report(self):
        self._combined_report_text = self._build_combined_report()
        self._set_combined_report_text(self._combined_report_text)
        self.copy_report_btn.configure(
            state="normal" if self._combined_report_text else "disabled"
        )

    def _build_combined_report(self) -> str:
        issue_sections = []
        for rto in sorted(self._rto_reports):
            issues = self._rto_reports[rto]
            lines = []
            if issues.get("nolic"):
                lines.append(f"- No license: {self._format_ids(issues['nolic'])}")
            if issues.get("nogroup"):
                lines.append(f"- Not in group: {self._format_ids(issues['nogroup'])}")
            if issues.get("missing"):
                lines.append(f"- Missing account: {self._format_ids(issues['missing'])}")
            if lines:
                issue_sections.append(f"{rto}:\n" + "\n".join(lines))

        if not self._rto_reports:
            return ""

        if not issue_sections:
            return (
                "Hi team,\n\n"
                "No license, group, or missing account issues were found for the RTOs checked.\n\n"
                "Thank you."
            )

        return (
            "Hi team,\n\n"
            "Following RTOs have student account issues:\n\n"
            + "\n\n".join(issue_sections)
            + "\n\nThank you."
        )

    def _format_ids(self, ids: list[str]) -> str:
        shown = ", ".join(ids[:20])
        extra = len(ids) - 20
        if extra > 0:
            shown += f", +{extra} more"
        return shown

    def _set_combined_report_text(self, text: str):
        self.report.configure(state="normal")
        self.report.delete("1.0", tk.END)
        if text:
            self.report.insert("1.0", text)
        self.report.configure(state="disabled")

    def _edit(self):
        acc = self._selected_account()
        if not acc:
            messagebox.showinfo("No selection", "Click an account first.", parent=self)
            return
        old_rto = acc["rto"]
        dlg = EditAccountDialog(self, acc)
        self.wait_window(dlg)
        if not dlg.result:
            return

        idx = next(i for i, a in enumerate(self.accounts) if a["rto"] == old_rto)
        self.accounts[idx] = dlg.result

        if old_rto in self.cards:
            self.cards[old_rto].update_account(dlg.result)
        if old_rto in self.tabs:
            self.tabs[old_rto].account = dlg.result

        self._save_accounts()
        self._sync_all()
        self._select_by_rto(old_rto)

    def _delete(self):
        acc = self._selected_account()
        if not acc:
            messagebox.showinfo("No selection", "Click an account first.", parent=self)
            return
        if not messagebox.askyesno("Delete Account",
                                   f"Remove '{acc['rto']}' from Student Checker?",
                                   parent=self):
            return
        self.accounts = [a for a in self.accounts if a["rto"] != acc["rto"]]
        self._selected_rto = None
        self._save_accounts()
        self._sync_all()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("VCIT Student Checker")
        self.geometry("1300x820")
        self.minsize(960, 640)
        self.configure(fg_color=C_BG)
        self.section = StudentCheckerSection(self)
        self.section.pack(fill="both", expand=True)


# ---------------------------------------------------------------------------
# Graph check
# ---------------------------------------------------------------------------
def _graph_check_students(graph_mgr, token, rto, student_ids, group_name,
                          progress, should_stop=None):
    """Check all students via Microsoft Graph.

    Two bulk fetches (all users, group members), then local matching.
    Emits the same log line shape as the old EXO checker so the existing
    tallying, colouring, issue report, and last-ID tracking keep working.
    License status comes from assignedLicenses — real data, not inferred
    from mailbox existence.
    """
    progress(f"[{rto}] Using Microsoft Graph.")
    progress(f"[{rto}] Found {len(student_ids)} students to check.")

    progress(f"[{rto}] Fetching all users (one bulk call)...")
    users = graph_mgr.list_users_basic(token)
    progress(f"[{rto}] Loaded {len(users)} users.")

    if should_stop and should_stop():
        progress(f"[{rto}] Stopped.")
        return

    group_members = set()
    group_email = ""
    progress(f"[{rto}] Fetching group members for: {group_name} ...")
    try:
        group = graph_mgr.find_group(token, group_name)
        if group:
            group_email = group.get("mail") or ""
            member_mails = graph_mgr.list_group_member_mails(token, group["id"])
            group_members = {m.lower() for m in member_mails if m}
            progress(f"[{rto}] Loaded {len(group_members)} group members. Checking students...")
        else:
            progress(f"[{rto}] WARNING: Group '{group_name}' not found.")
            progress(f"[{rto}] Continuing without group check...")
    except Exception as exc:
        progress(f"[{rto}] WARNING: Could not load group '{group_name}' - {exc}")
        progress(f"[{rto}] Continuing without group check...")

    for student_id in student_ids:
        if should_stop and should_stop():
            progress(f"[{rto}] Stopped.")
            return
        sid = student_id.strip()
        if not sid:
            continue
        sid_lower = sid.lower()

        user = next(
            (u for u in users if sid_lower in (u.get("displayName") or "").lower()),
            None,
        )
        if user is None:
            progress(
                f"[{rto}] StudentID: {sid} | Account: MISSING "
                "| Has Group: No | Department: N/A"
            )
            continue

        department = user.get("department") or "Not set"
        mail = (user.get("mail") or user.get("userPrincipalName") or "").lower()
        has_group = bool(mail and mail in group_members)
        if has_group and group_email:
            group_info = f"Yes - {group_email}"
        elif has_group:
            group_info = "Yes"
        else:
            group_info = "No"

        account_state = "EXISTS" if user.get("assignedLicenses") else "EXISTS (NO LICENSE)"
        progress(
            f"[{rto}] StudentID: {sid} | Account: {account_state} "
            f"| Has Group: {group_info} | Department: {department}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _color_tag(line: str) -> str:
    u = line.upper()
    if "| ACCOUNT:" not in u:
        return "cyan"
    if "ACCOUNT: MISSING" in u:
        return "red"
    if "NO LICENSE" in u:
        return "magenta"
    if "HAS GROUP: YES" in u:
        return "green"
    if "HAS GROUP: NO" in u:
        return "yellow"
    return "white"


def _tally(stats: dict, line: str):
    u = line.upper()
    if "| ACCOUNT:" not in u:
        return
    stats["total"] = stats.get("total", 0) + 1
    if "ACCOUNT: MISSING" in u:
        stats["missing"] = stats.get("missing", 0) + 1
    elif "NO LICENSE" in u:
        stats["nolic"] = stats.get("nolic", 0) + 1
    elif "HAS GROUP: NO" in u:
        stats["nogroup"] = stats.get("nogroup", 0) + 1
    else:
        stats["ok"] = stats.get("ok", 0) + 1


def _student_id_from_line(line: str) -> str:
    upper = line.upper()
    if "| ACCOUNT:" not in upper:
        return ""
    match = re.search(r"StudentID:\s*([^|]+)", line, flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def _student_ids_from_csv(csv_path: str, id_col: str) -> list[str]:
    path = Path(csv_path)
    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            return []

        col = id_col
        if col not in reader.fieldnames:
            wanted = id_col.strip().lower()
            for field in reader.fieldnames:
                if field.strip().lower() == wanted:
                    col = field
                    break
            else:
                raise ValueError(f"column '{id_col}' not found")

        ids = []
        for row in reader:
            value = (row.get(col) or "").strip()
            if value:
                ids.append(value)
        return ids


def _last_id_column_for_rto(rto: str) -> str:
    key = rto.strip().upper()
    return LAST_ID_RTO_LABELS.get(key, key)


def _format_last_id_value(rto: str, student_id: str) -> str:
    return f"{_last_id_column_for_rto(rto)}-{student_id.strip()}"


def _read_last_id_rows() -> list[dict]:
    if not LAST_IDS_FILE.exists():
        return []
    text = LAST_IDS_FILE.read_text(encoding="utf-8-sig")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "," if "," in first_line else ";"
    with LAST_IDS_FILE.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        if not reader.fieldnames:
            return []
        if reader.fieldnames == LAST_ID_COLUMNS:
            return [{col: row.get(col, "") for col in LAST_ID_COLUMNS} for row in reader]

        if "RTO" in reader.fieldnames and "Last Student ID" in reader.fieldnames:
            grouped: dict[str, list[str]] = {}
            for row in reader:
                date_value = row.get("Date", "")
                value = (row.get("Last Student ID") or "").strip()
                if date_value and value:
                    grouped.setdefault(date_value, []).append(value)
            return [
                {"Date": date_value, "Last Student IDs": "; ".join(values)}
                for date_value, values in grouped.items()
            ]

        if "Date" in reader.fieldnames:
            rows = []
            for row in reader:
                date_value = row.get("Date", "")
                values = []
                for field in reader.fieldnames:
                    if field == "Date":
                        continue
                    value = (row.get(field) or "").strip()
                    if value:
                        values.append(value)
                rows.append({
                    "Date": date_value,
                    "Last Student IDs": "; ".join(values),
                })
            return rows

        return []


def _write_last_id_rows(rows: list[dict]):
    with LAST_IDS_FILE.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LAST_ID_COLUMNS, delimiter=",", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _upsert_last_student_id(rto: str, value: str):
    date_value = datetime.datetime.now().strftime("%Y-%m-%d")
    rto_label = _last_id_column_for_rto(rto)
    with LAST_IDS_LOCK:
        rows = _read_last_id_rows()
        for row in rows:
            if row.get("Date") == date_value:
                entries = _split_last_id_entries(row.get("Last Student IDs", ""))
                entries = [
                    entry for entry in entries
                    if not entry.upper().startswith(f"{rto_label}-")
                ]
                entries.append(value)
                row["Last Student IDs"] = "; ".join(entries)
                break
        else:
            rows.append({
                "Date": date_value,
                "Last Student IDs": value,
            })
        _write_last_id_rows(rows)


def _split_last_id_entries(value: str) -> list[str]:
    return [part.strip() for part in value.split(";") if part.strip()]


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()
