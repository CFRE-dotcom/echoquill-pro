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
    """Add / edit / delete YOUR presets. Built-in defaults stay locked."""
    import tkinter as tk
    from tkinter import ttk, simpledialog, messagebox
    from . import theme
    dlg = tk.Toplevel(parent)
    dlg.title("Ask-AI presets")
    dlg.geometry("540x440")
    dlg.attributes("-topmost", True)
    theme.apply(dlg)
    ttk.Label(dlg, text="Ask-AI questions", style="Title.TLabel"
              ).pack(anchor="w", padx=14, pady=(12, 4))
    ttk.Label(dlg, style="Dim.TLabel", wraplength=500, text=(
        "The (default) ones are built in. Add your own below, and edit or "
        "delete the ones you added.")).pack(anchor="w", padx=14)
    lb = theme.dark_listbox(dlg, height=12)
    lb.pack(fill="both", expand=True, padx=14, pady=6)

    def reload():
        lb.delete(0, "end")
        for q in DEFAULTS:
            lb.insert("end", "  (default)  " + q)
        for q in (cfg.get("custom_prompts") or []):
            lb.insert("end", "  (mine)  " + q)
    reload()

    ttk.Label(dlg, style="Dim.TLabel",
              text="Add a new question (Ctrl+Enter or the Add button):").pack(
              anchor="w", padx=14, pady=(4, 0))
    row = ttk.Frame(dlg); row.pack(fill="x", padx=14, pady=(0, 6))
    ent = theme.dark_text(row, wrap="word", height=3)
    ent.pack(side="left", fill="x", expand=True)

    def _mine():
        sel = lb.curselection()
        if not sel:
            return None
        label = lb.get(sel[0])
        return label.split("(mine)", 1)[1].strip() if "(mine)" in label else None

    def add():
        t = ent.get("1.0", "end").strip()
        if t:
            add_prompt(cfg, t); ent.delete("1.0", "end"); reload(); on_change()
    ent.bind("<Control-Return>", lambda e: (add(), "break")[1])
    ttk.Button(row, text="Add", command=add).pack(side="left", padx=(6, 0))

    def _ask_block(title, initial=""):
        d = tk.Toplevel(dlg); d.title(title); d.attributes("-topmost", True)
        d.geometry("540x300"); d.resizable(True, True); theme.apply(d)
        ttk.Label(d, style="Dim.TLabel",
                  text="Edit the question, then Save:").pack(
                  anchor="w", padx=14, pady=(12, 4))
        box = theme.dark_text(d, wrap="word", height=8)
        box.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        box.insert("1.0", initial); box.focus_set()
        res = {"v": None}
        bar = ttk.Frame(d); bar.pack(fill="x", padx=14, pady=(0, 12))
        def _ok():
            res["v"] = box.get("1.0", "end").strip(); d.destroy()
        ttk.Button(bar, text="Save", style="Accent.TButton",
                   command=_ok).pack(side="right")
        ttk.Button(bar, text="Cancel", command=d.destroy).pack(
            side="right", padx=8)
        try:
            d.grab_set()
        except Exception:
            pass
        dlg.wait_window(d)
        return res["v"]

    def edit():
        q = _mine()
        if not q:
            messagebox.showinfo("Edit", "Pick one of YOUR presets (defaults are locked).", parent=dlg); return
        new = _ask_block("Edit preset", q)
        if new and new.strip():
            remove_prompt(cfg, q); add_prompt(cfg, new.strip()); reload(); on_change()

    def delete():
        q = _mine()
        if not q:
            messagebox.showinfo("Delete", "Pick one of YOUR presets (defaults can't be deleted).", parent=dlg); return
        remove_prompt(cfg, q); reload(); on_change()
    brow = ttk.Frame(dlg); brow.pack(fill="x", padx=14, pady=(0, 10))
    ttk.Button(brow, text="Edit selected", command=edit).pack(side="left")
    ttk.Button(brow, text="Delete selected", command=delete).pack(side="left", padx=6)
    ttk.Button(brow, text="Done", style="Accent.TButton",
               command=dlg.destroy).pack(side="right")


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
    """Simple, single-purpose SET builder: tick questions, name it, Save set."""
    import tkinter as tk
    from tkinter import ttk
    from . import theme

    dlg = tk.Toplevel(parent)
    dlg.title("Question sets")
    dlg.geometry("520x560")
    dlg.attributes("-topmost", True)
    theme.apply(dlg)

    ttk.Label(dlg, text="Question sets", style="Title.TLabel").pack(
        anchor="w", padx=14, pady=(12, 2))
    ttk.Label(dlg, style="Dim.TLabel", wraplength=480, text=(
        "Tick the questions for this set, type a name, click Save set. "
        "Load an existing set to change it. Auto-batch runs the set you pick.")
        ).pack(anchor="w", padx=14)

    top = ttk.Frame(dlg); top.pack(fill="x", padx=14, pady=(8, 2))
    ttk.Label(top, text="Load set:").pack(side="left")
    setvar = tk.StringVar(value="—")
    setmenu = ttk.OptionMenu(top, setvar, "—")
    setmenu.configure(width=18)
    setmenu.pack(side="left", padx=(6, 8))
    ttk.Button(top, text="Delete set", command=lambda: _del()).pack(side="left")
    ttk.Button(top, text="＋ Add/edit questions…",
               command=lambda: manage_dialog(dlg, cfg, _rebuild)).pack(side="right")

    sc = theme.Scrollable(dlg)
    sc.pack(fill="both", expand=True, padx=14, pady=6)
    vars_by_q = {}

    nrow = ttk.Frame(dlg); nrow.pack(fill="x", padx=14, pady=(4, 4))
    ttk.Label(nrow, text="Set name:").pack(side="left")
    namevar = tk.StringVar()
    tk.Entry(nrow, textvariable=namevar, width=22, bg=theme.FIELD, fg=theme.FG,
             insertbackground=theme.FG, relief="solid", borderwidth=1).pack(
             side="left", padx=(6, 8), ipady=2)
    ttk.Button(nrow, text="Save set", style="Accent.TButton",
               command=lambda: _save()).pack(side="left")
    status = ttk.Label(dlg, style="Dim.TLabel", text="")
    status.pack(anchor="w", padx=14)
    ttk.Button(dlg, text="Done", command=dlg.destroy).pack(
        anchor="e", padx=14, pady=(4, 12))

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
            ttk.Checkbutton(sc.inner, text=q, variable=v).pack(anchor="w", pady=1)
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
