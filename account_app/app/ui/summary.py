import tkinter as tk
from tkinter import ttk


class SummaryFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, width=280)
        self.grid_propagate(False)
        self._current_text = ""
        self._build()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, text="Account Summary", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 4)
        )

        self._text = tk.Text(
            self, state="disabled", wrap="word",
            font=("Consolas", 9), bg="#f8f8f8", relief="flat",
            padx=6, pady=6,
        )
        self._text.grid(row=1, column=0, sticky="nsew", padx=(10, 0))

        scroll = ttk.Scrollbar(self, orient="vertical", command=self._text.yview)
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 10))
        self._text.configure(yscrollcommand=scroll.set)

        self._copy_btn = ttk.Button(self, text="Copy to Clipboard", command=self._copy)
        self._copy_btn.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 2))

        self._status_var = tk.StringVar()
        ttk.Label(
            self, textvariable=self._status_var,
            foreground="#0078d4", font=("Segoe UI", 8),
        ).grid(row=3, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="w")

    def update(self, account):
        groups = "\n".join(f"  - {g.get('displayName', '')}" for g in account.distribution_groups)
        lines = [
            "=== Account Summary ===",
            f"",
            f"RTO:           {account.rto}",
            f"Domain:        {account.domain}",
            f"",
            f"--- Identity ---",
            f"Display Name:  {account.display_name}",
            f"UPN:           {account.upn}",
            f"",
            f"--- Role ---",
            f"Job Title:     {account.title}",
            f"Department:    {account.department}",
            f"Location:      {account.office_location}",
            f"Manager:       {account.manager_display}",
            f"Mobile:        {account.mobile}",
            f"",
            f"--- Access ---",
            f"Usage Loc:     {account.usage_location}",
            f"Start Date:    {account.start_date}",
            f"",
            f"--- Groups ---",
            groups if groups else "  (none selected)",
        ]
        text = "\n".join(lines)
        self._current_text = text
        self._text.config(state="normal")
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", text)
        self._text.config(state="disabled")

    def mark_created(self):
        self._text.config(state="normal")
        self._text.insert("1.0", ">> ACCOUNT CREATED SUCCESSFULLY <<\n\n")
        self._text.config(state="disabled")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._current_text)
        self._status_var.set("Copied!")
        self.after(2000, lambda: self._status_var.set(""))
