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


# ============================================================================
# Standalone Research project window (Pro): find videos -> questions -> report
# ============================================================================
class ResearchWindow:
    SORTS = ["Most viewed", "Newest", "Relevance", "Rating"]
    WINDOWS = ["Any", "Today", "This week", "This month", "This year"]
    DURATIONS = ["Any length", "Under 4 min", "4\u201320 min", "Over 20 min"]

    def __init__(self, parent, cfg):
        self.cfg = cfg
        self._videos = []          # [(url, title)]
        self._qrows = []           # [(row_frame, text_widget)]
        self._cancel = False
        self._busy = False
        self._report_path = ""

        self.win = tk.Toplevel(parent)
        self.win.title("EchoQuill — Research project")
        self.win.geometry("740x600")
        self.win.minsize(700, 480)
        theme.apply(self.win)

        top = ttk.Frame(self.win)
        top.pack(fill="x", padx=16, pady=(12, 2))
        ttk.Label(top, text="Research project", style="Title.TLabel").pack(
            side="left")
        ttk.Label(self.win, style="Dim.TLabel", wraplength=700, text=(
            "Find sources (videos and/or web) → ask questions → one cited "
            "report. Needs AI Enhancement set up.")).pack(
            anchor="w", padx=16, pady=(0, 2))

        nb = ttk.Notebook(self.win)
        t1 = ttk.Frame(nb)
        t2 = ttk.Frame(nb)
        nb.add(t1, text="  1 · Find sources  ")
        nb.add(t2, text="  2 · Questions  ")
        self._build_find(t1)
        self._build_questions(t2)
        self._build_runbar()   # pinned to the bottom so it never scrolls off
        nb.pack(fill="both", expand=True, padx=12, pady=(2, 2))

        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            if parent is not None and parent.winfo_viewable():
                self.win.transient(parent)   # only when parent is visible;
                # a transient of the hidden root won't show (pill/tray launch)
        except Exception:
            pass
        theme.bring_to_front(self.win)

    def _on_mode(self):
        web = self.mode_var.get() in ("Web", "Both")
        vids = self.mode_var.get() in ("Videos", "Both")
        try:
            if web:
                self.perq_lbl.pack(side="left")
                self.perq_menu.pack(side="left", padx=(4, 0))
            else:
                self.perq_lbl.pack_forget()
                self.perq_menu.pack_forget()
            # dim the video search area when Web-only (still visible, just noted)
            self._search_hint.configure(
                text=("" if vids else
                      "Web-only: skip the video search below; your questions "
                      "become the Google searches."))
        except Exception:
            pass

    # ------------------------------------------------------------- tab 1
    def _build_find(self, f):
        nm = ttk.Frame(f); nm.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(nm, text="Project name:").pack(side="left")
        self.name_var = tk.StringVar()
        _e = tk.Entry(nm, textvariable=self.name_var, width=28, bg=theme.FIELD,
                      fg=theme.FG, insertbackground=theme.FG, relief="solid",
                      borderwidth=1)
        _e.pack(side="left", padx=(6, 0))
        helptip.tip(_e, "Required. Names the project and its folder; the report "
                    "is saved inside it.")
        ttk.Label(nm, text="  Sources:").pack(side="left")
        self.mode_var = tk.StringVar(value="Videos")
        _mm = ttk.OptionMenu(nm, self.mode_var, "Videos", "Videos", "Web",
                             "Both", command=lambda *_: self._on_mode())
        _mm.pack(side="left", padx=(4, 8))
        helptip.tip(_mm, "Videos = YouTube (transcribe). Web = Google pages via "
                    "DataForSEO. Both = combine into one report.")
        self.perq_lbl = ttk.Label(nm, text="Web pages/question:")
        self.perq_var = tk.StringVar(value="10")
        self.perq_menu = ttk.OptionMenu(nm, self.perq_var, "10",
                                        *[str(i) for i in range(1, 11)])
        helptip.tip(self.perq_menu, "For Web/Both: how many top Google results "
                    "to read per question (1-10). More = more thorough + more "
                    "cost.")

        # folder (always shown)
        fr = ttk.Frame(f); fr.pack(fill="x", padx=10, pady=(2, 2))
        ttk.Label(fr, text="Save to folder:").pack(side="left")
        self.folder_var = tk.StringVar()
        _fe = tk.Entry(fr, textvariable=self.folder_var, bg=theme.FIELD,
                       fg=theme.FG, insertbackground=theme.FG, relief="solid",
                       borderwidth=1)
        _fe.pack(side="left", fill="x", expand=True, padx=(6, 6))
        helptip.tip(_fe, "Optional. Leave blank to save under "
                    "Transcriptions\\Research\\<project name>.")
        _fb = ttk.Button(fr, text="Browse…", command=self._browse)
        _fb.pack(side="left")
        helptip.tip(_fb, "Pick where to save this project's files.")

        # web-only note (shown when the video area is hidden)
        self._search_hint = ttk.Label(f, style="Dim.TLabel", text="")
        self._search_hint.pack(anchor="w", padx=10)

        # ---- video-search area (hidden entirely in Web-only mode) ----
        self._vid_area = ttk.Frame(f)
        va = self._vid_area
        sr = ttk.Frame(va); sr.pack(fill="x", padx=0, pady=(4, 2))
        ttk.Label(sr, text="Search:").pack(side="left")
        self.query_var = tk.StringVar()
        _q = tk.Entry(sr, textvariable=self.query_var, bg=theme.FIELD,
                      fg=theme.FG, insertbackground=theme.FG, relief="solid",
                      borderwidth=1)
        _q.pack(side="left", fill="x", expand=True, padx=(6, 6))
        helptip.tip(_q, "What to search on YouTube. Put an exact phrase in "
                    '"double quotes" to match it exactly.')
        self.fetch_btn = ttk.Button(sr, text="Fetch", style="Accent.TButton",
                                    command=self._fetch)
        self.fetch_btn.pack(side="left")
        helptip.tip(self.fetch_btn, "Search YouTube and add videos to the list "
                    "below.")

        op = ttk.Frame(va); op.pack(fill="x", pady=(2, 2))
        ttk.Label(op, text="Sort:").pack(side="left")
        self.sort_var = tk.StringVar(value="Most viewed")
        ttk.OptionMenu(op, self.sort_var, "Most viewed", *self.SORTS).pack(
            side="left", padx=(4, 12))
        ttk.Label(op, text="From:").pack(side="left")
        self.window_var = tk.StringVar(value="Any")
        ttk.OptionMenu(op, self.window_var, "Any", *self.WINDOWS).pack(
            side="left", padx=(4, 12))
        ttk.Label(op, text="Length:").pack(side="left")
        self.dur_var = tk.StringVar(value="Any length")
        ttk.OptionMenu(op, self.dur_var, "Any length", *self.DURATIONS).pack(
            side="left", padx=(4, 12))
        ttk.Label(op, text="How many:").pack(side="left")
        self.count_var = tk.StringVar(value="")
        tk.Entry(op, textvariable=self.count_var, width=5, bg=theme.FIELD,
                 fg=theme.FG, insertbackground=theme.FG, relief="solid",
                 borderwidth=1).pack(side="left", padx=(4, 0))

        lf = ttk.Frame(va); lf.pack(fill="both", expand=True, pady=(6, 2))
        self.vids = theme.dark_listbox(lf, height=7)
        self.vids.configure(selectmode="extended")
        _sb = ttk.Scrollbar(lf, orient="vertical", command=self.vids.yview)
        self.vids.configure(yscrollcommand=_sb.set)
        self.vids.bind("<Button-3>", self._vids_right_click)
        self.vids.bind("<Delete>", lambda e: self._remove_selected())
        _sb.pack(side="right", fill="y")
        self.vids.pack(side="left", fill="both", expand=True)
        helptip.tip(self.vids, "The videos that will be transcribed. Select "
                    "rows (Ctrl-click for several) and Remove selected to drop "
                    "long ones; right-click also removes.")

        br = ttk.Frame(va); br.pack(fill="x", pady=(0, 6))
        self.vcount = ttk.Label(br, style="Dim.TLabel", text="0 videos")
        self.vcount.pack(side="left")
        _clr = ttk.Button(br, text="Clear list", command=self._clear_videos)
        _clr.pack(side="right")
        helptip.tip(_clr, "Remove all fetched videos and start over.")
        _rms = ttk.Button(br, text="Remove selected",
                          command=self._remove_selected)
        _rms.pack(side="right", padx=6)
        helptip.tip(_rms, "Drop the highlighted video(s) from this run.")

        self._vid_area.pack(fill="both", expand=True, padx=10)
        self._on_mode()   # set initial visibility

    def _on_mode(self):
        m = self.mode_var.get()
        web = m in ("Web", "Both")
        vids = m in ("Videos", "Both")
        try:
            if web:
                self.perq_lbl.pack(side="left")
                self.perq_menu.pack(side="left", padx=(4, 0))
            else:
                self.perq_lbl.pack_forget()
                self.perq_menu.pack_forget()
            if vids:
                self._search_hint.configure(text="")
                self._vid_area.pack(fill="both", expand=True, padx=10)
            else:
                self._vid_area.pack_forget()
                self._search_hint.configure(
                    text="Web mode: your questions ARE the Google searches — "
                    "no video search needed. Add questions on tab 2, then Start.")
        except Exception:
            pass

    # ------------------------------------------------------------- tab 2
    # ------------------------------------------------------------- tab 2
    def _build_questions(self, f):
        ai = ttk.Frame(f); ai.pack(fill="x", padx=10, pady=(10, 2))
        ttk.Label(ai, style="Dim.TLabel", wraplength=700, text=(
            "Describe your goal and let AI draft the questions — then edit or "
            "delete any. Or type your own below.")).pack(anchor="w")
        self.goal = theme.dark_text(f, wrap="word", height=2)
        self.goal.pack(fill="x", padx=10, pady=(2, 2))
        helptip.tip(self.goal, "Describe what you want to learn, e.g. 'I want to "
                    "grow ginger — every factor: seeding, humidity, watering, "
                    "time to harvest.' AI turns this into questions.")
        gr = ttk.Frame(f); gr.pack(fill="x", padx=10, pady=(0, 4))
        _gen = ttk.Button(gr, text="Generate questions", style="Accent.TButton",
                          command=lambda: self._gen(False))
        _gen.pack(side="left")
        helptip.tip(_gen, "Ask AI to draft a full question set from your goal.")
        _exp = ttk.Button(gr, text="Expand questions ↻",
                          command=lambda: self._gen(True))
        _exp.pack(side="left", padx=8)
        helptip.tip(_exp, "Ask AI for MORE questions on top of the current set. "
                    "Press repeatedly to grow it.")
        _addq = ttk.Button(gr, text="+ Add", command=lambda: self._q_add())
        _addq.pack(side="left", padx=8)
        helptip.tip(_addq, "Add a blank question box to type your own.")
        self.qcount = ttk.Label(gr, style="Dim.TLabel", text="0 questions")
        self.qcount.pack(side="right")

        body = ttk.Frame(f); body.pack(fill="both", expand=True, padx=8,
                                       pady=(2, 2))
        self.qcanvas = tk.Canvas(body, bg=theme.BG, highlightthickness=0)
        _qsb = ttk.Scrollbar(body, orient="vertical",
                             command=self.qcanvas.yview)
        self.qcanvas.configure(yscrollcommand=_qsb.set)
        _qsb.pack(side="right", fill="y")
        self.qcanvas.pack(side="left", fill="both", expand=True)
        self.qinner = ttk.Frame(self.qcanvas)
        self._qwin = self.qcanvas.create_window((0, 0), window=self.qinner,
                                                anchor="nw")
        self.qinner.bind("<Configure>", lambda e: self.qcanvas.configure(
            scrollregion=self.qcanvas.bbox("all")))
        self.qcanvas.bind("<Configure>", lambda e: self.qcanvas.itemconfigure(
            self._qwin, width=e.width))

        sr = ttk.Frame(f); sr.pack(fill="x", padx=10, pady=(2, 2))
        _sv = ttk.Button(sr, text="Save set…", command=self._save_set)
        _sv.pack(side="left")
        helptip.tip(_sv, "Save these questions as a reusable named set.")
        _ld = ttk.Button(sr, text="Load set…", command=self._load_set)
        _ld.pack(side="left", padx=8)
        helptip.tip(_ld, "Replace the list with a saved question set.")

        ar = ttk.Frame(f); ar.pack(fill="x", padx=10, pady=(6, 8))
        self.auto_on = tk.BooleanVar(value=False)
        _ac = ttk.Checkbutton(ar, text="If some questions aren't answered, "
                              "auto-search for more videos",
                              variable=self.auto_on, command=self._toggle_auto)
        _ac.pack(side="left")
        helptip.tip(_ac, "After the run, AI searches for more videos to fill "
                    "any unanswered questions, then tries again.")
        self.autorow = ttk.Frame(ar)
        ttk.Label(self.autorow, text="  max extra searches:").pack(side="left")
        self.auto_max = tk.StringVar(value="2")
        _am = ttk.OptionMenu(self.autorow, self.auto_max, "2", "1", "2", "3",
                             "4", "5")
        _am.pack(side="left", padx=(4, 0))
        helptip.tip(_am, "How many extra search rounds to try before stopping "
                    "(default 2).")

    # ------------------------------------------------------------- run bar
    def _build_runbar(self):
        bar = ttk.Frame(self.win)
        bar.pack(side="bottom", fill="x", padx=16, pady=(2, 8))
        self.start_btn = ttk.Button(bar, text="Start", style="Accent.TButton",
                                    command=self._start)
        self.start_btn.pack(side="left")
        helptip.tip(self.start_btn, "Gather your sources (videos and/or web), "
                    "answer every question, and build the cited report.")
        self.stop_btn = ttk.Button(bar, text="Stop", command=self._stop,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        helptip.tip(self.stop_btn, "Halt after the current video.")
        _of = ttk.Button(bar, text="Open folder", command=self._open_folder)
        _of.pack(side="left", padx=8)
        helptip.tip(_of, "Open this project's folder in File Explorer.")
        self.report_btn = ttk.Button(bar, text="Open report",
                                     command=self._open_report, state="disabled")
        self.report_btn.pack(side="left", padx=8)
        helptip.tip(self.report_btn, "Open the finished HTML report.")
        ttk.Button(bar, text="Close", command=self._on_close).pack(side="right")

        self.log = theme.dark_text(self.win, wrap="word", height=4)
        self.log.pack(side="bottom", fill="x", padx=16, pady=(2, 2))
        self.status = ttk.Label(self.win, style="Dim.TLabel", text="")
        self.status.pack(side="bottom", anchor="w", padx=16)

    # ------------------------------------------------------------- helpers
    def _set(self, msg):
        try:
            self.win.after(0, lambda: self.status.configure(text=msg))
        except Exception:
            pass

    def _logline(self, msg):
        def _do():
            try:
                self.log.insert("end", msg + "\n")
                self.log.see("end")
            except Exception:
                pass
        try:
            self.win.after(0, _do)
        except Exception:
            pass

    def _browse(self):
        from tkinter import filedialog
        from .media_gui import transcripts_dir
        d = filedialog.askdirectory(parent=self.win, title="Save project to",
                                    initialdir=transcripts_dir(self.cfg))
        if d:
            self.folder_var.set(d)

    def _remove_selected(self):
        sel = list(self.vids.curselection())
        if not sel:
            self._set("Select a video in the list first.")
            return
        for i in sorted(sel, reverse=True):
            if 0 <= i < len(self._videos):
                del self._videos[i]
            self.vids.delete(i)
        self.vcount.configure(text=f"{len(self._videos)} videos")
        self._set(f"Removed {len(sel)} video(s).")

    def _vids_right_click(self, e):
        idx = self.vids.nearest(e.y)
        if idx < 0 or idx >= self.vids.size():
            return
        if idx not in self.vids.curselection():
            self.vids.selection_clear(0, "end")
            self.vids.selection_set(idx)
            self.vids.activate(idx)
        m = tk.Menu(self.vids, tearoff=0)
        m.add_command(label="Remove", command=self._remove_selected)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _clear_videos(self):
        self._videos = []
        self.vids.delete(0, "end")
        self.vcount.configure(text="0 videos")

    # ---- fetch
    def _fetch(self):
        import threading
        q = self.query_var.get().strip()
        if not q:
            self._set("Type something to search first.")
            return
        cv = self.count_var.get().strip()
        if not cv.isdigit() or int(cv) <= 0:
            self._set("Enter how many videos to fetch (a number).")
            return
        n = int(cv)
        self.fetch_btn.configure(state="disabled")
        self._set("Searching YouTube…")
        sort, win = self.sort_var.get(), self.window_var.get()
        dur = self.dur_var.get()

        def run():
            from . import research
            try:
                items = research.fetch_search_web(q, self.cfg, sort, win, n,
                                                  log=self._logline,
                                                  duration=dur)
            except Exception as e:
                items = []
                self._logline("search failed: " + str(e)[:100])

            def fill():
                from . import research as _r
                added = 0
                have = set(v[0] for v in self._videos)
                for it in items:
                    u, t = it[0], it[1]
                    d = it[2] if len(it) > 2 else None
                    if u in have:
                        continue
                    have.add(u)
                    self._videos.append((u, t, d))
                    ds = _r.dur_str(d)
                    self.vids.insert("end", (f"{ds} · " if ds else "")
                                     + (t or u))
                    added += 1
                self.vcount.configure(text=f"{len(self._videos)} videos")
                self._set(f"Added {added} video(s)." if added
                          else "No new results.")
                self.fetch_btn.configure(state="normal")
            self.win.after(0, fill)
        threading.Thread(target=run, daemon=True).start()

    # ---- question rows
    def _q_add(self, text=""):
        row = ttk.Frame(self.qinner)
        row.pack(fill="x", pady=3, padx=2)
        num = ttk.Label(row, style="Dim.TLabel", width=3,
                        text=str(len(self._qrows) + 1) + ".")
        num.pack(side="left", anchor="n", pady=(4, 0))
        box = theme.dark_text(row, wrap="word", height=2)
        box.pack(side="left", fill="x", expand=True)
        if text:
            box.insert("1.0", text)
        helptip.tip(box, "A research question. Edit freely; multi-line is fine.")
        rm = ttk.Button(row, text="✕", width=3,
                        command=lambda: self._q_remove(row))
        rm.pack(side="left", padx=(6, 0), anchor="n")
        helptip.tip(rm, "Delete this question.")
        self._qrows.append((row, box))
        self._q_recount()

    def _q_remove(self, row):
        self._qrows = [(r, b) for (r, b) in self._qrows if r is not row]
        row.destroy()
        for i, (r, _b) in enumerate(self._qrows):
            for ch in r.winfo_children():
                if isinstance(ch, ttk.Label):
                    ch.configure(text=str(i + 1) + ".")
                    break
        self._q_recount()

    def _q_clear(self):
        for r, _b in self._qrows:
            r.destroy()
        self._qrows = []
        self._q_recount()

    def _q_list(self):
        out = []
        for _r, box in self._qrows:
            q = box.get("1.0", "end").strip()
            if q:
                out.append(q)
        return out

    def _q_recount(self):
        self.qcount.configure(text=f"{len(self._q_list())} questions")

    # ---- generate / expand
    def _gen(self, more):
        import threading
        goal = self.goal.get("1.0", "end").strip()
        if not goal:
            self._set("Describe your goal first.")
            return
        self._set("Asking AI for questions…")

        def run():
            from . import research
            existing = self._q_list() if more else None
            qs = research.generate_questions(self.cfg, goal, existing,
                                             log=self._logline)

            def fill():
                if not qs:
                    self._set("AI returned no questions (is AI Enhancement set "
                              "up?).")
                    return
                if not more:
                    self._q_clear()
                for q in qs:
                    self._q_add(q)
                self._set(f"Added {len(qs)} question(s).")
            self.win.after(0, fill)
        threading.Thread(target=run, daemon=True).start()

    # ---- sets
    def _save_set(self):
        qs = self._q_list()
        if not qs:
            messagebox.showinfo("EchoQuill", "Add a question first.",
                                parent=self.win)
            return
        name = simpledialog.askstring("Save set", "Name this set:",
                                      parent=self.win)
        if name and name.strip():
            sets = load_sets(); sets[name.strip()] = qs; save_sets(sets)
            self._set(f"Saved set '{name.strip()}'.")

    def _load_set(self):
        sets = load_sets()
        if not sets:
            messagebox.showinfo("EchoQuill", "No saved sets yet.",
                                parent=self.win)
            return
        Picker(self.win, list(sets.keys()),
               lambda n: (self._q_clear(),
                          [self._q_add(q) for q in sets.get(n, [])]))

    def _toggle_auto(self):
        if self.auto_on.get():
            self.autorow.pack(side="left")
        else:
            self.autorow.pack_forget()

    # ---- run
    def _start(self):
        if self._busy:
            return
        from . import license as _lic
        if not _lic.is_pro(self.cfg):
            self._set("Research projects are a Pro feature.")
            return
        name = self.name_var.get().strip()
        if not name:
            self._set("Name the project first (tab 1).")
            return
        mode = self.mode_var.get()
        if mode in ("Videos", "Both") and not self._videos:
            self._set("Fetch some videos first (tab 1).")
            return
        questions = self._q_list()
        if not questions:
            self._set("Add at least one question (tab 2).")
            return
        if mode in ("Web", "Both"):
            from . import dataforseo
            if not dataforseo.configured(self.cfg):
                self._set("Add your DataForSEO login in Settings for web "
                          "research.")
                return
        self._cancel = False
        self._busy = True
        self._report_path = ""
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.report_btn.configure(state="disabled")
        import threading
        threading.Thread(target=self._worker,
                         args=(name, questions), daemon=True).start()

    def _worker(self, name, questions):
        from . import research, notify
        try:
            try:
                notify.done(False)
            except Exception:
                pass
            video_items = [{"url": v[0], "title": v[1]}
                           for v in self._videos]
            folder = self.folder_var.get().strip() or None
            goal = self.goal.get("1.0", "end").strip()
            auto_rounds = int(self.auto_max.get()) if self.auto_on.get() else 0
            sort, win = self.sort_var.get(), self.window_var.get()
            dur = self.dur_var.get()
            cv = self.count_var.get().strip()
            per = int(cv) if cv.isdigit() and int(cv) > 0 else 15

            def refetch(kw):
                return [{"url": it[0], "title": it[1]} for it in
                        research.fetch_search_web(kw, self.cfg, sort, win, per,
                                                  log=self._logline,
                                                  duration=dur)]

            def prog(ph, i, n):
                if ph == "transcribe":
                    self._set(f"Transcribing video {i}/{n}…")
                elif ph == "web-search":
                    self._set(f"Google + reading pages {i}/{n}…")
                else:
                    self._set(f"Reading sources {i}/{n}…")

            def on_done(path):
                self._report_path = path
                try:
                    notify.done(True)
                    notify.send("Research complete", name)
                except Exception:
                    pass

            _mode = {"Videos": "videos", "Web": "web",
                     "Both": "both"}.get(self.mode_var.get(), "videos")
            _perq = int(self.perq_var.get()) if self.perq_var.get().isdigit() \
                else 10

            res = research.run(self.cfg, name, questions, video_items,
                               log=self._logline, cancel=lambda: self._cancel,
                               progress=prog, on_done=on_done, folder=folder,
                               goal=goal, auto_rounds=auto_rounds,
                               refetch=refetch, mode=_mode, web_per_q=_perq)
            path = res.get("report", "")
            if path:
                self._report_path = path
                try:
                    import os
                    os.startfile(path)
                except Exception:
                    pass
                self._set("Research complete ✓" if not self._cancel
                          else "Stopped.")
                un = res.get("unanswered") or []
                if un and not self._cancel:
                    self._show_gaps(un, res.get("suggestion", ""))
            else:
                self._set("Stopped." if self._cancel else "Nothing produced.")
        finally:
            self._busy = False
            try:
                self.win.after(0, self._finish_ui)
            except Exception:
                pass

    def _finish_ui(self):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if self._report_path:
            self.report_btn.configure(state="normal")

    def _show_gaps(self, unanswered, suggestion):
        def _do():
            msg = ("These questions weren't answered by the videos:\n\n"
                   + "\n".join("• " + q for q in unanswered))
            if suggestion:
                msg += ("\n\nTry another search with:\n" + suggestion)
            messagebox.showinfo("Research — unanswered questions", msg,
                                parent=self.win)
        try:
            self.win.after(0, _do)
        except Exception:
            pass

    def _stop(self):
        self._cancel = True
        self._set("Stopping after the current video…")

    def _open_folder(self):
        import os
        folder = self.folder_var.get().strip()
        if not folder:
            name = self.name_var.get().strip()
            if not name:
                self._set("Name the project first.")
                return
            from . import research
            folder = research.project_dir(self.cfg, name)
        try:
            os.startfile(folder)
        except Exception as e:
            self._set("Could not open folder: " + str(e)[:60])

    def _open_report(self):
        import os
        if self._report_path:
            try:
                os.startfile(self._report_path)
            except Exception:
                pass

    def _on_close(self):
        self._cancel = True
        try:
            self.win.destroy()
        except Exception:
            pass
