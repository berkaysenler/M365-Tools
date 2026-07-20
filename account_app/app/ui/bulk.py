import tkinter as tk
from tkinter import ttk, messagebox

from app.account import (
    AccountData,
    generate_display_name,
    generate_password,
    generate_upn,
)
from app.ui.form import LICENSE_PLACEHOLDER
from app.ui.group_search import GroupSearch
from app.ui.theme import (
    C_BG,
    C_DIM,
    C_FIELD,
    C_STRIPE_EVEN,
    C_STRIPE_ODD,
    C_TEXT,
)
from app.ui.tkutil import (
    GROUP_TAG_COLORS,
    LICENSE_TAG_COLORS,
    fit_combo_popdown,
    render_tags,
)


class BulkCreationFrame(ttk.Frame):
    def __init__(self, parent, on_submit):
        super().__init__(parent)
        self.on_submit = on_submit

        self._rto = ""
        self._domain = ""
        self._prefix = ""
        self._default_group_name = ""
        self._domain_options: list[dict] = []
        self._rows: list[tuple[AccountData, str]] = []
        self._license_catalog: dict[str, list[dict]] = {}  # auth_rto -> options
        self._group_catalog: dict[str, list[dict]] = {}    # auth_rto -> groups
        self._selected_licenses: list[dict] = []
        self._selected_groups: list[dict] = []
        self._enabled = False
        self._submitting = False
        self._upn_auto = True

        self.columnconfigure(0, weight=1)
        # Creation Output (row 4) gets twice the stretch of the pending list.
        self.rowconfigure(2, weight=1)
        self.rowconfigure(4, weight=2)
        self._build()
        self.set_enabled(False)

    def _build(self):
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text="Bulk Account Creation",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w")

        form = ttk.LabelFrame(self, text="Add Account")
        form.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        for col in range(6):
            form.columnconfigure(col, weight=1)

        self._vars = {
            "first_name": tk.StringVar(),
            "last_name": tk.StringVar(),
            "create_for": tk.StringVar(),
            "title": tk.StringVar(),
            "department": tk.StringVar(),
            "office_location": tk.StringVar(),
            "manager": tk.StringVar(),
            "license": tk.StringVar(value=LICENSE_PLACEHOLDER),
            "password": tk.StringVar(),
            "upn": tk.StringVar(),
        }
        self._widgets: list[tk.Widget] = []
        self._inputs: dict[str, tk.Widget] = {}

        self._field(form, "First Name *", "first_name", 0, 0)
        self._field(form, "Last Name *", "last_name", 0, 1)
        self._combo(form, "Create For", "create_for", [], 0, 2)
        self._field(form, "Job Title", "title", 0, 3)
        self._field(form, "Department", "department", 0, 4)
        self._field(form, "Location", "office_location", 0, 5)
        self._field(form, "Manager", "manager", 2, 0)

        ttk.Label(form, text="Password").grid(row=2, column=1, sticky="w", padx=6)
        pw_wrap = ttk.Frame(form)
        pw_wrap.grid(row=3, column=1, sticky="ew", padx=6, pady=(1, 8))
        pw_wrap.columnconfigure(0, weight=1)
        pw = ttk.Entry(pw_wrap, textvariable=self._vars["password"])
        pw.grid(row=0, column=0, sticky="ew")
        gen_btn = ttk.Button(
            pw_wrap,
            text="Generate",
            command=lambda: self._vars["password"].set(generate_password()),
        )
        gen_btn.grid(row=0, column=1, padx=(4, 0))
        self._widgets.extend([pw, gen_btn])

        # Licenses on their own row: pick-to-add dropdown; each pick shows
        # as a removable green tag below (same pattern as the Single tab).
        ttk.Label(form, text="License (pick to add)").grid(row=4, column=0, columnspan=6, sticky="w", padx=6)
        license_combo = ttk.Combobox(
            form, textvariable=self._vars["license"],
            values=[LICENSE_PLACEHOLDER], state="readonly",
        )
        license_combo.grid(row=5, column=0, columnspan=6, sticky="ew", padx=6, pady=(1, 2))
        license_combo.bind("<<ComboboxSelected>>", self._on_license_pick)
        self._widgets.append(license_combo)
        self._inputs["license"] = license_combo
        self._license_tags = tk.Frame(form, bg=C_BG)
        self._license_tags.grid(row=6, column=0, columnspan=6, sticky="ew", padx=6, pady=(0, 8))

        # Groups on their own row: type-to-search picker; each pick shows as
        # a removable blue tag below.
        ttk.Label(form, text="Add Group (type to search)").grid(row=7, column=0, columnspan=6, sticky="w", padx=6)
        self._group_search = GroupSearch(form, on_pick=self._on_group_pick)
        self._group_search.grid(row=8, column=0, columnspan=6, sticky="new", padx=6, pady=(1, 2))
        self._group_tags = tk.Frame(form, bg=C_BG)
        self._group_tags.grid(row=9, column=0, columnspan=6, sticky="ew", padx=6, pady=(0, 8))

        ttk.Label(
            form,
            text="UPN (auto-built from first initial + last name + selected domain; edit to override)",
        ).grid(row=10, column=0, columnspan=6, sticky="w", padx=6)
        upn_entry = ttk.Entry(form, textvariable=self._vars["upn"])
        upn_entry.grid(row=11, column=0, columnspan=6, sticky="ew", padx=6, pady=(1, 8))
        self._widgets.append(upn_entry)
        self._inputs["upn"] = upn_entry

        buttons = ttk.Frame(form)
        buttons.grid(row=12, column=0, columnspan=6, sticky="ew", padx=6, pady=(0, 8))
        self._add_btn = ttk.Button(buttons, text="Add to List", command=self._add_row)
        self._add_btn.pack(side="left")
        self._clear_inputs_btn = ttk.Button(
            buttons, text="Clear Fields", command=self._clear_inputs
        )
        self._clear_inputs_btn.pack(side="left", padx=(6, 0))
        self._widgets.extend([self._add_btn, self._clear_inputs_btn])

        self._setup_traces()

        list_frame = ttk.LabelFrame(self, text="Pending Accounts")
        list_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 8))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        style = ttk.Style(self)
        style.configure("Pending.Treeview", rowheight=26, font=("Segoe UI", 9))
        style.configure("Pending.Treeview.Heading", font=("Segoe UI", 9, "bold"))

        columns = ("name", "upn", "rto", "title", "department", "manager",
                   "license", "groups", "password")
        self.tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", height=6,
            style="Pending.Treeview",
        )
        headings = {
            "name": "Display Name",
            "upn": "UPN",
            "rto": "RTO",
            "title": "Job Title",
            "department": "Department",
            "manager": "Manager",
            "license": "License",
            "groups": "Groups",
            "password": "Password",
        }
        widths = {
            "name": 190, "upn": 210, "rto": 60, "title": 130, "department": 140,
            "manager": 200, "license": 170, "groups": 190, "password": 120,
        }
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="w", stretch=False)
        self.tree.tag_configure("odd", background=C_STRIPE_ODD)
        self.tree.tag_configure("even", background=C_STRIPE_EVEN)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click)
        self._edit_entry: tk.Entry | None = None

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        self._remove_btn = ttk.Button(actions, text="Remove Selected", command=self._remove_selected)
        self._remove_btn.pack(side="left")
        self._clear_btn = ttk.Button(actions, text="Clear List", command=self._clear_rows)
        self._clear_btn.pack(side="left", padx=(6, 0))
        self._create_btn = ttk.Button(actions, text="Create All Accounts", command=self._submit)
        self._create_btn.pack(side="right")
        ttk.Label(
            actions,
            text="Double-click any cell to edit — licenses and groups open a picker.",
            foreground=C_DIM, font=("Segoe UI", 8),
        ).pack(side="left", padx=(12, 0))
        self._widgets.extend([self.tree, self._remove_btn, self._clear_btn, self._create_btn])

        results_frame = ttk.LabelFrame(self, text="Creation Output")
        results_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 8))
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)

        self.output = tk.Text(
            results_frame,
            height=16,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
            bg=C_FIELD,
            fg=C_TEXT,
            insertbackground=C_TEXT,
            relief="flat",
            highlightthickness=0,
        )
        self.output.grid(row=0, column=0, sticky="nsew")
        out_scroll = ttk.Scrollbar(results_frame, orient="vertical", command=self.output.yview)
        out_scroll.grid(row=0, column=1, sticky="ns")
        self.output.configure(yscrollcommand=out_scroll.set)

        output_actions = ttk.Frame(results_frame)
        output_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(output_actions, text="Copy Output", command=self.copy_output).pack(side="left")
        ttk.Button(output_actions, text="Clear Output", command=self.clear_output).pack(side="left", padx=(6, 0))

        self._status_var = tk.StringVar(value="Connect to an RTO to enable bulk creation.")
        ttk.Label(self, textvariable=self._status_var, foreground=C_DIM).grid(
            row=5, column=0, sticky="ew", padx=10, pady=(0, 10)
        )

    def _field(self, parent, label, key, row, col, colspan=1):
        ttk.Label(parent, text=label).grid(row=row, column=col, columnspan=colspan, sticky="w", padx=6)
        entry = ttk.Entry(parent, textvariable=self._vars[key])
        entry.grid(row=row + 1, column=col, columnspan=colspan, sticky="ew", padx=6, pady=(1, 8))
        self._widgets.append(entry)
        self._inputs[key] = entry

    def _combo(self, parent, label, key, values, row, col):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=6)
        combo = ttk.Combobox(parent, textvariable=self._vars[key], values=values, state="readonly")
        combo.grid(row=row + 1, column=col, sticky="ew", padx=6, pady=(1, 8))
        if values:
            combo.set(values[0])
        self._widgets.append(combo)
        self._inputs[key] = combo

    def set_rto(self, rto, domain, prefix="", default_group_name="", domain_options=None):
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
        combo = self._inputs["create_for"]
        combo.configure(values=labels)
        if labels:
            self._vars["create_for"].set(labels[0])
        self._selected_groups = []
        self._render_group_tags()
        self._status_var.set(f"Ready for {rto}. Add accounts manually, then create all.")

    def set_enabled(self, enabled):
        self._enabled = enabled
        for widget in self._widgets:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="readonly" if enabled else "disabled")
            elif isinstance(widget, ttk.Treeview):
                continue
            else:
                widget.configure(state="normal" if enabled else "disabled")
        self._group_search.set_enabled(enabled)
        self._refresh_create_state()
        if not enabled:
            self._status_var.set("Connect to an RTO to enable bulk creation.")

    def set_submit_state(self, enabled):
        self._submitting = not enabled
        active = enabled and self._enabled
        for widget in self._widgets:
            if isinstance(widget, ttk.Treeview):
                continue
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="readonly" if active else "disabled")
            else:
                widget.configure(state="normal" if active else "disabled")
        self._group_search.set_enabled(active)
        self._refresh_create_state()

    def _selected_domain_option(self):
        selected = self._vars["create_for"].get()
        return next((o for o in self._domain_options if o.get("label") == selected), None)

    def _setup_traces(self):
        self._vars["first_name"].trace_add("write", self._on_name_change)
        self._vars["last_name"].trace_add("write", self._on_name_change)
        self._vars["create_for"].trace_add("write", self._on_create_for_change)
        self._vars["upn"].trace_add("write", self._on_upn_change)

    def _on_create_for_change(self, *_):
        self._refresh_license_values()
        self._refresh_group_options()
        self._on_name_change()

    def set_license_options(self, auth_rto, options):
        """Store the SKU list for one tenant; refresh the dropdown if the
        currently selected 'Create For' belongs to that tenant."""
        self._license_catalog[auth_rto] = list(options or [])
        self._refresh_license_values()

    def _refresh_license_values(self):
        options = self._current_license_options()
        values = [LICENSE_PLACEHOLDER] + [o["label"] for o in options]
        self._inputs["license"].configure(values=values)
        fit_combo_popdown(self._inputs["license"])
        self._vars["license"].set(LICENSE_PLACEHOLDER)
        # Drop picked licenses that don't exist in the newly selected tenant.
        valid = {o.get("skuId") for o in options}
        kept = [o for o in self._selected_licenses if o.get("skuId") in valid]
        if len(kept) != len(self._selected_licenses):
            self._selected_licenses = kept
            self._render_license_tags()

    def _current_license_options(self):
        option = self._selected_domain_option()
        auth_rto = (option or {}).get("auth_rto", "")
        return self._license_catalog.get(auth_rto, [])

    def _on_license_pick(self, _event=None):
        label = self._vars["license"].get()
        self._vars["license"].set(LICENSE_PLACEHOLDER)
        picked = next(
            (o for o in self._current_license_options() if o.get("label") == label),
            None,
        )
        if not picked:
            return
        if any(
            o.get("skuId") == picked.get("skuId")
            for o in self._selected_licenses
        ):
            return
        self._selected_licenses.append(picked)
        self._render_license_tags()

    def _render_license_tags(self):
        render_tags(
            self._license_tags, self._selected_licenses,
            text_of=lambda o: o.get("name", ""),
            on_remove=self._remove_license,
            **LICENSE_TAG_COLORS,
        )

    def _remove_license(self, option):
        self._selected_licenses = [
            o for o in self._selected_licenses
            if o.get("skuId") != option.get("skuId")
        ]
        self._render_license_tags()

    def _render_group_tags(self):
        render_tags(
            self._group_tags, self._selected_groups,
            text_of=lambda g: g.get("displayName", ""),
            on_remove=self._remove_group,
            **GROUP_TAG_COLORS,
        )

    def _remove_group(self, group):
        self._selected_groups = [
            g for g in self._selected_groups
            if g.get("displayName") != group.get("displayName")
        ]
        self._render_group_tags()

    def set_group_options(self, auth_rto, options):
        self._group_catalog[auth_rto] = list(options or [])
        self._refresh_group_options()

    def _refresh_group_options(self):
        option = self._selected_domain_option()
        auth_rto = (option or {}).get("auth_rto", "")
        self._group_search.set_options(self._group_catalog.get(auth_rto, []))

    def _on_group_pick(self, group):
        name = group.get("displayName", "").strip()
        if not name:
            return
        if any(
            g.get("displayName", "").lower() == name.lower()
            for g in self._selected_groups
        ):
            return
        self._selected_groups.append(group)
        self._render_group_tags()

    def _on_name_change(self, *_):
        if not self._upn_auto:
            return
        option = self._selected_domain_option()
        domain = option.get("domain", "") if option else ""
        first = self._vars["first_name"].get()
        last = self._vars["last_name"].get()
        self._vars["upn"].set(generate_upn(first, last, domain))

    def _on_upn_change(self, *_):
        option = self._selected_domain_option()
        domain = option.get("domain", "") if option else ""
        first = self._vars["first_name"].get()
        last = self._vars["last_name"].get()
        self._upn_auto = self._vars["upn"].get() == generate_upn(first, last, domain)

    def _add_row(self):
        first = self._vars["first_name"].get().strip()
        last = self._vars["last_name"].get().strip()
        if not first or not last:
            messagebox.showwarning("Validation", "First Name and Last Name are required.")
            return

        option = self._selected_domain_option()
        if not option:
            messagebox.showwarning("Validation", "Select where this account should be created.")
            return

        password = self._vars["password"].get().strip() or generate_password()

        acc = AccountData()
        acc.first_name = first
        acc.last_name = last
        acc.rto = option.get("rto", "")
        acc.domain = option.get("domain", "")
        acc.display_name = generate_display_name(first, last, option.get("prefix", ""))
        acc.upn = self._vars["upn"].get().strip() or generate_upn(first, last, acc.domain)
        acc.title = self._vars["title"].get().strip()
        acc.department = self._vars["department"].get().strip()
        acc.office_location = self._vars["office_location"].get().strip()
        auth_rto = option.get("auth_rto", "")
        acc.licenses = [
            {"skuId": o.get("skuId", ""), "name": o.get("name", "")}
            for o in self._selected_licenses
        ]
        manager = self._vars["manager"].get().strip()
        acc.manager_display = manager
        acc.manager_upn = manager if "@" in manager else (f"{manager}@{acc.domain}" if manager else "")
        # Prefer the fetched group record (real mail + recipientType, so M365
        # groups get Add-UnifiedGroupLinks); fall back to the picked/typed one.
        catalog = {
            o.get("displayName", "").lower(): o
            for o in self._group_catalog.get(auth_rto, [])
        }
        acc.distribution_groups = [
            dict(catalog.get(g.get("displayName", "").lower(), g))
            for g in self._selected_groups
        ]

        if not acc.upn:
            messagebox.showwarning(
                "Validation",
                "UPN could not be generated from the name. Type one manually in the UPN field.",
            )
            return

        if any(existing.upn.lower() == acc.upn.lower() for existing, _ in self._rows):
            messagebox.showwarning("Duplicate", f"{acc.upn} is already in the pending list.")
            return

        self._rows.append((acc, password))
        self.tree.insert(
            "",
            "end",
            values=(
                acc.display_name,
                acc.upn,
                acc.rto,
                acc.title,
                acc.department,
                acc.manager_upn or acc.manager_display,
                "; ".join(l["name"] for l in acc.licenses) or "—",
                "; ".join(g["displayName"] for g in acc.distribution_groups),
                password,
            ),
        )
        self._restripe()
        self._clear_inputs(keep_context=True)
        self._refresh_create_state()
        self._status_var.set(f"{len(self._rows)} account(s) ready to create.")

    def add_prefilled_rows(self, entries: list[dict]) -> tuple[int, int]:
        """Queue ready-made rows from the HR Tasks section — one per tenant
        the new staff member needs an account in. Each entry carries its own
        rto/domain, so rows can span tenants that are not even connected
        yet; Create All resolves sessions per row and leaves rows for
        unconnected tenants pending. Returns (added, skipped_duplicates).
        """
        added = skipped = 0
        for entry in entries:
            first = entry.get("first_name", "").strip()
            last = entry.get("last_name", "").strip()
            domain = entry.get("domain", "").strip()
            if not first or not last or not domain:
                continue
            acc = AccountData()
            acc.first_name = first
            acc.last_name = last
            acc.rto = entry.get("rto", "")
            acc.domain = domain
            acc.display_name = generate_display_name(
                first, last, entry.get("prefix", "")
            )
            acc.upn = generate_upn(first, last, domain)
            acc.title = entry.get("title", "")
            acc.department = entry.get("department", "")
            acc.office_location = entry.get("office_location", "")
            acc.manager_display = entry.get("manager_display", "")
            acc.manager_upn = entry.get("manager_upn", "")
            acc.start_date = entry.get("start_date", "")
            if not acc.upn or any(
                existing.upn.lower() == acc.upn.lower() for existing, _ in self._rows
            ):
                skipped += 1
                continue
            password = generate_password()
            self._rows.append((acc, password))
            self.tree.insert(
                "", "end",
                values=(
                    acc.display_name, acc.upn, acc.rto, acc.title,
                    acc.department,
                    acc.manager_upn or acc.manager_display,
                    "—", "", password,
                ),
            )
            added += 1
        if added:
            self._restripe()
            self._refresh_create_state()
            self._status_var.set(
                f"{len(self._rows)} account(s) ready — connect each row's "
                "tenant, edit anything by double-click, then Create All."
            )
        return added, skipped

    # Column id -> (tree column name, AccountData attribute or None for password)
    _EDITABLE_TEXT_COLUMNS = {
        "#1": ("name", "display_name"),
        "#2": ("upn", "upn"),
        "#4": ("title", "title"),
        "#5": ("department", "department"),
        "#6": ("manager", "manager_upn"),
        "#9": ("password", None),
    }

    def _on_tree_double_click(self, event):
        if self._submitting or not self._enabled:
            return
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        items = self.tree.get_children()
        try:
            index = items.index(item_id)
        except ValueError:
            return

        if column in self._EDITABLE_TEXT_COLUMNS:
            col_name, attr = self._EDITABLE_TEXT_COLUMNS[column]
            self._edit_text_cell(index, item_id, column, col_name, attr)
        elif column == "#7":
            self._edit_row_licenses(index, item_id)
        elif column == "#8":
            self._edit_row_groups(index, item_id)
        elif column == "#3":
            self._status_var.set(
                "The tenant of a pending row can't be changed — remove the row "
                "and add it again under the right 'Create For'."
            )

    def _edit_text_cell(self, index, item_id, column, col_name, attr):
        bbox = self.tree.bbox(item_id, column)
        if not bbox:
            return
        x, y, w, h = bbox
        current = self.tree.set(item_id, col_name)

        if self._edit_entry is not None:
            self._edit_entry.destroy()
        entry = tk.Entry(
            self.tree, borderwidth=1, relief="solid",
            bg=C_FIELD, fg=C_TEXT, insertbackground=C_TEXT,
        )
        entry.insert(0, current)
        entry.select_range(0, tk.END)
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus_set()
        self._edit_entry = entry

        def commit(_=None):
            new_value = entry.get().strip()
            entry.destroy()
            self._edit_entry = None
            if new_value == current:
                return
            # Job Title, Department and Manager may be cleared; the others
            # must not be empty.
            if not new_value and col_name not in ("title", "department", "manager"):
                return
            acc, password = self._rows[index]
            if col_name == "manager":
                # Same convention as the add-form: bare alias gets the row's
                # domain appended; empty clears the manager.
                resolved = (
                    new_value if ("@" in new_value or not new_value)
                    else f"{new_value}@{acc.domain}"
                )
                acc.manager_upn = resolved
                acc.manager_display = resolved
                self.tree.set(item_id, col_name, resolved)
                return
            if col_name == "upn":
                if "@" not in new_value:
                    self._status_var.set("UPN must be a full address (user@domain).")
                    return
                if any(
                    i != index and existing.upn.lower() == new_value.lower()
                    for i, (existing, _) in enumerate(self._rows)
                ):
                    self._status_var.set(f"{new_value} is already in the pending list.")
                    return
            if attr is None:  # password lives in the row tuple, not AccountData
                self._rows[index] = (acc, new_value)
            else:
                setattr(acc, attr, new_value)
            self.tree.set(item_id, col_name, new_value)

        def cancel(_=None):
            entry.destroy()
            self._edit_entry = None

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", cancel)

    def _auth_rto_for_row(self, acc) -> str:
        for option in self._domain_options:
            if option.get("rto") == acc.rto:
                return option.get("auth_rto", acc.rto)
        return acc.rto

    def _edit_dialog(self, title, height=380):
        """Small dark modal shell for the license/group pickers."""
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.geometry(f"420x{height}")
        dlg.configure(bg=C_BG)
        dlg.transient(self.winfo_toplevel())
        dlg.grab_set()
        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)
        return dlg, body

    def _edit_row_licenses(self, index, item_id):
        acc, _ = self._rows[index]
        options = list(self._license_catalog.get(self._auth_rto_for_row(acc), []))
        # Keep any licenses already on the row that are missing from the
        # catalog (e.g. catalog not loaded yet) so they aren't silently lost.
        known = {o.get("skuId") for o in options}
        options += [l for l in acc.licenses if l.get("skuId") not in known]
        if not options:
            self._status_var.set(
                "No licenses loaded for this tenant yet — connect first."
            )
            return

        dlg, body = self._edit_dialog(f"Licenses — {acc.display_name}")
        ttk.Label(body, text="Tick the licenses for this account:").pack(anchor="w")

        selected_ids = {l.get("skuId") for l in acc.licenses}
        rows = []
        for option in options:
            var = tk.BooleanVar(value=option.get("skuId") in selected_ids)
            ttk.Checkbutton(
                body, text=option.get("name", ""), variable=var,
            ).pack(anchor="w", pady=2)
            rows.append((var, option))

        def save():
            acc.licenses = [
                {"skuId": o.get("skuId", ""), "name": o.get("name", "")}
                for var, o in rows if var.get()
            ]
            self.tree.set(item_id, "license",
                          "; ".join(l["name"] for l in acc.licenses) or "—")
            dlg.destroy()

        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=save).pack(side="right")

    def _edit_row_groups(self, index, item_id):
        acc, _ = self._rows[index]
        current = [dict(g) for g in acc.distribution_groups]

        dlg, body = self._edit_dialog(f"Groups — {acc.display_name}", height=440)
        ttk.Label(body, text="Type to add a group; remove with ×:").pack(anchor="w")

        picker_holder = ttk.Frame(body)
        picker_holder.pack(fill="x", pady=(4, 6))
        picker_holder.columnconfigure(0, weight=1)

        listing = ttk.Frame(body)
        listing.pack(fill="both", expand=True)

        def refresh():
            for child in listing.winfo_children():
                child.destroy()
            for group in current:
                row = ttk.Frame(listing)
                row.pack(fill="x", pady=1)
                ttk.Label(row, text=group.get("displayName", "")).pack(side="left")
                ttk.Button(
                    row, text="×", width=3,
                    command=lambda g=group: (current.remove(g), refresh()),
                ).pack(side="right")

        def add(group):
            name = group.get("displayName", "").strip()
            if not name:
                return
            if any(g.get("displayName", "").lower() == name.lower() for g in current):
                return
            current.append(group)
            refresh()

        picker = GroupSearch(picker_holder, on_pick=add)
        picker.grid(row=0, column=0, sticky="ew")
        picker.set_options(self._group_catalog.get(self._auth_rto_for_row(acc), []))
        refresh()

        def save():
            acc.distribution_groups = list(current)
            self.tree.set(item_id, "groups",
                          "; ".join(g["displayName"] for g in acc.distribution_groups))
            dlg.destroy()

        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=save).pack(side="right")

    def _remove_selected(self):
        selected = list(self.tree.selection())
        if not selected:
            return
        all_items = list(self.tree.get_children())
        indexes = sorted((all_items.index(item) for item in selected), reverse=True)
        for index in indexes:
            self._rows.pop(index)
            self.tree.delete(all_items[index])
        self._restripe()
        self._refresh_create_state()
        self._status_var.set(f"{len(self._rows)} account(s) ready to create.")

    def _clear_rows(self):
        self._rows.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._refresh_create_state()
        self._status_var.set("Pending list cleared.")

    def _restripe(self):
        for index, item in enumerate(self.tree.get_children()):
            self.tree.item(item, tags=("odd" if index % 2 else "even",))

    def _clear_inputs(self, keep_context=False):
        # Reset auto-mode before clearing fields so the cascade of trace
        # callbacks (first_name -> last_name -> upn) doesn't re-lock the UPN
        # field into "manual" mode based on intermediate empty values.
        self._upn_auto = True
        for key in [
            "first_name", "last_name", "title", "department",
            "office_location", "manager", "password", "upn",
        ]:
            self._vars[key].set("")
        if not keep_context:
            self._selected_groups = []
            self._render_group_tags()
            self._selected_licenses = []
            self._render_license_tags()
            self._vars["license"].set(LICENSE_PLACEHOLDER)

    def _submit(self):
        if not self._rows:
            messagebox.showinfo("Nothing to create", "Add accounts to the pending list first.")
            return
        self.on_submit(list(self._rows))

    def mark_created(self, account: AccountData):
        for index, (queued, _password) in enumerate(list(self._rows)):
            if queued.upn.lower() == account.upn.lower():
                self._rows.pop(index)
                item = self.tree.get_children()[index]
                self.tree.delete(item)
                break
        self._restripe()
        self._refresh_create_state()

    def set_status(self, text):
        self._status_var.set(text)

    def append_output(self, text: str):
        self.output.configure(state="normal")
        current = self.output.get("1.0", tk.END).strip()
        if current:
            self.output.insert(tk.END, "\n\n")
        self.output.insert(tk.END, text)
        self.output.see(tk.END)
        self.output.configure(state="disabled")

    def copy_output(self):
        text = self.output.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("No output", "There is no bulk creation output to copy.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)

    def clear_output(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", tk.END)
        self.output.configure(state="disabled")

    def _refresh_create_state(self):
        state = "normal" if self._enabled and self._rows and not self._submitting else "disabled"
        self._create_btn.configure(state=state)
