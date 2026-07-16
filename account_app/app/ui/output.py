import tkinter as tk
from tkinter import messagebox, ttk


class OutputPanel(ttk.LabelFrame):
    """Read-only creation output panel — same look as the Bulk tab output.

    Results are appended as copy-friendly text blocks separated by blank
    lines, with Copy / Clear actions underneath.
    """

    def __init__(self, parent, title="Creation Output"):
        super().__init__(parent, text=title)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.text = tk.Text(
            self,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
            padx=8,
            pady=8,
        )
        self.text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=(4, 0))
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        scroll.grid(row=0, column=1, sticky="ns", pady=(4, 0))
        self.text.configure(yscrollcommand=scroll.set)

        actions = ttk.Frame(self)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        ttk.Button(actions, text="Copy Output", command=self.copy).pack(side="left")
        ttk.Button(actions, text="Clear Output", command=self.clear).pack(
            side="left", padx=(6, 0)
        )

    def append(self, block: str):
        self.text.configure(state="normal")
        current = self.text.get("1.0", tk.END).strip()
        if current:
            self.text.insert(tk.END, "\n\n")
        self.text.insert(tk.END, block)
        self.text.see(tk.END)
        self.text.configure(state="disabled")

    def copy(self):
        content = self.text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showinfo("No output", "There is no creation output to copy.")
            return
        self.clipboard_clear()
        self.clipboard_append(content)

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.configure(state="disabled")
