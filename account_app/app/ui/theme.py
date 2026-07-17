"""Central dark palette and ttk style for the whole app.

customtkinter widgets take colors directly from these constants; classic
ttk/tk screens are converted in one place by apply_dark_theme(), which
switches ttk to the fully-stylable "clam" theme and recolors every widget
class. Call it once on the root window before building any ttk UI.
"""

from tkinter import ttk

# Base palette (canonical — keep main.py and sections in sync via imports)
C_BG          = "#171a24"   # window background
C_PANEL       = "#202433"   # cards / sidebars
C_PANEL_HOVER = "#2a3042"
C_SELECTED    = "#2563eb"   # accent / primary action
C_ACCENT_HOV  = "#1d4ed8"
C_TEXT        = "#eef2ff"
C_DIM         = "#9aa3b8"   # captions, hints
C_FIELD       = "#161927"   # entry / text field background
C_BORDER      = "#343b52"
C_OK          = "#4CAF50"
C_WARN        = "#FFC107"
C_ERR         = "#EF5350"
C_STRIPE_ODD  = "#242940"   # treeview zebra rows
C_STRIPE_EVEN = "#202433"

# Removable tag chips (see tkutil.render_tags)
GROUP_TAG_COLORS = {"bg": "#1e3a5f", "fg": "#93c5fd"}
LICENSE_TAG_COLORS = {"bg": "#14532d", "fg": "#86efac"}

FONT_BASE = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")

_applied = False


def apply_dark_theme(root) -> None:
    """Apply the dark palette to every ttk widget class. Idempotent."""
    global _applied
    if _applied:
        return
    _applied = True

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(
        ".",
        background=C_BG,
        foreground=C_TEXT,
        fieldbackground=C_FIELD,
        bordercolor=C_BORDER,
        lightcolor=C_PANEL,
        darkcolor=C_BG,
        troughcolor=C_PANEL,
        insertcolor=C_TEXT,
        selectbackground=C_SELECTED,
        selectforeground=C_TEXT,
        font=FONT_BASE,
    )

    style.configure("TFrame", background=C_BG)
    style.configure("TLabel", background=C_BG, foreground=C_TEXT)
    style.configure("TSeparator", background=C_BORDER)

    style.configure(
        "TLabelframe", background=C_BG, bordercolor=C_BORDER,
        lightcolor=C_BG, darkcolor=C_BG,
    )
    style.configure("TLabelframe.Label", background=C_BG, foreground=C_DIM,
                    font=FONT_BOLD)

    style.configure(
        "TButton", background=C_PANEL, foreground=C_TEXT,
        bordercolor=C_BORDER, focuscolor=C_SELECTED, padding=(10, 5),
    )
    style.map(
        "TButton",
        background=[("disabled", C_BG), ("pressed", C_SELECTED), ("active", C_PANEL_HOVER)],
        foreground=[("disabled", C_DIM)],
    )

    # Primary action buttons (opt-in via style="Accent.TButton")
    style.configure(
        "Accent.TButton", background=C_SELECTED, foreground=C_TEXT,
        bordercolor=C_SELECTED, padding=(12, 5),
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", C_PANEL), ("pressed", C_ACCENT_HOV), ("active", C_ACCENT_HOV)],
        foreground=[("disabled", C_DIM)],
    )

    style.configure(
        "TEntry", fieldbackground=C_FIELD, foreground=C_TEXT,
        bordercolor=C_BORDER, insertcolor=C_TEXT, padding=4,
    )
    style.map(
        "TEntry",
        fieldbackground=[("readonly", C_PANEL), ("disabled", C_BG)],
        foreground=[("disabled", C_DIM)],
    )

    style.configure(
        "TCombobox", fieldbackground=C_FIELD, background=C_PANEL,
        foreground=C_TEXT, bordercolor=C_BORDER, arrowcolor=C_TEXT,
        insertcolor=C_TEXT, padding=4,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", C_FIELD), ("disabled", C_BG)],
        foreground=[("disabled", C_DIM)],
        background=[("active", C_PANEL_HOVER)],
    )

    style.configure("TCheckbutton", background=C_BG, foreground=C_TEXT,
                    indicatorcolor=C_FIELD, focuscolor=C_BG)
    style.map(
        "TCheckbutton",
        background=[("active", C_BG)],
        indicatorcolor=[("selected", C_SELECTED)],
    )
    style.configure("TRadiobutton", background=C_BG, foreground=C_TEXT,
                    indicatorcolor=C_FIELD, focuscolor=C_BG)
    style.map(
        "TRadiobutton",
        background=[("active", C_BG)],
        indicatorcolor=[("selected", C_SELECTED)],
    )

    style.configure("TNotebook", background=C_BG, bordercolor=C_BORDER,
                    tabmargins=(4, 4, 4, 0))
    style.configure(
        "TNotebook.Tab", background=C_PANEL, foreground=C_DIM,
        bordercolor=C_BORDER, padding=(14, 6), font=FONT_BASE,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", C_BG)],
        foreground=[("selected", C_TEXT)],
    )

    style.configure(
        "Treeview", background=C_PANEL, fieldbackground=C_PANEL,
        foreground=C_TEXT, bordercolor=C_BORDER, rowheight=26,
    )
    style.map(
        "Treeview",
        background=[("selected", C_SELECTED)],
        foreground=[("selected", C_TEXT)],
    )
    style.configure(
        "Treeview.Heading", background=C_PANEL_HOVER, foreground=C_TEXT,
        bordercolor=C_BORDER, font=FONT_BOLD,
    )
    style.map("Treeview.Heading", background=[("active", C_PANEL_HOVER)])

    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar", background=C_PANEL,
            troughcolor=C_BG, bordercolor=C_BG, arrowcolor=C_DIM,
        )
        style.map(f"{orient}.TScrollbar", background=[("active", C_PANEL_HOVER)])

    style.configure("TProgressbar", background=C_SELECTED, troughcolor=C_PANEL,
                    bordercolor=C_BORDER)

    # Combobox popdown list is a raw tk Listbox inside an internal toplevel —
    # only reachable through the option database.
    root.option_add("*TCombobox*Listbox.background", C_FIELD)
    root.option_add("*TCombobox*Listbox.foreground", C_TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", C_SELECTED)
    root.option_add("*TCombobox*Listbox.selectForeground", C_TEXT)
