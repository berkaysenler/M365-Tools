import tkinter as tk
from tkinter import ttk


class SidebarFrame(tk.Frame):
    def __init__(self, parent, rto_list, on_select, on_connect):
        super().__init__(parent, width=190, bg="#2b2b2b")
        self.on_select = on_select
        self.on_connect = on_connect
        self._selected = None
        self._buttons = {}
        self._build(rto_list)

    def _build(self, rto_list):
        self.grid_propagate(False)
        self.columnconfigure(0, weight=1)

        tk.Label(
            self, text="M365 Tools", bg="#2b2b2b", fg="#ffffff",
            font=("Segoe UI", 11, "bold"), anchor="w", padx=10, pady=10,
        ).grid(row=0, column=0, sticky="ew")

        tk.Frame(self, bg="#444", height=1).grid(row=1, column=0, sticky="ew")

        tk.Label(
            self, text="RTO TENANTS", bg="#2b2b2b", fg="#888",
            font=("Segoe UI", 7, "bold"), anchor="w", padx=10,
        ).grid(row=2, column=0, sticky="ew", pady=(8, 4))

        for i, rto in enumerate(rto_list):
            btn = tk.Button(
                self, text=rto, anchor="w",
                bg="#2b2b2b", fg="#cccccc", activebackground="#3c3c3c",
                activeforeground="#ffffff", relief="flat",
                padx=14, pady=6, font=("Segoe UI", 9),
                bd=0, highlightthickness=0,
                command=lambda r=rto: self._select(r),
                cursor="hand2",
            )
            btn.grid(row=3 + i, column=0, sticky="ew")
            self._buttons[rto] = btn

        sep_row = 3 + len(rto_list)
        tk.Frame(self, bg="#444", height=1).grid(row=sep_row, column=0, sticky="ew", pady=(8, 0))

        self._connect_btn = tk.Button(
            self, text="Connect", bg="#0078d4", fg="#ffffff",
            activebackground="#005a9e", activeforeground="#ffffff",
            relief="flat", padx=10, pady=6, font=("Segoe UI", 9, "bold"),
            bd=0, highlightthickness=0, state="disabled",
            command=self.on_connect, cursor="hand2",
        )
        self._connect_btn.grid(row=sep_row + 1, column=0, sticky="ew", padx=10, pady=(8, 4))

        self._status_var = tk.StringVar(value="Select an RTO to begin")
        tk.Label(
            self, textvariable=self._status_var,
            bg="#2b2b2b", fg="#888", font=("Segoe UI", 8),
            wraplength=165, justify="left", anchor="w", padx=10,
        ).grid(row=sep_row + 2, column=0, sticky="ew")

    def _select(self, rto):
        if self._selected and self._selected in self._buttons:
            self._buttons[self._selected].config(bg="#2b2b2b", fg="#cccccc")
        self._selected = rto
        self._buttons[rto].config(bg="#3c3c3c", fg="#ffffff")
        self._connect_btn.config(state="normal")
        self.on_select(rto)

    def set_connect_state(self, connected):
        if connected:
            self._connect_btn.config(text="Disconnect", bg="#c0392b", activebackground="#96281b")
            if self._selected:
                self._buttons[self._selected].config(bg="#1e4d2b", fg="#4caf50")
        else:
            self._connect_btn.config(text="Connect", bg="#0078d4", activebackground="#005a9e")
            if self._selected:
                self._buttons[self._selected].config(bg="#3c3c3c", fg="#ffffff")

    def set_status(self, text):
        self._status_var.set(text)
