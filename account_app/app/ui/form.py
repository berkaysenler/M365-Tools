import tkinter as tk
from tkinter import ttk, messagebox

from app.account import (
    AccountData,
    generate_upn,
    generate_display_name,
    generate_password,
)
from app.ui.group_search import GroupSearch
from app.ui.theme import C_BG, C_DIM
from app.ui.tkutil import (
    GROUP_TAG_COLORS,
    LICENSE_TAG_COLORS,
    fit_combo_popdown,
    render_tags,
)


LICENSE_PLACEHOLDER = "Add a license…"


class FormFrame(ttk.Frame):
    """Form for entering new account details. Pure data entry — no live
    fetches against Exchange Online. Group selection works by typing names
    (matches the sample.md script approach: just call
    Add-DistributionGroupMember against a known name)."""

    def __init__(self, parent, on_change, on_submit):
        super().__init__(parent)
        self.on_change = on_change
        self.on_submit = on_submit

        self._rto = None
        self._domain = None
        self._prefix = ""
        self._default_group_name = ""
        self._domain_options: list[dict] = []
        self._selected_groups: list[dict] = []
        self._license_options: list[dict] = []
        self._selected_licenses: list[dict] = []
        self._group_options: list[dict] = []
        self._display_auto = True
        self._upn_auto = True

        self.columnconfigure(0, weight=1)
        self._build()

    # ------------------------------------------------------------------
    # Build — compact 3-column grid: fields on top, output panel lives
    # below the form (see main_window layout).
    # ------------------------------------------------------------------

    def _build(self):
        f = self
        for col in range(3):
            f.columnconfigure(col, weight=1, uniform="formcol")

        self._vars = {}
        self._entries = {}

        ttk.Label(f, text="New Account", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 0)
        )
        ttk.Separator(f, orient="horizontal").grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 6)
        )

        self._field(f, "First Name *", "first_name", row=2, col=0)
        self._field(f, "Last Name *", "last_name", row=2, col=1)
        self._combo(f, "Create For", "create_for", [], row=2, col=2)

        self._field(f, "Display Name", "display_name", row=4, col=0)
        self._field(f, "UPN", "upn", row=4, col=1)
        self._field(f, "Job Title", "title", row=4, col=2)

        self._field(f, "Department", "department", row=6, col=0)
        self._field(f, "Office Location", "office_location", row=6, col=1)
        self._field(f, "Manager", "manager", row=6, col=2)

        self._license_picker(f, row=8)
        self._field(f, "Start Date (YYYY-MM-DD)", "start_date", row=8, col=2)

        self._group_picker(f, row=11)
        self._password_field(f, row=11, col=2)

        ttk.Label(
            f,
            text=(
                "Display name and UPN are built automatically from the name and "
                "selected domain — edit either to override. Licenses and groups "
                "load after connecting. Manager can be a username (e.g. j.smith) "
                "or a full email address."
            ),
            foreground=C_DIM, font=("Segoe UI", 8),
            wraplength=620, justify="left",
        ).grid(row=14, column=0, columnspan=2, sticky="sw", padx=10, pady=(6, 10))

        self._submit_btn = ttk.Button(
            f, text="Create Account", style="Accent.TButton",
            command=self._submit, state="disabled",
        )
        self._submit_btn.grid(row=14, column=2, sticky="sew", padx=10, pady=(6, 10))

        self._setup_traces()

    def _field(self, parent, label, key, row, col, colspan=1, readonly=False):
        ttk.Label(parent, text=label).grid(
            row=row, column=col, columnspan=colspan, sticky="w", padx=10, pady=(4, 1)
        )
        var = tk.StringVar()
        entry = ttk.Entry(
            parent, textvariable=var, state="readonly" if readonly else "normal"
        )
        entry.grid(
            row=row + 1, column=col, columnspan=colspan, sticky="ew",
            padx=10, pady=(0, 4),
        )
        self._vars[key] = var
        self._entries[key] = entry

    def _combo(self, parent, label, key, values, row, col, colspan=1, editable=False):
        ttk.Label(parent, text=label).grid(
            row=row, column=col, columnspan=colspan, sticky="w", padx=10, pady=(4, 1)
        )
        var = tk.StringVar()
        combo = ttk.Combobox(
            parent, textvariable=var, values=values,
            state="normal" if editable else "readonly",
        )
        combo.grid(
            row=row + 1, column=col, columnspan=colspan, sticky="ew",
            padx=10, pady=(0, 4),
        )
        if values:
            combo.set(values[0])
        self._vars[key] = var
        self._entries[key] = combo

    def _password_field(self, parent, row, col):
        ttk.Label(parent, text="Temporary Password *").grid(
            row=row, column=col, sticky="w", padx=10, pady=(4, 1)
        )
        wrap = ttk.Frame(parent)
        wrap.grid(row=row + 1, column=col, sticky="ew", padx=10, pady=(0, 4))
        wrap.columnconfigure(0, weight=1)

        var = tk.StringVar()
        entry = ttk.Entry(wrap, textvariable=var)
        entry.grid(row=0, column=0, sticky="ew")

        ttk.Button(
            wrap, text="Generate",
            command=lambda: var.set(generate_password()),
        ).grid(row=0, column=1, sticky="e", padx=(6, 0))

        self._vars["temp_password"] = var
        self._entries["temp_password"] = entry
        ttk.Label(
            parent,
            text="Not required to change on first sign-in.",
            foreground=C_DIM, font=("Segoe UI", 8),
        ).grid(row=row + 2, column=col, sticky="nw", padx=10, pady=(1, 4))

    def _license_picker(self, parent, row):
        ttk.Label(parent, text="Licenses").grid(
            row=row, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 1)
        )
        var = tk.StringVar(value=LICENSE_PLACEHOLDER)
        combo = ttk.Combobox(
            parent, textvariable=var, values=[LICENSE_PLACEHOLDER],
            state="readonly",
        )
        combo.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 2))
        combo.bind("<<ComboboxSelected>>", self._on_license_pick)
        self._vars["license"] = var
        self._entries["license"] = combo
        self._license_tags_frame = tk.Frame(parent, bg=C_BG)
        self._license_tags_frame.grid(
            row=row + 2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 4)
        )

    def _on_license_pick(self, _event=None):
        label = self._vars["license"].get()
        self._vars["license"].set(LICENSE_PLACEHOLDER)
        option = next(
            (o for o in self._license_options if o.get("label") == label),
            None,
        )
        if not option:
            return
        if any(
            sel.get("skuId") == option.get("skuId")
            for sel in self._selected_licenses
        ):
            return
        self._selected_licenses.append(option)
        self._render_license_tags()
        self._emit_change()

    def _render_license_tags(self):
        # One tag per line — license names are long and side-by-side tags
        # overflow the narrow form column.
        render_tags(
            self._license_tags_frame, self._selected_licenses,
            text_of=lambda o: o.get("name", ""),
            on_remove=self._remove_license,
            vertical=True, **LICENSE_TAG_COLORS,
        )

    def _remove_license(self, option):
        self._selected_licenses = [
            o for o in self._selected_licenses
            if o.get("skuId") != option.get("skuId")
        ]
        self._render_license_tags()
        self._emit_change()

    def _group_picker(self, parent, row):
        ttk.Label(parent, text="Distribution Groups").grid(
            row=row, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 1)
        )

        frame = ttk.Frame(parent)
        frame.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 2))
        frame.columnconfigure(0, weight=1)

        self._group_search = GroupSearch(frame, on_pick=self._add_group)
        self._group_search.grid(row=0, column=0, sticky="ew")

        # Selected tags
        self._tags_frame = tk.Frame(parent, bg=C_BG)
        self._tags_frame.grid(
            row=row + 2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 4)
        )

    def set_group_options(self, options):
        """Fetched tenant groups: [{id, displayName, mail, recipientType}]."""
        self._group_options = list(options or [])
        self._group_search.set_options(self._group_options)

    def _add_group(self, group):
        name = group.get("displayName", "").strip()
        if not name:
            return
        if any(
            g.get("displayName", "").lower() == name.lower()
            for g in self._selected_groups
        ):
            return
        self._selected_groups.append(group)
        self._render_tags()
        self._emit_change()

    # ------------------------------------------------------------------
    # Traces and callbacks
    # ------------------------------------------------------------------

    def _setup_traces(self):
        self._vars["first_name"].trace_add("write", self._on_name_change)
        self._vars["last_name"].trace_add("write", self._on_name_change)
        self._vars["create_for"].trace_add("write", self._on_create_for_change)
        self._vars["display_name"].trace_add("write", self._on_display_name_change)
        self._vars["upn"].trace_add("write", self._on_upn_change)

        for key in [
            "title", "department", "office_location",
            "manager", "start_date", "temp_password",
        ]:
            self._vars[key].trace_add("write", lambda *a: self._emit_change())

    def _on_name_change(self, *_):
        first = self._vars["first_name"].get()
        last = self._vars["last_name"].get()
        if self._display_auto:
            self._vars["display_name"].set(
                generate_display_name(first, last, self._prefix)
            )
        if self._domain and self._upn_auto:
            self._set_entry(self._entries["upn"], generate_upn(first, last, self._domain))
        self._emit_change()

    def _on_create_for_change(self, *_):
        selected = self._vars["create_for"].get()
        match = next(
            (o for o in self._domain_options if o.get("label") == selected),
            None,
        )
        if not match:
            return

        self._rto = match.get("rto", "")
        self._domain = match.get("domain", "")
        self._prefix = match.get("prefix", "")
        self._default_group_name = match.get("default_group", "")

        first = self._vars["first_name"].get()
        last = self._vars["last_name"].get()
        if self._display_auto:
            self._vars["display_name"].set(
                generate_display_name(first, last, self._prefix)
            )
        if self._upn_auto:
            self._set_entry(
                self._entries["upn"], generate_upn(first, last, self._domain)
            )
        self._emit_change()

    def _on_display_name_change(self, *_):
        first = self._vars["first_name"].get()
        last = self._vars["last_name"].get()
        current = self._vars["display_name"].get()
        self._display_auto = (
            current == generate_display_name(first, last, self._prefix)
            or current == generate_display_name(first, last)
        )

    def _on_upn_change(self, *_):
        first = self._vars["first_name"].get()
        last = self._vars["last_name"].get()
        current = self._vars["upn"].get()
        self._upn_auto = current == generate_upn(first, last, self._domain or "")

    def _seed_default_group(self):
        self._selected_groups = []
        if self._default_group_name:
            self._selected_groups.append({
                "id": self._default_group_name,
                "displayName": self._default_group_name,
                "mail": "",
                "recipientType": "",
            })
        self._render_tags()

    def _render_tags(self):
        render_tags(
            self._tags_frame, self._selected_groups,
            text_of=lambda g: g.get("displayName", ""),
            on_remove=self._remove_group,
            **GROUP_TAG_COLORS,
        )

    def _remove_group(self, group):
        self._selected_groups = [
            g for g in self._selected_groups
            if g.get("displayName") != group.get("displayName")
        ]
        self._render_tags()
        self._emit_change()

    # ------------------------------------------------------------------
    # Account data
    # ------------------------------------------------------------------

    def _emit_change(self):
        self.on_change(self._build_account())

    def _build_account(self):
        acc = AccountData()
        acc.first_name = self._vars["first_name"].get()
        acc.last_name = self._vars["last_name"].get()
        acc.display_name = self._vars["display_name"].get()
        acc.upn = self._vars["upn"].get()
        acc.rto = self._rto or ""
        acc.domain = self._domain or ""
        acc.title = self._vars["title"].get()
        acc.department = self._vars["department"].get()
        acc.office_location = self._vars["office_location"].get()
        manager_text = self._vars["manager"].get().strip()
        acc.manager_display = manager_text
        if manager_text:
            acc.manager_upn = (
                manager_text if "@" in manager_text else f"{manager_text}@{acc.domain}"
            )
        else:
            acc.manager_upn = ""
        acc.manager_id = ""
        acc.start_date = self._vars["start_date"].get()
        acc.distribution_groups = list(self._selected_groups)
        acc.licenses = [
            {"skuId": o.get("skuId", ""), "name": o.get("name", "")}
            for o in self._selected_licenses
        ]
        return acc

    def _submit(self):
        acc = self._build_account()
        temp_pw = self._vars["temp_password"].get().strip()
        errors = []
        if not acc.first_name:
            errors.append("First Name is required")
        if not acc.last_name:
            errors.append("Last Name is required")
        if not acc.upn:
            errors.append("UPN could not be generated — check First and Last Name")
        if not temp_pw:
            errors.append("Temporary Password is required")
        if errors:
            messagebox.showwarning("Validation", "\n".join(errors))
            return
        self.on_submit(acc, temp_pw)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_rto(
        self, rto, domain, prefix="", default_group_name="", domain_options=None
    ):
        self._rto = rto
        self._domain = domain
        self._prefix = prefix
        self._default_group_name = default_group_name
        self._domain_options = domain_options or [{
            "rto": rto,
            "domain": domain,
            "prefix": prefix,
            "default_group": default_group_name,
            "label": f"{rto} ({domain})",
        }]

        labels = [o["label"] for o in self._domain_options]
        create_for = self._entries["create_for"]
        create_for.config(values=labels)
        if labels:
            current = next(
                (
                    o["label"] for o in self._domain_options
                    if o.get("rto") == rto and o.get("domain") == domain
                ),
                labels[0],
            )
            self._vars["create_for"].set(current)

        first = self._vars["first_name"].get()
        last = self._vars["last_name"].get()
        if first and last:
            if self._upn_auto:
                self._set_entry(self._entries["upn"], generate_upn(first, last, domain))
            if self._display_auto:
                self._vars["display_name"].set(
                    generate_display_name(first, last, self._prefix)
                )

        self._emit_change()

    def set_license_options(self, options):
        """options: [{skuId, name, label}] for the connected tenant."""
        self._license_options = list(options or [])
        values = [LICENSE_PLACEHOLDER] + [o["label"] for o in self._license_options]
        self._entries["license"].config(values=values)
        fit_combo_popdown(self._entries["license"])
        self._vars["license"].set(LICENSE_PLACEHOLDER)
        # Drop selected licenses that no longer exist in this tenant.
        valid = {o.get("skuId") for o in self._license_options}
        kept = [o for o in self._selected_licenses if o.get("skuId") in valid]
        if len(kept) != len(self._selected_licenses):
            self._selected_licenses = kept
            self._render_license_tags()
            self._emit_change()

    def set_enabled(self, enabled):
        state_normal = "normal"
        state_disabled = "disabled"
        for key, widget in self._entries.items():
            if isinstance(widget, ttk.Combobox):
                widget.config(state="readonly" if enabled else state_disabled)
            else:
                widget.config(state=state_normal if enabled else state_disabled)
        self._submit_btn.config(state=state_normal if enabled else state_disabled)

    def set_submit_state(self, enabled):
        self._submit_btn.config(state="normal" if enabled else "disabled")

    def clear_live_data(self):
        self._selected_groups = []
        self._render_tags()
        self._selected_licenses = []
        self._render_license_tags()

    @staticmethod
    def _set_entry(entry, value):
        entry.config(state="normal")
        entry.delete(0, tk.END)
        entry.insert(0, value)
