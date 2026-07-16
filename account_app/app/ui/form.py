import tkinter as tk
from tkinter import ttk, messagebox

from app.account import (
    AccountData,
    generate_upn,
    generate_display_name,
    generate_password,
)
from app.ui.group_search import GroupSearch
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
        self.rowconfigure(0, weight=1)
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        canvas = tk.Canvas(self, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self._inner = ttk.Frame(canvas)

        self._inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        win_id = canvas.create_window((0, 0), window=self._inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        canvas.bind(
            "<Enter>",
            lambda e: canvas.bind_all(
                "<MouseWheel>",
                lambda ev: canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units"),
            ),
        )
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        f = self._inner
        f.columnconfigure(0, weight=1)

        self._vars = {}
        self._entries = {}

        r = 0
        r = self._section(f, "Identity", r)
        r = self._field(f, "First Name *", "first_name", r)
        r = self._field(f, "Last Name *", "last_name", r)
        r = self._combo(f, "Create For", "create_for", [], r)
        r = self._field(
            f, "Display Name", "display_name", r,
            hint="Auto-built as '<RTO prefix> First Last' — edit to override.",
        )
        r = self._field(
            f, "UPN", "upn", r,
            hint="Auto-built from first initial + last name + selected domain. Edit if needed.",
        )

        r = self._section(f, "Role", r)
        r = self._field(f, "Job Title", "title", r)
        r = self._field(f, "Department", "department", r)
        r = self._field(f, "Office Location", "office_location", r)
        r = self._field(
            f, "Manager", "manager", r,
            hint="Type the manager's username, e.g. y.ong, or a full UPN.",
        )

        r = self._section(f, "Access", r)
        r = self._license_picker(f, r)
        r = self._field(
            f, "Start Date (YYYY-MM-DD)", "start_date", r,
            hint="Recorded in the log only — would feed onboarding workflows via Graph API.",
        )
        r = self._group_picker(f, r)

        r = self._section(f, "Create Account", r)
        r = self._password_field(f, r)

        self._submit_btn = ttk.Button(
            f, text="Create Account", command=self._submit, state="disabled"
        )
        self._submit_btn.grid(row=r, column=0, sticky="ew", padx=10, pady=(6, 20))

        self._setup_traces()

    def _section(self, parent, title, row):
        ttk.Label(parent, text=title, font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, sticky="w", padx=10, pady=(14, 2)
        )
        ttk.Separator(parent, orient="horizontal").grid(
            row=row + 1, column=0, sticky="ew", padx=10, pady=(0, 6)
        )
        return row + 2

    def _field(self, parent, label, key, row, readonly=False, hint=None):
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=10, pady=(0, 1)
        )
        var = tk.StringVar()
        entry = ttk.Entry(
            parent, textvariable=var, state="readonly" if readonly else "normal"
        )
        entry.grid(row=row + 1, column=0, sticky="ew", padx=10, pady=(0, 0 if hint else 6))
        self._vars[key] = var
        self._entries[key] = entry
        if hint:
            ttk.Label(
                parent, text=hint, foreground="#666", font=("Segoe UI", 8),
                wraplength=380, justify="left",
            ).grid(row=row + 2, column=0, sticky="w", padx=10, pady=(1, 6))
            return row + 3
        return row + 2

    def _combo(self, parent, label, key, values, row, editable=False, hint=None):
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=10, pady=(0, 1)
        )
        var = tk.StringVar()
        combo = ttk.Combobox(
            parent, textvariable=var, values=values,
            state="normal" if editable else "readonly",
        )
        combo.grid(row=row + 1, column=0, sticky="ew", padx=10, pady=(0, 0 if hint else 6))
        if values:
            combo.set(values[0])
        self._vars[key] = var
        self._entries[key] = combo
        if hint:
            ttk.Label(
                parent, text=hint, foreground="#666", font=("Segoe UI", 8),
                wraplength=380, justify="left",
            ).grid(row=row + 2, column=0, sticky="w", padx=10, pady=(1, 6))
            return row + 3
        return row + 2

    def _password_field(self, parent, row):
        ttk.Label(parent, text="Temporary Password *").grid(
            row=row, column=0, sticky="w", padx=10, pady=(0, 1)
        )
        wrap = ttk.Frame(parent)
        wrap.grid(row=row + 1, column=0, sticky="ew", padx=10, pady=(0, 0))
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
            text="User will not be required to change this on first sign-in.",
            foreground="#666", font=("Segoe UI", 8),
        ).grid(row=row + 2, column=0, sticky="w", padx=10, pady=(1, 6))
        return row + 3

    def _license_picker(self, parent, row):
        ttk.Label(parent, text="Licenses").grid(
            row=row, column=0, sticky="w", padx=10, pady=(0, 1)
        )
        var = tk.StringVar(value=LICENSE_PLACEHOLDER)
        combo = ttk.Combobox(
            parent, textvariable=var, values=[LICENSE_PLACEHOLDER],
            state="readonly",
        )
        combo.grid(row=row + 1, column=0, sticky="ew", padx=10, pady=(0, 0))
        combo.bind("<<ComboboxSelected>>", self._on_license_pick)
        self._vars["license"] = var
        self._entries["license"] = combo
        ttk.Label(
            parent,
            text="Pick licenses one at a time — each shows as a tag below. "
                 "Licenses load after connecting and are assigned via "
                 "Microsoft Graph right after the mailbox is created.",
            foreground="#666", font=("Segoe UI", 8),
            wraplength=380, justify="left",
        ).grid(row=row + 2, column=0, sticky="w", padx=10, pady=(1, 0))
        self._license_tags_frame = tk.Frame(parent, bg="#f0f0f0")
        self._license_tags_frame.grid(
            row=row + 3, column=0, sticky="ew", padx=10, pady=(2, 6)
        )
        return row + 4

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
            row=row, column=0, sticky="w", padx=10, pady=(0, 1)
        )

        frame = ttk.Frame(parent)
        frame.grid(row=row + 1, column=0, sticky="ew", padx=10, pady=(0, 6))
        frame.columnconfigure(0, weight=1)

        self._group_search = GroupSearch(
            frame,
            on_pick=self._add_group,
            hint="Type to search the tenant's groups (loaded after connect) — "
                 "click a match or press Enter for the top one. Unknown names "
                 "are added as typed.",
        )
        self._group_search.grid(row=0, column=0, sticky="ew")

        # Selected tags
        self._tags_frame = tk.Frame(frame, bg="#f0f0f0")
        self._tags_frame.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        return row + 2

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
