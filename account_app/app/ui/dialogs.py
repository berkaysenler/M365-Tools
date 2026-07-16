import tkinter as tk
from tkinter import ttk
import webbrowser


class DeviceCodeDialog(tk.Toplevel):
    """Device-code sign-in dialog. Shows the URL + code; closes when MSAL completes."""

    def __init__(self, parent, user_code, verification_uri):
        super().__init__(parent)
        self.title("Microsoft Sign In")
        self.geometry("440x230")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        self.columnconfigure(0, weight=1)

        ttk.Label(self, text="Sign in to Microsoft 365", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, pady=(18, 4), padx=20
        )
        ttk.Label(
            self,
            text="Open the link below and enter the code to authenticate.",
            foreground="#444",
        ).grid(row=1, column=0, pady=2, padx=20)

        ttk.Button(
            self,
            text=verification_uri,
            command=lambda: webbrowser.open(verification_uri),
        ).grid(row=2, column=0, pady=(6, 2), padx=20, sticky="ew")

        ttk.Label(self, text="Your one-time code:", font=("Segoe UI", 9)).grid(
            row=3, column=0, pady=(10, 0)
        )
        ttk.Label(
            self,
            text=user_code,
            font=("Consolas", 22, "bold"),
            foreground="#0078d4",
        ).grid(row=4, column=0, pady=(0, 4))

        ttk.Button(
            self,
            text="Copy Code",
            command=lambda: (self.clipboard_clear(), self.clipboard_append(user_code)),
        ).grid(row=5, column=0, pady=(0, 16), padx=20)


class AccountCreatedDialog(tk.Toplevel):
    """Copy-friendly account creation result dialog."""

    def __init__(self, parent, message: str):
        super().__init__(parent)
        self.title("Account Created")
        self.geometry("560x420")
        self.minsize(480, 320)
        self.transient(parent)
        self.grab_set()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(
            self,
            text="Account Created",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 8))

        frame = ttk.Frame(self)
        frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        text = tk.Text(
            frame,
            wrap="word",
            height=12,
            font=("Consolas", 10),
            padx=8,
            pady=8,
        )
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        text.insert("1.0", message)
        text.configure(state="disabled")
        self._text = text
        self._message = message

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        buttons.columnconfigure(0, weight=1)

        ttk.Button(buttons, text="Copy", command=self._copy).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(buttons, text="OK", command=self.destroy).grid(row=0, column=2)

        self.bind("<Control-c>", lambda _e: self._copy())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, self._focus_text)

    def _focus_text(self):
        try:
            self._text.focus_set()
            self._text.tag_add("sel", "1.0", "end-1c")
        except tk.TclError:
            pass

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._message)
        self.update()
