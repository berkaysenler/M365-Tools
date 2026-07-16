import csv
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from app.account import AccountData


class SyncroTestFrame(ttk.Frame):
    """Standalone tab for testing the Syncro API without creating an M365 account."""

    def __init__(self, parent, syncro, config):
        super().__init__(parent)
        self.syncro = syncro
        self.config = config

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._build()

    def _build(self):
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        ttk.Label(
            header,
            text="Syncro Contact Test",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text=(
                "Sends a contact directly to Syncro under the selected RTO's organization. "
                "Does not create an M365 account."
            ),
            foreground="#555",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        form = ttk.LabelFrame(self, text="Contact details")
        form.grid(row=1, column=0, sticky="ew", padx=10, pady=(6, 8))
        form.columnconfigure(1, weight=1)

        self._vars = {
            "rto": tk.StringVar(),
            "name": tk.StringVar(),
            "email": tk.StringVar(),
            "mobile": tk.StringVar(),
            "location": tk.StringVar(),
            "title": tk.StringVar(),
            "city": tk.StringVar(),
        }

        rto_options = [
            rto for rto, cfg in self.config.items()
            if cfg.get("syncro_org_id")
        ]
        if not rto_options:
            rto_options = list(self.config.keys())

        fields = [
            ("RTO", "rto", rto_options),
            ("Name", "name", None),
            ("Email", "email", None),
            ("Mobile", "mobile", None),
            ("Location", "location", None),
            ("Job Title", "title", None),
            ("City", "city", None),
        ]
        for row, (label, key, choices) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(8, 12), pady=4)
            if choices is not None:
                cb = ttk.Combobox(
                    form,
                    textvariable=self._vars[key],
                    values=choices,
                    state="readonly",
                )
                cb.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=4)
                if choices:
                    self._vars[key].set(choices[0])
            else:
                ttk.Entry(form, textvariable=self._vars[key]).grid(
                    row=row, column=1, sticky="ew", padx=(0, 8), pady=4
                )

        actions = ttk.Frame(self)
        actions.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.send_btn = ttk.Button(actions, text="Send to Syncro", command=self._send)
        self.send_btn.pack(side="left")
        self.csv_btn = ttk.Button(actions, text="Import CSV...", command=self._import_csv)
        self.csv_btn.pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(actions, textvariable=self.status_var, foreground="#555").pack(
            side="left", padx=(12, 0)
        )
        ttk.Label(
            actions,
            text="CSV columns: name, email, mobile, location, title, city",
            foreground="#888",
            font=("Segoe UI", 8),
        ).pack(side="right")

        output_frame = ttk.LabelFrame(self, text="Output")
        output_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        self.output = tk.Text(output_frame, height=10, wrap="word", font=("Consolas", 9))
        self.output.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

    def _append(self, text: str):
        self.output.insert("end", text + "\n")
        self.output.see("end")

    def _send(self):
        if not self.syncro:
            self._append("ERROR: Syncro is not configured. Set SYNCRO_SUBDOMAIN and "
                         "SYNCRO_API_KEY in .env and restart the app.")
            return

        rto = self._vars["rto"].get().strip()
        if not rto:
            self._append("ERROR: Pick an RTO.")
            return

        org_id = self.config.get(rto, {}).get("syncro_org_id")
        if not org_id:
            self._append(f"ERROR: {rto} has no syncro_org_id in config.json.")
            return

        name = self._vars["name"].get().strip()
        email = self._vars["email"].get().strip()
        if not name or not email:
            self._append("ERROR: Name and Email are required.")
            return

        account = AccountData(
            display_name=name,
            upn=email,
            mobile=self._vars["mobile"].get().strip(),
            office_location=self._vars["location"].get().strip(),
            title=self._vars["title"].get().strip(),
            city=self._vars["city"].get().strip(),
            rto=rto,
        )

        self.send_btn.configure(state="disabled")
        self.status_var.set("Sending...")
        self._append(f"--- Sending contact to Syncro org {org_id} ({rto}) ---")
        self._append(f"  Name: {name}")
        self._append(f"  Email: {email}")
        if account.mobile:
            self._append(f"  Mobile: {account.mobile}")
        if account.office_location:
            self._append(f"  Location: {account.office_location}")
        if account.title:
            self._append(f"  Job Title: {account.title}")
        if account.city:
            self._append(f" City: {account.city}")

        def _run():
            try:
                err = self.syncro.create_contact(org_id, account)
            except Exception as exc:
                err = f"Unexpected error: {exc}"
            self.after(0, lambda: self._done(err))

        threading.Thread(target=_run, daemon=True).start()

    def _done(self, err):
        self.send_btn.configure(state="normal")
        if err:
            self.status_var.set("Failed.")
            self._append(f"FAILED: {err}")
        else:
            self.status_var.set("Success.")
            self._append("SUCCESS: contact created.")
        self._append("")

    def _import_csv(self):
        if not self.syncro:
            self._append("ERROR: Syncro is not configured. Set SYNCRO_SUBDOMAIN and "
                         "SYNCRO_API_KEY in .env and restart the app.")
            return

        rto = self._vars["rto"].get().strip()
        if not rto:
            self._append("ERROR: Pick an RTO first.")
            return

        org_id = self.config.get(rto, {}).get("syncro_org_id")
        if not org_id:
            self._append(f"ERROR: {rto} has no syncro_org_id in config.json.")
            return

        path = filedialog.askopenfilename(
            title="Select CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return

        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except Exception as exc:
            self._append(f"ERROR: Could not read CSV: {exc}")
            return

        if not rows:
            self._append("ERROR: CSV has no data rows.")
            return

        # Build accounts up front so we can report bad rows before any API calls.
        accounts = []
        skipped = 0
        for idx, row in enumerate(rows, start=2):  # header is row 1
            name = (row.get("name") or "").strip()
            email = (row.get("email") or "").strip()
            if not name or not email:
                self._append(f"  Row {idx}: SKIP (missing name or email)")
                skipped += 1
                continue
            accounts.append((idx, AccountData(
                display_name=name,
                upn=email,
                mobile=(row.get("mobile") or "").strip(),
                office_location=(row.get("location") or "").strip(),
                title=(row.get("title") or "").strip(),
                city=(row.get("city") or "").strip(),
                rto=rto,
            )))

        if not accounts:
            self._append("ERROR: No valid rows to import.")
            return

        self.send_btn.configure(state="disabled")
        self.csv_btn.configure(state="disabled")
        self.status_var.set(f"Importing {len(accounts)} rows...")
        self._append(f"--- CSV import: {len(accounts)} rows to org {org_id} ({rto}) ---")

        def _run():
            ok = 0
            failed = 0
            for line_num, account in accounts:
                try:
                    err = self.syncro.create_contact(org_id, account)
                except Exception as exc:
                    err = f"Unexpected error: {exc}"
                if err:
                    failed += 1
                    self.after(0, lambda ln=line_num, e=err, em=account.upn:
                               self._append(f"  Row {ln} ({em}): FAILED — {e}"))
                else:
                    ok += 1
                    self.after(0, lambda ln=line_num, em=account.upn:
                               self._append(f"  Row {ln} ({em}): OK"))
            self.after(0, lambda: self._csv_done(ok, failed, skipped))

        threading.Thread(target=_run, daemon=True).start()

    def _csv_done(self, ok, failed, skipped):
        self.send_btn.configure(state="normal")
        self.csv_btn.configure(state="normal")
        self.status_var.set(f"Done. {ok} ok, {failed} failed, {skipped} skipped.")
        self._append(f"--- Done. {ok} created, {failed} failed, {skipped} skipped ---")
        self._append("")
