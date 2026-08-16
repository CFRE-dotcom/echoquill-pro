"""Channel watcher window. Tabbed: Channels / Schedule / Proxy, with a live
monitor and the log always visible below the tabs."""

import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from . import theme, helptip, watcher, notify
from . import prompts as _pr


def _ago(ts):
    if not ts:
        return "never"
    secs = max(0, int(time.time() - ts))
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


LIFE_OPTS = [("No expiry", 0), ("1 day", 1), ("3 days", 3), ("5 days", 5),
             ("7 days", 7), ("14 days", 14), ("30 days", 30), ("60 days", 60),
             ("90 days", 90)]
UPLOAD_OPTS = [("Any time", 0), ("Past 7 days", 7), ("Past 30 days", 30),
               ("Past 60 days", 60), ("Past 90 days", 90)]
DUR_OPTS = ["Any", "Under 4 min", "4-20 min", "Over 20 min"]
SORT_OPTS = ["Relevance", "Upload date"]
_LIFE_L2V = {l: v for l, v in LIFE_OPTS}
_LIFE_V2L = {v: l for l, v in LIFE_OPTS}
_UP_L2V = {l: v for l, v in UPLOAD_OPTS}
_UP_V2L = {v: l for l, v in UPLOAD_OPTS}


def _fmt_left(info):
    if info == "none":
        return "no expiry"
    if info == "expired":
        return "expired"
    secs = int(info)
    if secs < 3600:
        return f"{secs // 60}m left"
    if secs < 86400:
        return f"{secs // 3600}h left"
    return f"{secs // 86400}d left"


def _entry(parent, var, width=None):
    return tk.Entry(parent, textvariable=var, width=width or 0, bg=theme.FIELD,
                    fg=theme.FG, insertbackground=theme.FG, relief="solid",
                    borderwidth=1)


class WatcherWindow:
    def __init__(self, parent, cfg):
        self.cfg = cfg
        self._chan_ids = []
        self._cancel = False
        self._editing = None
        self._editing_search = None
        self._editing_playlist = None
        self._tip = None

        self.win = tk.Toplevel(parent)
        self.win.title("EchoQuill — Channel watcher")
        self.win.geometry("800x660")
        self.win.minsize(680, 580)
        theme.apply(self.win)

        # ---- header ----
        top = ttk.Frame(self.win)
        top.pack(fill="x", padx=16, pady=(12, 2))
        ttk.Label(top, text="Channel watcher", style="Title.TLabel").pack(
            side="left")

        # ---- constant bottom bar: the live monitor, pinned to the very bottom
        mon = tk.Frame(self.win, bg="#141414", bd=1, relief="solid")
        mon.pack(side="bottom", fill="x", padx=16, pady=(2, 6))
        self.monitor = tk.Label(mon, bg="#141414", fg="#4da3ff",
                                font=("Segoe UI", 12, "bold"), anchor="w",
                                justify="left", wraplength=740,
                                text="●  Idle — nothing running")
        self.monitor.pack(fill="x", padx=12, pady=8)
        mon.bind("<Configure>", lambda e: self.monitor.configure(
            wraplength=max(220, e.width - 28)))

        # ---- tabs fill the space above the monitor ----
        self._nb = ttk.Notebook(self.win)
        self._nb.pack(side="top", fill="both", expand=True, padx=12, pady=(6, 4))
        self._tab_list = ttk.Frame(self._nb)
        self._tab_add = ttk.Frame(self._nb)
        tab_sch = ttk.Frame(self._nb)
        tab_prx = ttk.Frame(self._nb)
        self._nb.add(self._tab_list, text="  Channels / keyword searches  ")
        self._nb.add(self._tab_add, text="  Add channel / keyword / playlist  ")
        self._nb.add(tab_sch, text="  Schedule & pacing  ")
        self._nb.add(tab_prx, text="  Proxy  ")
        self._build_list_tab(self._tab_list)
        self._build_add_tab(self._tab_add)
        self._build_schedule_tab(tab_sch)
        self._build_proxy_tab(tab_prx)

        notify.badge(False)   # opening the watcher clears the 'new results' dot
        # also clear it whenever the (already-open) window gets focus
        self.win.bind("<FocusIn>", lambda e: notify.badge(False))
        watcher.add_log_listener(self._log)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.focus_force()
        self.win.after(400, self._drop_topmost)
        self._refresh()
        self._tick_monitor()

    # ================= tab builders =================
    def _build_list_tab(self, f):
        ttk.Label(f, style="Dim.TLabel", wraplength=720, text=(
            "Channels and topic-searches you watch. Double-click a row to edit "
            "it (opens the Add tab) · hover for stats.")).pack(
            anchor="w", padx=8, pady=(8, 2))
        lbf = ttk.Frame(f); lbf.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.lb = theme.dark_listbox(lbf, height=8)
        _lbsb = ttk.Scrollbar(lbf, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=_lbsb.set)
        _lbsb.pack(side="right", fill="y")
        self.lb.pack(side="left", fill="both", expand=True)
        self.lb.bind("<Double-Button-1>", self._edit_selected)
        self.lb.bind("<Motion>", self._hover)
        self.lb.bind("<Leave>", lambda _e: self._hide_tip())
        lrow = ttk.Frame(f); lrow.pack(fill="x", padx=8, pady=(0, 4))
        _bd = ttk.Button(lrow, text="Delete", command=self._delete)
        _bd.pack(side="left")
        helptip.tip(_bd, "Removes the source AND everything stored for it "
                    "(seen list + queued items). Asks first.")
        _bp = ttk.Button(lrow, text="Pause / Resume", command=self._toggle_pause)
        _bp.pack(side="left", padx=6)
        helptip.tip(_bp, "Pause stops it being scanned; Resume turns it back on "
                    "(and un-retires an expired search). Takes effect next cycle.")
        _bw = ttk.Button(lrow, text="Wipe its results",
                         command=self._clear_source)
        _bw.pack(side="left", padx=6)
        helptip.tip(_bw, "Clears this source's queued/finished items AND its "
                    "seen-list so it re-pulls fresh. Files already on disk are "
                    "NOT deleted.")
        _bf = ttk.Button(lrow, text="★ Focus", command=self._toggle_focus)
        _bf.pack(side="left", padx=6)
        helptip.tip(_bf, "Push this source to the FRONT of the queue until it's "
                    "caught up. Click again to un-focus. Marked with ★.")
        _br = ttk.Button(lrow, text="Randomize", command=self._randomize)
        _br.pack(side="left", padx=6)
        helptip.tip(_br, "Shuffle the pending queue order right now.")

        # run controls + status sit ABOVE the log
        brow = ttk.Frame(f); brow.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Button(brow, text="Check now", style="Accent.TButton",
                   command=self._check_now).pack(side="left")
        ttk.Button(brow, text="Refresh", command=self._refresh).pack(
            side="left", padx=8)
        _bstop = ttk.Button(brow, text="Stop", command=self._stop)
        _bstop.pack(side="left", padx=8)
        helptip.tip(_bstop, "Halts the current run after the video in progress.")
        _bclr = ttk.Button(brow, text="Clear queue", command=self._clear_queue)
        _bclr.pack(side="left", padx=8)
        helptip.tip(_bclr, "Deletes every queued item (keeps your channels).")
        _btest = ttk.Button(brow, text="Test notification",
                            command=self._test_notify)
        _btest.pack(side="left", padx=8)
        helptip.tip(_btest, "Fires a sample toast so you can confirm "
                    "notifications work on your PC.")
        ttk.Button(brow, text="Close", command=self._on_close).pack(side="right")

        self.status = ttk.Label(f, style="Dim.TLabel", text="")
        self.status.pack(anchor="w", padx=8, pady=(0, 2))

        logrow = ttk.Frame(f); logrow.pack(fill="x", padx=8, pady=(2, 0))
        ttk.Label(logrow, text="Activity log",
                  style="Section.TLabel").pack(side="left")
        _blog = ttk.Button(logrow, text="Open log file…",
                           command=self._open_logfile)
        _blog.pack(side="right")
        helptip.tip(_blog, "Opens watcher.log - every run's activity is saved "
                    "there so you can review it later, even after closing.")
        self.log = theme.dark_text(f, wrap="word", height=8)
        self.log.pack(fill="x", padx=8, pady=(2, 8))

    def _build_add_tab(self, f):
        self._subnb = ttk.Notebook(f)
        self._subnb.pack(fill="both", expand=True, padx=4, pady=(6, 6))
        self._tab_addch = ttk.Frame(self._subnb)
        self._tab_search = ttk.Frame(self._subnb)
        self._tab_playlist = ttk.Frame(self._subnb)
        self._subnb.add(self._tab_addch, text="  Add a channel  ")
        self._subnb.add(self._tab_search, text="  Search by keyword  ")
        self._subnb.add(self._tab_playlist, text="  Add a playlist  ")
        self._build_add_channel_form(self._tab_addch)
        self._build_search_form(self._tab_search)
        self._build_playlist_form(self._tab_playlist)

    def _build_add_channel_form(self, f):
        self.form_lbl = ttk.Label(f, text="Add a channel",
                                  style="Section.TLabel")
        self.form_lbl.pack(anchor="w", padx=8, pady=(6, 2))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Channel URL / @handle:").pack(side="left")
        self.url_var = tk.StringVar()
        _entry(r, self.url_var).pack(side="left", fill="x", expand=True,
                                     padx=(6, 0))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Catch:").pack(side="left")
        self.k_videos = tk.BooleanVar(value=True)
        self.k_shorts = tk.BooleanVar(value=False)
        self.k_lives = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="Videos", variable=self.k_videos).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Shorts", variable=self.k_shorts).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(r, text="Lives", variable=self.k_lives).pack(side="left", padx=(8, 12))
        ttk.Label(r, text="Keyword:").pack(side="left")
        self.kw_var = tk.StringVar()
        _entry(r, self.kw_var, 16).pack(side="left", padx=(4, 12))
        ttk.Label(r, text="Newest:").pack(side="left")
        self.count_var = tk.StringVar(value="15")
        _entry(r, self.count_var, 5).pack(side="left", padx=(4, 0))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Question set:").pack(side="left")
        self.set_var = tk.StringVar(value="—")
        self.set_menu = ttk.OptionMenu(r, self.set_var, "—")
        self.set_menu.configure(width=18)
        self.set_menu.pack(side="left", padx=(6, 12))
        self._refresh_sets()
        ttk.Label(r, text="Transcript:").pack(side="left")
        self.tmode_var = tk.StringVar(value="Whisper (accurate)")
        ttk.OptionMenu(r, self.tmode_var, "Whisper (accurate)",
                       "Whisper (accurate)", "YouTube captions (fast)").pack(
                       side="left", padx=(4, 0))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Folder:").pack(side="left")
        self.folder_var = tk.StringVar()
        _entry(r, self.folder_var, 22).pack(side="left", padx=(6, 12))
        self.sv = tk.BooleanVar(value=False)
        self.sa = tk.BooleanVar(value=False)
        self.sd = tk.BooleanVar(value=False)
        self.sc = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="Video", variable=self.sv).pack(side="left")
        ttk.Checkbutton(r, text="Audio", variable=self.sa).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Desc", variable=self.sd).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Comments", variable=self.sc).pack(side="left", padx=(6, 0))

        arow = ttk.Frame(f); arow.pack(anchor="w", padx=8, pady=(4, 8))
        self.add_btn = ttk.Button(arow, text="＋ Add channel",
                                  style="Accent.TButton", command=self._add)
        self.add_btn.pack(side="left")
        self.cancel_edit_btn = ttk.Button(arow, text="Cancel edit",
                                          command=self._cancel_edit)

    def _build_playlist_form(self, f):
        self.pl_form_lbl = ttk.Label(f, text="Add a playlist",
                                     style="Section.TLabel")
        self.pl_form_lbl.pack(anchor="w", padx=8, pady=(6, 2))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Playlist URL:").pack(side="left")
        self.pl_url = tk.StringVar()
        _entry(r, self.pl_url).pack(side="left", fill="x", expand=True,
                                    padx=(6, 0))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Keyword (optional):").pack(side="left")
        self.pl_kw = tk.StringVar()
        _entry(r, self.pl_kw, 16).pack(side="left", padx=(4, 12))
        ttk.Label(r, text="Newest:").pack(side="left")
        self.pl_count = tk.StringVar(value="25")
        _entry(r, self.pl_count, 5).pack(side="left", padx=(4, 0))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Question set:").pack(side="left")
        self.pl_set = tk.StringVar(value="—")
        self.pl_set_menu = ttk.OptionMenu(r, self.pl_set, "—")
        self.pl_set_menu.configure(width=18)
        self.pl_set_menu.pack(side="left", padx=(6, 12))
        m = self.pl_set_menu["menu"]; m.delete(0, "end")
        m.add_command(label="—", command=lambda: self.pl_set.set("—"))
        for n in _pr.set_names(self.cfg):
            m.add_command(label=n, command=lambda n=n: self.pl_set.set(n))
        ttk.Label(r, text="Transcript:").pack(side="left")
        self.pl_tmode = tk.StringVar(value="Whisper (accurate)")
        ttk.OptionMenu(r, self.pl_tmode, "Whisper (accurate)",
                       "Whisper (accurate)", "YouTube captions (fast)").pack(
                       side="left", padx=(4, 0))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Folder:").pack(side="left")
        self.pl_folder = tk.StringVar()
        _entry(r, self.pl_folder, 22).pack(side="left", padx=(6, 12))
        self.plv = tk.BooleanVar(value=False)
        self.pla = tk.BooleanVar(value=False)
        self.pld = tk.BooleanVar(value=False)
        self.plc = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="Video", variable=self.plv).pack(side="left")
        ttk.Checkbutton(r, text="Audio", variable=self.pla).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Desc", variable=self.pld).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Comments", variable=self.plc).pack(side="left", padx=(6, 0))

        arow = ttk.Frame(f); arow.pack(anchor="w", padx=8, pady=(4, 8))
        self.add_playlist_btn = ttk.Button(arow, text="＋ Add playlist",
                                           style="Accent.TButton",
                                           command=self._add_playlist)
        self.add_playlist_btn.pack(side="left")
        self.cancel_playlist_btn = ttk.Button(arow, text="Cancel edit",
                                              command=self._cancel_playlist_edit)

    def _build_search_form(self, f):
        self.s_form_lbl = ttk.Label(f, text="Search by keyword",
                                    style="Section.TLabel")
        self.s_form_lbl.pack(anchor="w", padx=8, pady=(6, 2))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Keyword:").pack(side="left")
        self.s_query = tk.StringVar()
        _entry(r, self.s_query).pack(side="left", fill="x", expand=True,
                                     padx=(6, 0))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Type:").pack(side="left")
        self.st_video = tk.BooleanVar(value=True)
        self.st_shorts = tk.BooleanVar(value=True)
        self.st_live = tk.BooleanVar(value=True)
        self.st_playlist = tk.BooleanVar(value=False)
        self.st_channel = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="Video", variable=self.st_video).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Shorts", variable=self.st_shorts).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(r, text="Live", variable=self.st_live).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(r, text="Playlist", variable=self.st_playlist).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(r, text="Channel", variable=self.st_channel).pack(side="left", padx=(8, 0))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Upload window:").pack(side="left")
        self.s_upload = tk.StringVar(value="Past 30 days")
        ttk.OptionMenu(r, self.s_upload, "Past 30 days",
                       *[l for l, _ in UPLOAD_OPTS]).pack(side="left", padx=(4, 12))
        ttk.Label(r, text="Duration:").pack(side="left")
        self.s_dur = tk.StringVar(value="Any")
        ttk.OptionMenu(r, self.s_dur, "Any", *DUR_OPTS).pack(side="left", padx=(4, 12))
        ttk.Label(r, text="Sort:").pack(side="left")
        self.s_sort = tk.StringVar(value="Upload date")
        ttk.OptionMenu(r, self.s_sort, "Upload date", *SORT_OPTS).pack(side="left", padx=(4, 0))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Newest:").pack(side="left")
        self.s_count = tk.StringVar(value="25")
        _entry(r, self.s_count, 5).pack(side="left", padx=(4, 12))
        ttk.Label(r, text="Lifespan:").pack(side="left")
        self.s_life = tk.StringVar(value="30 days")
        ttk.OptionMenu(r, self.s_life, "30 days",
                       *[l for l, _ in LIFE_OPTS]).pack(side="left", padx=(4, 0))
        helptip.tip(r, "Lifespan auto-retires this search after N days so a "
                    "hot-topic watch doesn't run forever.")

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Question set:").pack(side="left")
        self.s_set = tk.StringVar(value="—")
        self.s_set_menu = ttk.OptionMenu(r, self.s_set, "—")
        self.s_set_menu.configure(width=18)
        self.s_set_menu.pack(side="left", padx=(6, 12))
        m = self.s_set_menu["menu"]; m.delete(0, "end")
        m.add_command(label="—", command=lambda: self.s_set.set("—"))
        for n in _pr.set_names(self.cfg):
            m.add_command(label=n, command=lambda n=n: self.s_set.set(n))
        ttk.Label(r, text="Transcript:").pack(side="left")
        self.s_tmode = tk.StringVar(value="Whisper (accurate)")
        ttk.OptionMenu(r, self.s_tmode, "Whisper (accurate)",
                       "Whisper (accurate)", "YouTube captions (fast)").pack(
                       side="left", padx=(4, 0))

        r = ttk.Frame(f); r.pack(fill="x", padx=8, pady=1)
        ttk.Label(r, text="Folder:").pack(side="left")
        self.s_folder = tk.StringVar()
        _entry(r, self.s_folder, 22).pack(side="left", padx=(6, 12))
        self.ssv = tk.BooleanVar(value=False)
        self.ssa = tk.BooleanVar(value=False)
        self.ssd = tk.BooleanVar(value=False)
        self.ssc = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="Video", variable=self.ssv).pack(side="left")
        ttk.Checkbutton(r, text="Audio", variable=self.ssa).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Desc", variable=self.ssd).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Comments", variable=self.ssc).pack(side="left", padx=(6, 0))

        arow = ttk.Frame(f); arow.pack(anchor="w", padx=8, pady=(4, 8))
        self.add_search_btn = ttk.Button(arow, text="＋ Add search",
                                         style="Accent.TButton",
                                         command=self._add_search)
        self.add_search_btn.pack(side="left")
        self.cancel_search_btn = ttk.Button(arow, text="Cancel edit",
                                            command=self._cancel_search_edit)

    def _build_schedule_tab(self, f):
        ttk.Label(f, style="Dim.TLabel", wraplength=720, text=(
            "These four settings are saved with the Save schedule button "
            "below.")).pack(anchor="w", padx=10, pady=(10, 6))

        r = ttk.Frame(f); r.pack(fill="x", padx=10, pady=4)
        ttk.Label(r, text="Check for new every").pack(side="left")
        self.chk_hours = tk.StringVar(
            value=str(self.cfg.get("watch_check_hours", 6)))
        _entry(r, self.chk_hours, 4).pack(side="left", padx=4)
        ttk.Label(r, text="hours").pack(side="left")

        r = ttk.Frame(f); r.pack(fill="x", padx=10, pady=4)
        ttk.Label(r, text="Retry a failed video every").pack(side="left")
        self.retry_min = tk.StringVar(
            value=str(self.cfg.get("watch_retry_minutes", 30)))
        _entry(r, self.retry_min, 5).pack(side="left", padx=4)
        ttk.Label(r, text="minutes").pack(side="left")

        r = ttk.Frame(f); r.pack(fill="x", padx=10, pady=4)
        ttk.Label(r, text="When several are queued, do at most").pack(side="left")
        self.per_cycle = tk.StringVar(
            value=str(self.cfg.get("watch_per_cycle", 5)))
        _entry(r, self.per_cycle, 4).pack(side="left", padx=4)
        ttk.Label(r, text="per cycle").pack(side="left")

        r = ttk.Frame(f); r.pack(fill="x", padx=10, pady=4)
        ttk.Label(r, text="Wait").pack(side="left")
        self.gap_sec = tk.StringVar(
            value=str(self.cfg.get("watch_gap_seconds", 600)))
        _ge = _entry(r, self.gap_sec, 6); _ge.pack(side="left", padx=4)
        ttk.Label(r, text="seconds between each video").pack(side="left")
        helptip.tip(_ge, "Seconds to pause between videos so YouTube does not "
                    "flag the batch. 600 (10 min) is a safe default; you can go "
                    "lower on a residential proxy.")

        ttk.Button(f, text="Save schedule", style="Accent.TButton",
                   command=self._save_sched).pack(anchor="w", padx=10, pady=(10, 8))

        r = ttk.Frame(f); r.pack(fill="x", padx=10, pady=(6, 2))
        ttk.Label(r, text="Processing order:").pack(side="left")
        self._order_labels = {"Fair — spread across sources": "fair",
                              "In order — top to bottom": "order",
                              "Random": "random"}
        self._order_v2l = {v: k for k, v in self._order_labels.items()}
        cur = self._order_v2l.get(self.cfg.get("watch_order", "fair"),
                                  "Fair — spread across sources")
        self.order_var = tk.StringVar(value=cur)
        om = ttk.OptionMenu(r, self.order_var, cur,
                            *self._order_labels.keys(),
                            command=self._save_order)
        om.configure(width=26)
        om.pack(side="left", padx=(6, 0))
        helptip.tip(r, "Fair = one video per source in turn, so every channel/"
                    "search makes progress (recommended). In order = finish the "
                    "top source before the next. Random = shuffle. Focus (on a "
                    "source) always goes first regardless of this.")

    def _build_proxy_tab(self, f):
        self.proxy_on = tk.BooleanVar(value=bool(self.cfg.get("di_enabled")))
        _pc = ttk.Checkbutton(f, text="Run downloads through the proxy "
                              "(residential IP)", variable=self.proxy_on,
                              command=self._toggle_proxy)
        _pc.pack(anchor="w", padx=10, pady=(12, 2))
        ttk.Label(f, style="Dim.TLabel", wraplength=720, text=(
            "Saves instantly the moment you tick it — it is NOT part of the "
            "Save schedule button. Set up the proxy itself (login, password, "
            "location) in Settings ▸ Transcription, and verify it there first."
        )).pack(anchor="w", padx=28, pady=(0, 10))
        ttk.Label(f, style="Dim.TLabel", wraplength=720, text=(
            "Using a residential IP makes YouTube far less likely to "
            "flag a batch, so you can safely lower the gap on the Schedule "
            "tab.")).pack(anchor="w", padx=28)

    # ================= helpers =================
    def _on_close(self):
        try:
            watcher.remove_log_listener(self._log)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass

    def _open_logfile(self):
        try:
            import os
            os.startfile(watcher.logfile_path())
        except Exception as e:
            self._set_status(f"Couldn't open log: {e}")

    def _goto_list(self):
        try:
            self._nb.select(self._tab_list)
        except Exception:
            pass

    def _drop_topmost(self):
        try:
            self.win.attributes("-topmost", False)
        except Exception:
            pass

    def _save_sched(self):
        from . import config as _c
        try:
            self.cfg["watch_check_hours"] = max(1, int(
                self.chk_hours.get().strip() or 6))
            self.cfg["watch_retry_minutes"] = max(1, int(
                self.retry_min.get().strip() or 30))
            self.cfg["watch_per_cycle"] = max(0, int(
                self.per_cycle.get().strip() or 5))
            self.cfg["watch_gap_seconds"] = max(0, int(
                self.gap_sec.get().strip() or 600))
        except Exception:
            self._set_status("Enter whole numbers for hours/minutes/pacing."); return
        _c.save(self.cfg)
        self._set_status("Schedule saved. (Check interval applies next cycle.)")

    def _refresh_sets(self):
        m = self.set_menu["menu"]; m.delete(0, "end")
        m.add_command(label="—", command=lambda: self.set_var.set("—"))
        for n in _pr.set_names(self.cfg):
            m.add_command(label=n, command=lambda n=n: self.set_var.set(n))

    def _set_status(self, msg):
        try:
            self.win.after(0, lambda: self.status.configure(text=msg))
        except Exception:
            pass

    def _log(self, msg):
        def _do():
            self.log.insert("end", msg + "\n")
            self.log.see("end")
        try:
            self.win.after(0, _do)
        except Exception:
            pass

    def _refresh(self):
        d = watcher.load()
        self.lb.delete(0, "end")
        self._chan_ids = []
        for ch in d["channels"]:
            stx = watcher.stats(ch.get("id"))
            tail = " · ✓" + str(stx["done"])
            if stx["last7"]:
                tail += " · 7d:" + str(stx["last7"])
            if stx["done"]:
                tail += " · last " + _ago(stx["last"])
            if stx["pending"]:
                tail += " · " + str(stx["pending"]) + " queued"
            if ch.get("kind") == "search":
                types = ", ".join(ch.get("types") or [])
                win = _UP_V2L.get(int(ch.get("upload_days", 0) or 0),
                                  "custom")
                life = _fmt_left(watcher.expiry_info(ch))
                if not ch.get("enabled", True):
                    off = " (retired)" if ch.get("expired") else " (paused)"
                else:
                    off = ""
                star = "★ " if ch.get("focus") else ""
                self.lb.insert("end",
                               "  " + star + "⌕ " + ch.get("query", "")
                               + "   [" + types + "] · " + win + " · "
                               + life + off + tail)
            elif ch.get("kind") == "playlist":
                kw = f" · kw:{ch['keyword']}" if ch.get("keyword") else ""
                st = ch.get("set_name") or "no set"
                off = "" if ch.get("enabled", True) else " (paused)"
                star = "★ " if ch.get("focus") else ""
                self.lb.insert("end",
                               "  " + star + "▤ " + ch.get("url", "")
                               + "   [playlist]" + kw + " · " + st + off + tail)
            else:
                kinds = ", ".join(ch.get("kinds") or [])
                kw = f" · kw:{ch['keyword']}" if ch.get("keyword") else ""
                st = ch.get("set_name") or "no set"
                off = "" if ch.get("enabled", True) else " (paused)"
                star = "★ " if ch.get("focus") else ""
                self.lb.insert("end",
                               "  " + star + "▸ " + ch.get("url", "")
                               + "   [" + kinds + "]" + kw + " · " + st
                               + off + tail)
            self._chan_ids.append(ch.get("id"))
        c = watcher.counts()
        self.status.configure(text=(
            f"{c['channels']} channels · queue: {c['done']} done · "
            f"{c['pending']} pending · {c['failed']} retrying · "
            f"{c['unavailable']} unavailable"))

    # ---------- hover tooltip ----------
    def _hover(self, e):
        try:
            idx = self.lb.nearest(e.y)
        except Exception:
            return
        if idx < 0 or idx >= len(self._chan_ids):
            self._hide_tip(); return
        cid = self._chan_ids[idx]
        d = watcher.load()
        ch = next((c for c in d["channels"] if c.get("id") == cid), None)
        if not ch:
            self._hide_tip(); return
        stx = watcher.stats(cid)
        if ch.get("kind") == "search":
            head = (f'Search: {ch.get("query","")}\n'
                    f"Type: {', '.join(ch.get('types') or [])}"
                    f"   ·   window: {_UP_V2L.get(int(ch.get('upload_days',0) or 0),'custom')}\n"
                    f"Sort: {ch.get('sort','Relevance')}"
                    f"   ·   lifespan: {_fmt_left(watcher.expiry_info(ch))}\n")
        else:
            head = (f"{ch.get('url','')}\n"
                    f"Catch: {', '.join(ch.get('kinds') or [])}"
                    f"{'  · kw: ' + ch['keyword'] if ch.get('keyword') else ''}\n")
        txt = (head +
               f"Downloaded: {stx['done']} total"
               f"   ·   last 7 days: {stx['last7']}\n"
               f"Last finished: {_ago(stx['last'])}"
               f"   ·   {stx['pending']} still queued\n"
               f"Seen (dedup): {len(ch.get('seen', []))} ids")
        self._show_tip(txt)

    def _show_tip(self, txt):
        self._hide_tip()
        try:
            t = tk.Toplevel(self.win)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            fr = tk.Frame(t, bg="#101010", bd=1, relief="solid")
            fr.pack(fill="both", expand=True)
            tk.Label(fr, text=txt, bg="#101010", fg="#e8e8e8",
                     font=("Segoe UI", 9), justify="left",
                     anchor="w").pack(padx=10, pady=8)
            t.update_idletasks()
            x = self.win.winfo_pointerx() + 14
            y = self.win.winfo_pointery() + 12
            t.geometry(f"+{x}+{y}")
            self._tip = t
        except Exception:
            self._tip = None

    def _hide_tip(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def _kinds(self):
        out = []
        if self.k_videos.get():
            out.append("Videos")
        if self.k_shorts.get():
            out.append("Shorts")
        if self.k_lives.get():
            out.append("Lives")
        return out

    def _form_data(self):
        cv = self.count_var.get().strip()
        return {
            "url": self.url_var.get().strip(), "kinds": self._kinds(),
            "keyword": self.kw_var.get().strip(),
            "count": int(cv) if cv.isdigit() else 15,
            "set_name": ("" if self.set_var.get() in ("—", "")
                         else self.set_var.get()),
            "transcript_mode": self.tmode_var.get(),
            "folder": self.folder_var.get().strip(),
            "save_video": self.sv.get(), "save_audio": self.sa.get(),
            "save_desc": self.sd.get(), "save_comments": self.sc.get(),
        }

    def _clear_form(self):
        self.url_var.set(""); self.kw_var.set(""); self.folder_var.set("")
        self.count_var.set("15"); self.set_var.set("—")
        self.k_videos.set(True); self.k_shorts.set(False); self.k_lives.set(False)
        self.tmode_var.set("Whisper (accurate)")
        for v in (self.sv, self.sa, self.sd, self.sc):
            v.set(False)

    def _add(self):
        data = self._form_data()
        if not data["url"]:
            self._set_status("Paste a channel URL/@handle first."); return
        if not data["kinds"]:
            self._set_status("Tick at least one of Videos/Shorts/Lives."); return
        if self._editing:
            watcher.update_channel(self._editing, data)
            self._cancel_edit()
            self._refresh()
            self._goto_list()
            self._set_status("Channel updated.")
            return
        watcher.add_channel(data)
        self._clear_form()
        self._refresh()
        self._goto_list()
        self._set_status("Channel added. Use 'Check now' to pull its latest.")

    def _edit_selected(self, _e=None):
        sel = self.lb.curselection()
        if not sel:
            return
        cid = self._chan_ids[sel[0]]
        d = watcher.load()
        ch = next((c for c in d["channels"] if c.get("id") == cid), None)
        if not ch:
            return
        if ch.get("kind") == "search":
            self._edit_search(ch)
            return
        if ch.get("kind") == "playlist":
            self._edit_playlist(ch)
            return
        try:
            self._nb.select(self._tab_add)
            self._subnb.select(self._tab_addch)
        except Exception:
            pass
        self._editing = cid
        self.url_var.set(ch.get("url", ""))
        self.kw_var.set(ch.get("keyword", ""))
        self.folder_var.set(ch.get("folder", ""))
        self.count_var.set(str(ch.get("count", 15)))
        self.set_var.set(ch.get("set_name") or "—")
        kinds = ch.get("kinds") or []
        self.k_videos.set("Videos" in kinds)
        self.k_shorts.set("Shorts" in kinds)
        self.k_lives.set("Lives" in kinds)
        self.tmode_var.set(ch.get("transcript_mode") or "Whisper (accurate)")
        self.sv.set(ch.get("save_video", False))
        self.sa.set(ch.get("save_audio", False))
        self.sd.set(ch.get("save_desc", False))
        self.sc.set(ch.get("save_comments", False))
        self.form_lbl.configure(text="Edit channel")
        self.add_btn.configure(text="✔ Save changes")
        self.cancel_edit_btn.pack(side="left", padx=8)
        self._set_status("Editing — change anything, then Save changes.")

    def _cancel_edit(self):
        self._editing = None
        self.form_lbl.configure(text="Add a channel")
        self.add_btn.configure(text="＋ Add channel")
        self.cancel_edit_btn.pack_forget()
        self._clear_form()

    # ---------- playlist sources ----------
    def _form_data_playlist(self):
        cv = self.pl_count.get().strip()
        return {
            "kind": "playlist",
            "url": self.pl_url.get().strip(),
            "keyword": self.pl_kw.get().strip(),
            "count": int(cv) if cv.isdigit() else 25,
            "set_name": ("" if self.pl_set.get() in ("—", "")
                         else self.pl_set.get()),
            "transcript_mode": self.pl_tmode.get(),
            "folder": self.pl_folder.get().strip(),
            "save_video": self.plv.get(), "save_audio": self.pla.get(),
            "save_desc": self.pld.get(), "save_comments": self.plc.get(),
        }

    def _clear_playlist_form(self):
        self.pl_url.set(""); self.pl_kw.set(""); self.pl_folder.set("")
        self.pl_count.set("25"); self.pl_set.set("—")
        self.pl_tmode.set("Whisper (accurate)")
        for v in (self.plv, self.pla, self.pld, self.plc):
            v.set(False)

    def _add_playlist(self):
        data = self._form_data_playlist()
        if not data["url"]:
            self._set_status("Paste a playlist URL first."); return
        if self._editing_playlist:
            watcher.update_channel(self._editing_playlist, data)
            self._cancel_playlist_edit()
            self._refresh()
            self._goto_list()
            self._set_status("Playlist updated.")
            return
        watcher.add_channel(data)
        self._clear_playlist_form()
        self._refresh()
        self._goto_list()
        self._set_status("Playlist added. Use 'Check now' to pull its videos.")

    def _edit_playlist(self, ch):
        try:
            self._nb.select(self._tab_add)
            self._subnb.select(self._tab_playlist)
        except Exception:
            pass
        self._editing_playlist = ch.get("id")
        self.pl_url.set(ch.get("url", ""))
        self.pl_kw.set(ch.get("keyword", ""))
        self.pl_folder.set(ch.get("folder", ""))
        self.pl_count.set(str(ch.get("count", 25)))
        self.pl_set.set(ch.get("set_name") or "—")
        self.pl_tmode.set(ch.get("transcript_mode") or "Whisper (accurate)")
        self.plv.set(ch.get("save_video", False))
        self.pla.set(ch.get("save_audio", False))
        self.pld.set(ch.get("save_desc", False))
        self.plc.set(ch.get("save_comments", False))
        self.pl_form_lbl.configure(text="Edit playlist")
        self.add_playlist_btn.configure(text="✔ Save changes")
        self.cancel_playlist_btn.pack(side="left", padx=8)
        self._set_status("Editing playlist — change anything, then Save changes.")

    def _cancel_playlist_edit(self):
        self._editing_playlist = None
        self.pl_form_lbl.configure(text="Add a playlist")
        self.add_playlist_btn.configure(text="＋ Add playlist")
        self.cancel_playlist_btn.pack_forget()
        self._clear_playlist_form()

    # ---------- search sources ----------
    def _search_types(self):
        out = []
        if self.st_video.get():
            out.append("Video")
        if self.st_shorts.get():
            out.append("Shorts")
        if self.st_live.get():
            out.append("Live")
        if self.st_playlist.get():
            out.append("Playlist")
        if self.st_channel.get():
            out.append("Channel")
        return out

    def _form_data_search(self):
        cv = self.s_count.get().strip()
        return {
            "kind": "search",
            "query": self.s_query.get().strip(),
            "types": self._search_types(),
            "upload_days": _UP_L2V.get(self.s_upload.get(), 0),
            "duration": self.s_dur.get(),
            "sort": self.s_sort.get(),
            "count": int(cv) if cv.isdigit() else 25,
            "lifespan_days": _LIFE_L2V.get(self.s_life.get(), 0),
            "set_name": ("" if self.s_set.get() in ("—", "")
                         else self.s_set.get()),
            "transcript_mode": self.s_tmode.get(),
            "folder": self.s_folder.get().strip(),
            "save_video": self.ssv.get(), "save_audio": self.ssa.get(),
            "save_desc": self.ssd.get(), "save_comments": self.ssc.get(),
        }

    def _clear_search_form(self):
        self.s_query.set(""); self.s_folder.set(""); self.s_count.set("25")
        self.s_set.set("—"); self.s_upload.set("Past 30 days")
        self.s_dur.set("Any"); self.s_sort.set("Upload date")
        self.s_life.set("30 days"); self.s_tmode.set("Whisper (accurate)")
        self.st_video.set(True); self.st_shorts.set(True); self.st_live.set(True)
        self.st_playlist.set(False); self.st_channel.set(False)
        for v in (self.ssv, self.ssa, self.ssd, self.ssc):
            v.set(False)

    def _add_search(self):
        data = self._form_data_search()
        if not data["query"]:
            self._set_status("Type a keyword to search for first."); return
        if not data["types"]:
            self._set_status("Tick at least one Type."); return
        if self._editing_search:
            import time as _t
            data["enabled"] = True          # editing un-retires it
            data["expired"] = False
            data["created_at"] = _t.time()  # restart the lifespan clock
            watcher.update_channel(self._editing_search, data)
            self._cancel_search_edit()
            self._refresh()
            self._goto_list()
            self._set_status("Search updated (lifespan restarted).")
            return
        import time as _t
        data["created_at"] = _t.time()
        data["enabled"] = True
        watcher.add_channel(data)
        self._clear_search_form()
        self._refresh()
        self._goto_list()
        self._set_status("Search added. Use 'Check now' to pull matches.")

    def _edit_search(self, ch):
        try:
            self._nb.select(self._tab_add)
            self._subnb.select(self._tab_search)
        except Exception:
            pass
        self._editing_search = ch.get("id")
        self.s_query.set(ch.get("query", ""))
        self.s_folder.set(ch.get("folder", ""))
        self.s_count.set(str(ch.get("count", 25)))
        self.s_set.set(ch.get("set_name") or "—")
        self.s_upload.set(_UP_V2L.get(int(ch.get("upload_days", 0) or 0),
                                      "Past 30 days"))
        self.s_dur.set(ch.get("duration") or "Any")
        self.s_sort.set(ch.get("sort") or "Upload date")
        self.s_life.set(_LIFE_V2L.get(int(ch.get("lifespan_days", 0) or 0),
                                      "No expiry"))
        self.s_tmode.set(ch.get("transcript_mode") or "Whisper (accurate)")
        types = ch.get("types") or []
        self.st_video.set("Video" in types)
        self.st_shorts.set("Shorts" in types)
        self.st_live.set("Live" in types)
        self.st_playlist.set("Playlist" in types)
        self.st_channel.set("Channel" in types)
        self.ssv.set(ch.get("save_video", False))
        self.ssa.set(ch.get("save_audio", False))
        self.ssd.set(ch.get("save_desc", False))
        self.ssc.set(ch.get("save_comments", False))
        self.s_form_lbl.configure(text="Edit search")
        self.add_search_btn.configure(text="✔ Save changes")
        self.cancel_search_btn.pack(side="left", padx=8)
        self._set_status("Editing search — change anything, then Save changes.")

    def _cancel_search_edit(self):
        self._editing_search = None
        self.s_form_lbl.configure(text="Search by keyword")
        self.add_search_btn.configure(text="＋ Add search")
        self.cancel_search_btn.pack_forget()
        self._clear_search_form()

    def _save_order(self, _label=None):
        from . import config as _c
        val = self._order_labels.get(self.order_var.get(), "fair")
        self.cfg["watch_order"] = val
        _c.save(self.cfg)
        self._set_status(f"Processing order: {self.order_var.get()}.")

    def _toggle_focus(self):
        sel = self.lb.curselection()
        if not sel:
            self._set_status("Select a source first."); return
        cid = self._chan_ids[sel[0]]
        d = watcher.load()
        ch = next((c for c in d["channels"] if c.get("id") == cid), None)
        if not ch:
            return
        new_on = not ch.get("focus")
        watcher.set_focus(cid, new_on)
        self._refresh()
        self._set_status("Focused — this source goes first." if new_on
                         else "Un-focused.")

    def _randomize(self):
        watcher.randomize_queue()
        self._refresh()
        self._set_status("Queue order shuffled.")

    def _toggle_pause(self):
        sel = self.lb.curselection()
        if not sel:
            self._set_status("Select a source first."); return
        cid = self._chan_ids[sel[0]]
        d = watcher.load()
        ch = next((c for c in d["channels"] if c.get("id") == cid), None)
        if not ch:
            return
        new_on = not ch.get("enabled", True)
        watcher.set_enabled(cid, new_on)
        self._refresh()
        self._set_status("Resumed." if new_on else "Paused.")

    def _clear_source(self):
        sel = self.lb.curselection()
        if not sel:
            self._set_status("Select a source first."); return
        cid = self._chan_ids[sel[0]]
        name = self.lb.get(sel[0]).strip()
        if not messagebox.askyesno(
                "Wipe results",
                f"Clear all queued/finished items and the seen-list for:\n\n"
                f"{name}\n\nThe source stays and will re-pull fresh on the "
                "next check. Files already saved to disk are NOT deleted.",
                parent=self.win):
            return
        watcher.clear_source_queue(cid)
        self._refresh()
        self._set_status("Wiped this source's queue + seen list.")

    def _delete(self):
        sel = self.lb.curselection()
        if not sel:
            self._set_status("Select a channel to delete."); return
        cid = self._chan_ids[sel[0]]
        name = self.lb.get(sel[0]).strip()
        if not messagebox.askyesno(
                "Delete watch",
                f"Delete this watch and ALL stored data for it?\n\n{name}\n\n"
                "This removes its seen-video list and any queued items. "
                "This can't be undone.", parent=self.win):
            return
        watcher.delete_channel(cid)
        if self._editing == cid:
            self._cancel_edit()
        self._refresh()
        self._set_status("Deleted the watch and its data.")

    def _stop(self):
        self._cancel = True
        self._set_status("Stopping after the current video…")
        self._log("Stop requested — finishing the current video, then halting.")

    def _clear_queue(self):
        if not messagebox.askyesno(
                "Clear queue",
                "Delete every queued item?\n\nThis keeps your watched "
                "channels but empties everything waiting to be processed. "
                "This can't be undone.", parent=self.win):
            return
        watcher.clear_queue()
        self._refresh()
        self._set_status("Queue cleared.")

    def _toggle_proxy(self):
        from . import config as _c
        self.cfg["di_enabled"] = bool(self.proxy_on.get())
        _c.save(self.cfg)
        self._set_status("Proxy ON for downloads." if self.proxy_on.get()
                         else "Proxy OFF for downloads.")

    def _test_notify(self):
        notify.send("EchoQuill - test",
                    "If you can see this toast, notifications work. 🎉")
        self._set_status("Sent a test notification.")

    def _tick_monitor(self):
        try:
            a = watcher.get_activity()
            ph = a.get("phase", "idle")
            if ph == "idle":
                self.monitor.configure(text="●  Idle — nothing running",
                                       fg="#8a8a8a")
            elif ph == "waiting":
                rem = max(0, int(a.get("wait_until", 0) - time.time()))
                m, sec = divmod(rem, 60)
                self.monitor.configure(
                    text=f"⏳  Waiting {m}m {sec:02d}s before video "
                         f"{a.get('i',0)}/{a.get('n',0)}  ·  next: "
                         f"{a.get('title') or ''}",
                    fg="#e0a030")
            else:
                t = a.get("title") or ""
                fld = a.get("folder") or ""
                self.monitor.configure(
                    text=f"▶  Video {a.get('i',0)}/{a.get('n',0)}  ·  {ph}"
                         f"{'  ·  ' + t if t else ''}"
                         f"{chr(10) + '     folder: ' + fld if fld else ''}",
                    fg="#4da3ff")
        except Exception:
            pass
        # live-refresh the list when a video finishes (done/pending/failed change)
        try:
            c = watcher.counts()
            sig = (c["done"], c["pending"], c["failed"], c["unavailable"])
            if sig != getattr(self, "_last_sig", None):
                self._last_sig = sig
                self._refresh()
        except Exception:
            pass
        try:
            self.win.after(500, self._tick_monitor)
        except Exception:
            pass

    def _check_now(self):
        self._cancel = False
        self._set_status("Checking channels…")
        self._log("Checking for new uploads…")

        def run():
            done = watcher.run_once(self.cfg, cancel=lambda: self._cancel)
            self.win.after(0, self._refresh)
            if done:
                notify.send(
                    "EchoQuill - new results",
                    f"{done} new video(s) transcribed - check your "
                    "Transcriptions folder.")
            self._set_status(f"Check done — {done} newly finished."
                             if done else "Check done — nothing new to finish.")
        threading.Thread(target=run, daemon=True).start()
