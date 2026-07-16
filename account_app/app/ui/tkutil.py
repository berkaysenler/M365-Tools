import tkinter as tk


def fit_combo_popdown(combo):
    """Widen a ttk.Combobox dropdown list to fit its longest value.

    By default the popdown list is only as wide as the entry widget, which
    truncates long labels (license names). Tk has no public API for this, so
    we reach into the internal popdown listbox.
    """
    try:
        values = combo.cget("values") or ()
        longest = max((len(str(v)) for v in values), default=0)
        if not longest:
            return
        popdown = combo.tk.call("ttk::combobox::PopdownWindow", combo)
        combo.tk.call(f"{popdown}.f.l", "configure", "-width", longest + 2)
    except tk.TclError:
        pass  # cosmetic only — never let it break the form


def render_tags(container, items, text_of, on_remove, bg, fg, vertical=False):
    """Redraw removable tag chips inside ``container``.

    ``text_of`` extracts the label from an item; ``on_remove`` is called with
    the item when its × button is clicked. ``vertical`` stacks one tag per
    line for long labels (license names).
    """
    for w in container.winfo_children():
        w.destroy()
    for item in items:
        wrap = tk.Frame(container, bg=bg, padx=6, pady=3)
        if vertical:
            wrap.pack(side="top", anchor="w", pady=(0, 4))
        else:
            wrap.pack(side="left", padx=(0, 4), pady=4)
        tk.Label(
            wrap, text=text_of(item), bg=bg, fg=fg, font=("Segoe UI", 8),
        ).pack(side="left")
        tk.Button(
            wrap, text="×", bg=bg, fg=fg,
            relief="flat", bd=0, padx=2, font=("Segoe UI", 8),
            cursor="hand2",
            command=lambda i=item: on_remove(i),
        ).pack(side="left")


GROUP_TAG_COLORS = {"bg": "#dbeafe", "fg": "#1e40af"}
LICENSE_TAG_COLORS = {"bg": "#dcfce7", "fg": "#166534"}
