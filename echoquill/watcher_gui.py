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

        # ---- tabs ----
        nb = ttk.Notebook(self.win)
        nb.pack(fill="x", padx=12, pady=(6, 4))
        tab_ch = ttk.Frame(nb)
        tab_sch = ttk.Frame(nb)
        tab_prx = ttk.Frame(nb)
        nb.add(tab_ch, text="  Channels  ")
        nb.add(tab_sch, text="  Schedule & pacing  ")
        nb.add(tab_prx, text="  Proxy  ")

        self._build_channels_tab(tab_ch)
        self._build_schedule_tab(tab_sch)
        self._build_proxy_tab(tab_prx)

        # ---- constant: monitor ----
        mon = tk.Frame(self.win, bg="#141414", bd=1, relief="solid")
        mon.pack(fill="x", padx=16, pady=(2, 2))
        self.monitor = tk.Label(mon, bg="#141414", fg="#4da3ff",
                                font=("Segoe UI", 12, "bold"), anchor="w",
                                justify="left", wraplength=740,
                                text="●  Idle — nothing running")
        self.monitor.pack(fill="x", padx=12, pady=8)
        mon.bind("<Configure>", lambda e: self.monitor.configure(
            wraplength=max(220, e.width - 28)))

        # ---- constant: run controls ----
        brow = ttk.Frame(self.win); brow.pack(fill="x", padx=16, pady=(2, 2))
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

        self.status = ttk.Label(self.win, style="Dim.TLabel", text="")
        self.status.pack(anchor="w", padx=16)

        # ---- constant: log ----
        self.log = theme.dark_text(self.win, wrap="word", height=14)
        self.log.pack(fill="both", expand=True, padx=16, pady=(2, 12))

        notify.badge(False)   # opening the watcher clears the 'new results' dot
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
    def _build_channels_tab(self, f):
        ttk.Label(f, style="Dim.TLabel", wraplength=720, text=(
            "Double-click a channel to edit it · hover for stats.")).pack(
            anchor="w", padx=8, pady=(8, 2))
        lbf = ttk.Frame(f); lbf.pack(fill="x", padx=8, pady=(0, 4))
        self.lb = theme.dark_listbox(lbf, height=8)
        _lbsb = ttk.Scrollbar(lbf, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=_lbsb.set)
        _lbsb.pack(side="right", fill="y")
        self.lb.pack(side="left", fill="both", expand=True)
        self.lb.bind("<Double-Button-1>", self._edit_selected)
        self.lb.bind("<Motion>", self._hover)
        self.lb.bind("<Leave>", lambda _e: self._hide_tip())
        lrow = ttk.Frame(f); lrow.pack(fill="x", padx=8, pady=(0, 6))
        _bd = ttk.Button(lrow, text="Delete selected (and its data)",
                         command=self._delete)
        _bd.pack(side="left")
        helptip.tip(_bd, "Removes the watch AND everything stored for it "
                    "(seen list + queued items). Asks first.")

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
                    "lower on a residential/mobile proxy.")

        ttk.Button(f, text="Save schedule", style="Accent.TButton",
                   command=self._save_sched).pack(anchor="w", padx=10, pady=(10, 4))

    def _build_proxy_tab(self, f):
        self.proxy_on = tk.BooleanVar(value=bool(self.cfg.get("di_enabled")))
        _pc = ttk.Checkbutton(f, text="Run downloads through the proxy "
                              "(residential/mobile IP)", variable=self.proxy_on,
                              command=self._toggle_proxy)
        _pc.pack(anchor="w", padx=10, pady=(12, 2))
        ttk.Label(f, style="Dim.TLabel", wraplength=720, text=(
            "Saves instantly the moment you tick it — it is NOT part of the "
            "Save schedule button. Set up the proxy itself (login, password, "
            "location) in Settings ▸ Transcription, and verify it there first."
        )).pack(anchor="w", padx=28, pady=(0, 10))
        ttk.Label(f, style="Dim.TLabel", wraplength=720, text=(
            "Using a residential/mobile IP makes YouTube far less likely to "
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
            kinds = ", ".join(ch.get("kinds") or [])
            kw = f" · kw:{ch['keyword']}" if ch.get("keyword") else ""
            st = ch.get("set_name") or "no set"
            stx = watcher.stats(ch.get("id"))
            tail = " · ✓" + str(stx["done"])
            if stx["last7"]:
                tail += " · 7d:" + str(stx["last7"])
            if stx["done"]:
                tail += " · last " + _ago(stx["last"])
            if stx["pending"]:
                tail += " · " + str(stx["pending"]) + " queued"
            url = ch.get("url", "")
            self.lb.insert("end",
                           "  " + url + "   [" + kinds + "]" + kw
                           + " · " + st + tail)
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
        txt = (f"{ch.get('url','')}\n"
               f"Catch: {', '.join(ch.get('kinds') or [])}"
               f"{'  · kw: ' + ch['keyword'] if ch.get('keyword') else ''}\n"
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
            self._set_status("Channel updated.")
            return
        watcher.add_channel(data)
        self._clear_form()
        self._refresh()
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
                self.monitor.configure(
                    text=f"▶  Video {a.get('i',0)}/{a.get('n',0)}  ·  {ph}"
                         f"{'  ·  ' + t if t else ''}",
                    fg="#4da3ff")
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
            done = watcher.run_once(self.cfg, self._log,
                                    cancel=lambda: self._cancel)
            self.win.after(0, self._refresh)
            if done:
                notify.send(
                    "EchoQuill - new results",
                    f"{done} new video(s) transcribed - check your "
                    "Transcriptions folder.")
            self._set_status(f"Check done — {done} newly finished."
                             if done else "Check done — nothing new to finish.")
        threading.Thread(target=run, daemon=True).start()
