"""Preset 'Ask AI' questions for the transcriber and meeting areas.

Ships with sensible defaults; the user (admin) can add or remove their own,
stored locally in the config so they persist."""

DEFAULTS = [
    "Summarize this into concise key points.",
    "List every action item and next step.",
    "Extract all the steps or instructions mentioned, in order.",
    "Pull out all names, tools, links and resources referenced.",
    "Write a short professional summary I could paste into an email.",
    "What are the key takeaways, and why do they matter?",
    "Turn this into a clean bulleted outline.",
]


def all_prompts(cfg):
    custom = (cfg or {}).get("custom_prompts") or []
    return list(DEFAULTS) + [c for c in custom if c not in DEFAULTS]


def add_prompt(cfg, text):
    text = (text or "").strip()
    if not text or text in DEFAULTS:
        return
    lst = list((cfg or {}).get("custom_prompts") or [])
    if text not in lst:
        lst.append(text)
        cfg["custom_prompts"] = lst
        _save(cfg)


def remove_prompt(cfg, text):
    lst = [t for t in ((cfg or {}).get("custom_prompts") or []) if t != text]
    cfg["custom_prompts"] = lst
    _save(cfg)


def _save(cfg):
    try:
        from . import config as _c
        _c.save(cfg)
    except Exception:
        pass


MANAGE_LABEL = "\u2699  Add / edit my presets\u2026"


def menu_items(cfg):
    """Preset questions plus the 'manage' entry at the very bottom."""
    return all_prompts(cfg) + [MANAGE_LABEL]


def manage_dialog(parent, cfg, on_change=lambda: None):
    """Add / edit / delete YOUR questions. Defaults stay locked. Bottom controls
    are pinned so they can't be pushed off; the list scrolls and hovering a line
    shows it in full."""
    import tkinter as tk
    from tkinter import ttk, messagebox
    from . import theme

    dlg = tk.Toplevel(parent)
    dlg.title("Ask-AI questions")
    dlg.geometry("560x480")
    dlg.minsize(480, 420)
    dlg.attributes("-topmost", True)
    theme.apply(dlg)

    ttk.Label(dlg, text="Ask-AI questions", style="Title.TLabel").pack(
        anchor="w", padx=14, pady=(12, 4))
    ttk.Label(dlg, style="Dim.TLabel", wraplength=520, text=(
        "(default) ones are built in. Type a question below and click Add. "
        "Hover any line to read it in full; edit or delete the ones you add.")
        ).pack(anchor="w", padx=14)

    # ---- bottom controls FIRST so they can never be pushed off-screen ----
    btnbar = ttk.Frame(dlg); btnbar.pack(side="bottom", fill="x", padx=14,
                                         pady=(2, 12))
    ttk.Button(btnbar, text="Edit selected",
               command=lambda: _edit()).pack(side="left")
    ttk.Button(btnbar, text="Delete selected",
               command=lambda: _del()).pack(side="left", padx=6)
    ttk.Button(btnbar, text="Done", style="Accent.TButton",
               command=dlg.destroy).pack(side="right")
    status = ttk.Label(dlg, style="Dim.TLabel", text="")
    status.pack(side="bottom", anchor="w", padx=14)
    addrow = ttk.Frame(dlg); addrow.pack(side="bottom", fill="x", padx=14,
                                         pady=(4, 2))
    ttk.Label(addrow, text="New question:").pack(side="left")
    ent = tk.Entry(addrow, bg=theme.FIELD, fg=theme.FG, insertbackground=theme.FG,
                   relief="solid", borderwidth=1)
    ent.pack(side="left", fill="x", expand=True, padx=(6, 6), ipady=3)
    ttk.Button(addrow, text="Add", style="Accent.TButton",
               command=lambda: _add()).pack(side="left")

    # ---- scrollable list (fills the rest) ----
    lwrap = ttk.Frame(dlg); lwrap.pack(fill="both", expand=True, padx=14, pady=6)
    vs = ttk.Scrollbar(lwrap, orient="vertical")
    hs = ttk.Scrollbar(lwrap, orient="horizontal")
    lb = tk.Listbox(lwrap, bg=theme.FIELD, fg=theme.FG,
                    selectbackground=theme.ACCENT, selectforeground="#ffffff",
                    borderwidth=0, highlightthickness=0, font=theme.FONT,
                    activestyle="none", yscrollcommand=vs.set,
                    xscrollcommand=hs.set)
    vs.configure(command=lb.yview); hs.configure(command=lb.xview)
    lb.grid(row=0, column=0, sticky="nsew")
    vs.grid(row=0, column=1, sticky="ns")
    hs.grid(row=1, column=0, sticky="ew")
    lwrap.rowconfigure(0, weight=1); lwrap.columnconfigure(0, weight=1)
    lb.bind("<MouseWheel>",
            lambda e: (lb.yview_scroll(int(-e.delta / 120), "units"), "break")[1])

    def _reload():
        lb.delete(0, "end")
        for q in DEFAULTS:
            lb.insert("end", "  (default)  " + q)
        for q in (cfg.get("custom_prompts") or []):
            lb.insert("end", "  (mine)  " + q)

    def _mine():
        sel = lb.curselection()
        if not sel:
            return None
        label = lb.get(sel[0])
        return label.split("(mine)", 1)[1].strip() if "(mine)" in label else None

    def _add():
        t = ent.get().strip()
        if not t:
            status.configure(text="Type a question first."); return
        add_prompt(cfg, t); ent.delete(0, "end"); _reload(); on_change()
        status.configure(text="Added ✓")
    ent.bind("<Return>", lambda e: (_add(), "break")[1])

    def _ask_block(title, initial=""):
        d = tk.Toplevel(dlg); d.title(title); d.attributes("-topmost", True)
        d.geometry("540x260"); theme.apply(d)
        box = theme.dark_text(d, wrap="word", height=6)
        box.pack(fill="both", expand=True, padx=14, pady=(12, 8))
        box.insert("1.0", initial); box.focus_set()
        out = {"v": None}
        bar = ttk.Frame(d); bar.pack(fill="x", padx=14, pady=(0, 12))

        def _ok():
            out["v"] = box.get("1.0", "end").strip(); d.destroy()
        ttk.Button(bar, text="Save", style="Accent.TButton",
                   command=_ok).pack(side="right")
        ttk.Button(bar, text="Cancel", command=d.destroy).pack(side="right",
                                                               padx=8)
        try:
            d.grab_set()
        except Exception:
            pass
        dlg.wait_window(d)
        return out["v"]

    def _edit():
        q = _mine()
        if not q:
            messagebox.showinfo("Edit", "Select one of YOUR questions "
                                "(defaults are locked).", parent=dlg); return
        new = _ask_block("Edit question", q)
        if new and new.strip():
            remove_prompt(cfg, q); add_prompt(cfg, new.strip()); _reload()
            on_change()

    def _del():
        q = _mine()
        if not q:
            messagebox.showinfo("Delete", "Select one of YOUR questions "
                                "(defaults can't be deleted).", parent=dlg); return
        remove_prompt(cfg, q); _reload(); on_change()

    # ---- hover a line to read it in full ----
    tipref = {"w": None, "i": -1}

    def _hide_tip(_e=None):
        if tipref["w"]:
            try:
                tipref["w"].destroy()
            except Exception:
                pass
        tipref["w"] = None; tipref["i"] = -1

    def _show_tip(e):
        if lb.size() == 0:
            return
        i = lb.nearest(e.y)
        if i < 0 or i >= lb.size():
            _hide_tip(); return
        if i == tipref["i"] and tipref["w"]:
            return
        _hide_tip(); tipref["i"] = i
        tw = tk.Toplevel(lb); tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=lb.get(i).strip(), bg=theme.PANEL, fg=theme.FG,
                 wraplength=480, justify="left", relief="solid", borderwidth=1,
                 padx=6, pady=4, font=theme.FONT).pack()
        tw.wm_geometry(f"+{e.x_root + 14}+{e.y_root + 16}")
        tipref["w"] = tw
    lb.bind("<Motion>", _show_tip)
    lb.bind("<Leave>", _hide_tip)

    _reload()


# ---- saved question sets (named groups of presets you can re-run) ----

def get_sets(cfg):
    return cfg.get("prompt_sets") or []


def set_names(cfg):
    return [s.get("name", "") for s in get_sets(cfg) if s.get("name")]


def get_set(cfg, name):
    for s in get_sets(cfg):
        if s.get("name") == name:
            return list(s.get("questions") or [])
    return []


def save_set(cfg, name, questions):
    name = (name or "").strip()
    if not name:
        return
    sets = [s for s in get_sets(cfg) if s.get("name") != name]
    sets.append({"name": name, "questions": list(questions)})
    cfg["prompt_sets"] = sets
    try:
        from . import config as _c
        _c.save(cfg)
    except Exception:
        pass


def delete_set(cfg, name):
    cfg["prompt_sets"] = [s for s in get_sets(cfg) if s.get("name") != name]
    try:
        from . import config as _c
        _c.save(cfg)
    except Exception:
        pass


def manage_sets(parent, cfg, on_change=lambda: None):
    """Simple SET builder: tick questions, name it, Save set. Bottom controls
    are pinned; hovering a question shows it in full."""
    import tkinter as tk
    from tkinter import ttk
    from . import theme, helptip

    dlg = tk.Toplevel(parent)
    dlg.title("Question sets")
    dlg.geometry("520x560")
    dlg.minsize(460, 420)
    dlg.attributes("-topmost", True)
    theme.apply(dlg)

    ttk.Label(dlg, text="Question sets", style="Title.TLabel").pack(
        anchor="w", padx=14, pady=(12, 2))
    ttk.Label(dlg, style="Dim.TLabel", wraplength=480, text=(
        "Tick the questions for this set, type a name, click Save set. Hover a "
        "question to read it in full. Auto-batch runs the set you pick.")).pack(
        anchor="w", padx=14)

    top = ttk.Frame(dlg); top.pack(fill="x", padx=14, pady=(8, 2))
    ttk.Label(top, text="Load set:").pack(side="left")
    setvar = tk.StringVar(value="—")
    setmenu = ttk.OptionMenu(top, setvar, "—")
    setmenu.configure(width=16)
    setmenu.pack(side="left", padx=(6, 8))
    ttk.Button(top, text="Delete set", command=lambda: _del()).pack(side="left")
    ttk.Button(top, text="＋ Add/edit questions…",
               command=lambda: manage_dialog(dlg, cfg, _rebuild)).pack(side="right")

    # ---- bottom controls FIRST so they can't be pushed off-screen ----
    ttk.Button(dlg, text="Done", command=dlg.destroy).pack(
        side="bottom", anchor="e", padx=14, pady=(4, 12))
    status = ttk.Label(dlg, style="Dim.TLabel", text="")
    status.pack(side="bottom", anchor="w", padx=14)
    nrow = ttk.Frame(dlg); nrow.pack(side="bottom", fill="x", padx=14,
                                     pady=(6, 4))
    ttk.Label(nrow, text="Set name:").pack(side="left")
    namevar = tk.StringVar()
    tk.Entry(nrow, textvariable=namevar, width=22, bg=theme.FIELD, fg=theme.FG,
             insertbackground=theme.FG, relief="solid", borderwidth=1).pack(
             side="left", padx=(6, 8), ipady=2)
    ttk.Button(nrow, text="Save set", style="Accent.TButton",
               command=lambda: _save()).pack(side="left")

    sc = theme.Scrollable(dlg)
    sc.pack(fill="both", expand=True, padx=14, pady=6)
    vars_by_q = {}

    def _refresh_menu():
        m = setmenu["menu"]; m.delete(0, "end")
        m.add_command(label="—", command=lambda: _load("—"))
        for n in set_names(cfg):
            m.add_command(label=n, command=lambda n=n: _load(n))

    def _rebuild():
        for w in sc.inner.winfo_children():
            w.destroy()
        vars_by_q.clear()
        for q in all_prompts(cfg):
            v = tk.BooleanVar(value=False)
            vars_by_q[q] = v
            cb = ttk.Checkbutton(sc.inner, text=q, variable=v)
            cb.pack(anchor="w", pady=1)
            helptip.tip(cb, q)          # hover shows the full question
        _refresh_menu()

    def _load(name):
        setvar.set(name if name else "—")
        if name in ("—", ""):
            for v in vars_by_q.values():
                v.set(False)
            namevar.set(""); return
        chosen = set(get_set(cfg, name))
        for q, v in vars_by_q.items():
            v.set(q in chosen)
        namevar.set(name)

    def _save():
        picks = [q for q, v in vars_by_q.items() if v.get()]
        nm = namevar.get().strip()
        if not nm:
            status.configure(text="Type a set name first."); return
        if not picks:
            status.configure(text="Tick at least one question."); return
        save_set(cfg, nm, picks); _refresh_menu(); on_change()
        status.configure(text=f"Saved set '{nm}' ({len(picks)} questions) ✓")

    def _del():
        n = setvar.get()
        if n in ("—", ""):
            status.configure(text="Pick a set to delete."); return
        delete_set(cfg, n); _refresh_menu(); _load("—"); on_change()
        status.configure(text=f"Deleted set '{n}'.")

    _rebuild()
