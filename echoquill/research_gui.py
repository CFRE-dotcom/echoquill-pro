"""Research question-set editor (Pro).

A scrollable list of MULTI-LINE question boxes — add/remove as many as you like
(50, 100, whatever) — with save/load of named sets. Every input and button
carries a tooltip. Returns the working question list to the caller.
"""

import json
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

from . import theme, helptip


def _store_path():
    from .config import app_data_dir
    return app_data_dir() / "research_questions.json"


def load_sets():
    try:
        return json.loads(_store_path().read_text(encoding="utf-8")).get(
            "sets", {})
    except Exception:
        return {}


def save_sets(sets):
    try:
        _store_path().write_text(json.dumps({"sets": sets}, indent=2),
                                 encoding="utf-8")
    except Exception:
        pass


class QuestionsDialog:
    """Edit the working question list. on_apply(list_of_questions) is called
    when the user clicks Done."""

    def __init__(self, parent, questions, on_apply):
        self.on_apply = on_apply
        self._rows = []

        self.win = tk.Toplevel(parent)
        self.win.title("EchoQuill — Research questions")
        self.win.geometry("640x600")
        theme.apply(self.win)

        top = ttk.Frame(self.win)
        top.pack(fill="x", padx=16, pady=(14, 4))
        ttk.Label(top, text="Research questions", style="Title.TLabel").pack(
            side="left")
        ttk.Label(self.win, style="Dim.TLabel", wraplength=600, text=(
            "Ask as many as you want. After every video is transcribed, each "
            "question is answered across ALL of them, with citations.")).pack(
            anchor="w", padx=16)

        # scrollable body ------------------------------------------------
        body = ttk.Frame(self.win)
        body.pack(fill="both", expand=True, padx=12, pady=(8, 4))
        self.canvas = tk.Canvas(body, bg=theme.BG, highlightthickness=0)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win_id = self.canvas.create_window((0, 0), window=self.inner,
                                                 anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._win_id, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._wheel)

        # controls -------------------------------------------------------
        add = ttk.Frame(self.win)
        add.pack(fill="x", padx=16, pady=(2, 2))
        _ba = ttk.Button(add, text="+ Add question", command=self._add)
        _ba.pack(side="left")
        helptip.tip(_ba, "Add another empty question box to the bottom.")
        _bl = ttk.Button(add, text="Load set…", command=self._load)
        _bl.pack(side="left", padx=8)
        helptip.tip(_bl, "Replace the list with a saved question set.")
        _bs = ttk.Button(add, text="Save set…", command=self._save)
        _bs.pack(side="left", padx=8)
        helptip.tip(_bs, "Save the current questions as a named set to reuse "
                    "on future research projects.")
        self.count = ttk.Label(add, style="Dim.TLabel", text="")
        self.count.pack(side="right")

        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=16, pady=(4, 12))
        _bd = ttk.Button(bar, text="Done", style="Accent.TButton",
                         command=self._done)
        _bd.pack(side="right")
        helptip.tip(_bd, "Save these questions to the project and close.")
        _bc = ttk.Button(bar, text="Clear all", command=self._clear)
        _bc.pack(side="left")
        helptip.tip(_bc, "Remove every question box.")

        for q in (questions or [""]):
            self._add(q)
        self._recount()
        self.win.transient(parent)
        theme.bring_to_front(self.win)

    # ---------------------------------------------------------------- rows
    def _wheel(self, e):
        try:
            self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        except Exception:
            pass

    def _add(self, text=""):
        row = ttk.Frame(self.inner)
        row.pack(fill="x", pady=4, padx=2)
        num = ttk.Label(row, style="Dim.TLabel", width=3,
                        text=str(len(self._rows) + 1) + ".")
        num.pack(side="left", anchor="n", pady=(4, 0))
        box = theme.dark_text(row, wrap="word", height=3)
        box.pack(side="left", fill="x", expand=True)
        if text:
            box.insert("1.0", text)
        helptip.tip(box, "Type one research question here. It can be as long "
                    "as you like — multiple sentences are fine.")
        rm = ttk.Button(row, text="✕", width=3,
                        command=lambda: self._remove(row))
        rm.pack(side="left", padx=(6, 0), anchor="n")
        helptip.tip(rm, "Remove this question.")
        self._rows.append((row, box))
        self._recount()

    def _remove(self, row):
        self._rows = [(r, b) for (r, b) in self._rows if r is not row]
        row.destroy()
        self._renumber()
        self._recount()

    def _renumber(self):
        for i, (row, _b) in enumerate(self._rows):
            for ch in row.winfo_children():
                if isinstance(ch, ttk.Label):
                    ch.configure(text=str(i + 1) + ".")
                    break

    def _clear(self):
        for row, _b in self._rows:
            row.destroy()
        self._rows = []
        self._recount()

    def _questions(self):
        out = []
        for _row, box in self._rows:
            q = box.get("1.0", "end").strip()
            if q:
                out.append(q)
        return out

    def _recount(self):
        self.count.configure(text=f"{len(self._questions())} questions")

    # ---------------------------------------------------------------- sets
    def _save(self):
        qs = self._questions()
        if not qs:
            messagebox.showinfo("EchoQuill", "Add a question first.",
                                parent=self.win)
            return
        name = simpledialog.askstring("Save set", "Name this question set:",
                                      parent=self.win)
        if not name or not name.strip():
            return
        sets = load_sets()
        sets[name.strip()] = qs
        save_sets(sets)
        messagebox.showinfo("EchoQuill", f"Saved '{name.strip()}'.",
                            parent=self.win)

    def _load(self):
        sets = load_sets()
        if not sets:
            messagebox.showinfo("EchoQuill", "No saved sets yet.",
                                parent=self.win)
            return
        Picker(self.win, list(sets.keys()),
               lambda n: self._apply_set(sets.get(n, [])))

    def _apply_set(self, qs):
        self._clear()
        for q in (qs or [""]):
            self._add(q)
        self._recount()

    def _done(self):
        self.on_apply(self._questions())
        self.win.destroy()


class Picker:
    """Tiny single-choice list dialog."""

    def __init__(self, parent, names, on_pick):
        self.on_pick = on_pick
        self.win = tk.Toplevel(parent)
        self.win.title("Load set")
        self.win.geometry("320x360")
        theme.apply(self.win)
        ttk.Label(self.win, text="Pick a set", style="Title.TLabel").pack(
            anchor="w", padx=14, pady=(12, 6))
        self.lb = theme.dark_listbox(self.win, height=12)
        self.lb.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        for n in names:
            self.lb.insert("end", n)
        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(bar, text="Load", style="Accent.TButton",
                   command=self._pick).pack(side="right")
        ttk.Button(bar, text="Cancel",
                   command=self.win.destroy).pack(side="right", padx=8)
        self.win.transient(parent)
        theme.bring_to_front(self.win)

    def _pick(self):
        sel = self.lb.curselection()
        if sel:
            self.on_pick(self.lb.get(sel[0]))
        self.win.destroy()
