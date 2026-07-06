"""Pop-up browser of recent transcriptions.

Every dictation is kept here (locally). Double-click any line - or select it
and hit Copy - to put it back on the clipboard, for when text didn't land
where you wanted it.
"""

import tkinter as tk
from tkinter import ttk

from . import history, license, theme


class ClipboardWindow:
    def __init__(self, root: tk.Tk):
        self.win = tk.Toplevel(root)
        self.win.title("EchoQuill — Recent transcriptions")
        self.win.geometry("560x420")
        self.win.minsize(460, 340)
        self.win.attributes("-topmost", True)
        theme.apply(self.win)

        ttk.Label(self.win, text="Recent transcriptions",
                  style="Title.TLabel").pack(anchor="w", padx=16, pady=(14, 2))
        ttk.Label(self.win, style="Dim.TLabel",
                  text="Double-click a line to copy it."
                  ).pack(anchor="w", padx=16, pady=(0, 8))

        bar = ttk.Frame(self.win)
        bar.pack(side="bottom", fill="x", padx=16, pady=10)
        self.status = ttk.Label(bar, text="", style="Dim.TLabel")
        self.status.pack(side="left")
        ttk.Button(bar, text="Copy selected", style="Accent.TButton",
                   command=self._copy).pack(side="right")
        if self._pro:
            ttk.Button(bar, text="Older →", command=lambda: self._flip(1)).pack(side="right", padx=4)
            ttk.Button(bar, text="← Newer", command=lambda: self._flip(-1)).pack(side="right")

        from . import config as _cfg
        self._pro = license.is_pro(_cfg.load())
        self._page = 0
        self._page_size = 50 if self._pro else 10
        self._all = history.entries(limit=100000 if self._pro else 10)
        self.entries = self._all[:self._page_size]
        self.listbox = theme.dark_listbox(self.win, activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=16)
        for e in self.entries:
            text = e.get("text", "").replace("\n", " ")
            when = str(e.get("date", ""))[11:16]
            shown = text if len(text) <= 80 else text[:77] + "…"
            self.listbox.insert("end", f" {when}   {shown}")
        self.listbox.bind("<Double-Button-1>", lambda e: self._copy())

    def _fill(self):
        self.listbox.delete(0, "end")
        start = self._page * self._page_size
        self.entries = self._all[start:start + self._page_size]
        for e in self.entries:
            text = e.get("text", "").replace("\n", " ")
            when = str(e.get("date", ""))[11:16]
            shown = text if len(text) <= 80 else text[:77] + "…"
            self.listbox.insert("end", f" {when}   {shown}")
        self.status.configure(text=f"Page {self._page + 1} · {len(self._all):,} total")

    def _flip(self, direction):
        max_page = max(0, (len(self._all) - 1) // self._page_size)
        self._page = max(0, min(max_page, self._page + direction))
        self._fill()

    def _copy(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        entry = self.entries[sel[0]]
        try:
            import pyperclip
            pyperclip.copy(entry.get("text", ""))
            self.status.configure(text="Copied ✓")
            self.win.after(1500, lambda: self.status.configure(text=""))
        except Exception:
            self.status.configure(text="Copy failed")
