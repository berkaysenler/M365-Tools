import tkinter as tk
from tkinter import ttk


class GroupSearch(ttk.Frame):
    """Type-to-search picker for fetched tenant groups.

    Entry + Add button; a list of matches appears under the entry while
    typing (name and email both searched). Clicking a match or pressing
    Enter picks it — the full group record (mail + recipientType) is passed
    to on_pick. Free text with no matches falls back to a name-only record,
    so unknown/unloaded groups can still be added.
    """

    def __init__(self, parent, on_pick, hint=None, max_results=8):
        super().__init__(parent)
        self._on_pick = on_pick
        self._max_results = max_results
        self._choices: list[dict] = []  # NOT _options — tkinter uses that name
        self._matches: list[dict] = []

        self.columnconfigure(0, weight=1)

        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Return>", lambda e: self.add_current())
        self._btn = ttk.Button(self, text="Add", command=self.add_current)
        self._btn.grid(row=0, column=1, padx=(6, 0))

        if hint:
            ttk.Label(
                self, text=hint, foreground="#666", font=("Segoe UI", 8),
                wraplength=380, justify="left",
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        self._list = tk.Listbox(
            self, height=5, exportselection=False,
            font=("Segoe UI", 9), activestyle="none",
        )
        self._list.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._list.grid_remove()
        self._list.bind("<<ListboxSelect>>", self._on_list_pick)

        self.var.trace_add("write", lambda *a: self._refresh())

    def set_options(self, options):
        self._choices = list(options or [])
        self._refresh()

    def set_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.entry.configure(state=state)
        self._btn.configure(state=state)

    def _refresh(self):
        query = self.var.get().strip().lower()
        if not query or not self._choices:
            self._matches = []
            self._list.grid_remove()
            return
        self._matches = [
            o for o in self._choices
            if query in o.get("displayName", "").lower()
            or query in (o.get("mail") or "").lower()
        ][: self._max_results]
        if not self._matches:
            self._list.grid_remove()
            return
        self._list.delete(0, tk.END)
        for option in self._matches:
            mail = option.get("mail") or ""
            name = option.get("displayName", "")
            self._list.insert(tk.END, f"{name}  <{mail}>" if mail else name)
        self._list.configure(height=min(len(self._matches), self._max_results))
        self._list.grid()

    def _on_list_pick(self, _event):
        selection = self._list.curselection()
        if not selection or selection[0] >= len(self._matches):
            return
        self._choose(dict(self._matches[selection[0]]))

    def add_current(self):
        name = self.var.get().strip()
        if not name:
            return
        if self._matches:
            self._choose(dict(self._matches[0]))
            return
        self._choose({
            "id": name, "displayName": name, "mail": "", "recipientType": "",
        })

    def _choose(self, group):
        self.var.set("")  # trace also hides the match list
        self._on_pick(group)
