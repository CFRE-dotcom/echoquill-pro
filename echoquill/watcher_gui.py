"""Channel watcher window: add/edit/delete watched channels, run a check now,
see queue status, and hover a channel for its download stats."""

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


class WatcherWindow:
    def __init__(self, parent, cfg):
        self.cfg = cfg
        self._chan_ids = []
        self._cancel = False
        self._editing = None          # channel id being edited, or None
        self._tip = None

        self.win = tk.Toplevel(parent)
        self.win.title("EchoQuill — Channel watcher")
        self.win.geometry("760x760")
        self.win.minsize(620, 560)
        theme.apply(self.win)

        # whole window scrolls
        sc = theme.Scrollable(self.win)
        sc.pack(fill="both", expand=True)
        body = sc.inner

        top = ttk.Frame(body)
        top.pack(fill="x", padx=16, pady=(12, 2))
        ttk.Label(top, text="Channel watcher", style="Title.TLabel").pack(
            side="left")
        ttk.Label(body, style="Dim.TLabel", wraplength=680, text=(
            "Watch channels for new uploads and auto-run them through the "
            "pipeline. New videos are queued and retried until they succeed; "
            "you get a toast when new results land. Double-click a channel to "
            "edit it; hover it to see its stats.")).pack(anchor="w", padx=16)

        # -------- add / edit form --------
        self.form_lbl = ttk.Label(body, text="Add a channel",
                                  style="Section.TLabel")
        self.form_lbl.pack(anchor="w", padx=16, pady=(10, 2))

        r = ttk.Frame(body); r.pack(fill="x", padx=16, pady=1)
        ttk.Label(r, text="Channel URL / @handle:").pack(side="left")
        self.url_var = tk.StringVar()
        tk.Entry(r, textvariable=self.url_var, bg=theme.FIELD, fg=theme.FG,
                 insertbackground=theme.FG, relief="solid", borderwidth=1).pack(
                 side="left", fill="x", expand=True, padx=(6, 0))

        r = ttk.Frame(body); r.pack(fill="x", padx=16, pady=1)
        ttk.Label(r, text="Catch:").pack(side="left")
        self.k_videos = tk.BooleanVar(value=True)
        self.k_shorts = tk.BooleanVar(value=False)
        self.k_lives = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="Videos", variable=self.k_videos).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Shorts", variable=self.k_shorts).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(r, text="Lives", variable=self.k_lives).pack(side="left", padx=(8, 12))
        ttk.Label(r, text="Keyword:").pack(side="left")
        self.kw_var = tk.StringVar()
        tk.Entry(r, textvariable=self.kw_var, width=16, bg=theme.FIELD,
                 fg=theme.FG, insertbackground=theme.FG, relief="solid",
                 borderwidth=1).pack(side="left", padx=(4, 12))
        ttk.Label(r, text="Newest:").pack(side="left")
        self.count_var = tk.StringVar(value="15")
        tk.Entry(r, textvariable=self.count_var, width=5, bg=theme.FIELD,
                 fg=theme.FG, insertbackground=theme.FG, relief="solid",
                 borderwidth=1).pack(side="left", padx=(4, 0))

        r = ttk.Frame(body); r.pack(fill="x", padx=16, pady=1)
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

        r = ttk.Frame(body); r.pack(fill="x", padx=16, pady=1)
        ttk.Label(r, text="Folder:").pack(side="left")
        self.folder_var = tk.StringVar()
        tk.Entry(r, textvariable=self.folder_var, width=22, bg=theme.FIELD,
                 fg=theme.FG, insertbackground=theme.FG, relief="solid",
                 borderwidth=1).pack(side="left", padx=(6, 12))
        self.sv = tk.BooleanVar(value=False)
        self.sa = tk.BooleanVar(value=False)
        self.sd = tk.BooleanVar(value=False)
        self.sc = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="Video", variable=self.sv).pack(side="left")
        ttk.Checkbutton(r, text="Audio", variable=self.sa).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Desc", variable=self.sd).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r, text="Comments", variable=self.sc).pack(side="left", padx=(6, 0))

        arow = ttk.Frame(body); arow.pack(anchor="w", padx=16, pady=(4, 6))
        self.add_btn = ttk.Button(arow, text="＋ Add channel",
                                  style="Accent.TButton", command=self._add)
        self.add_btn.pack(side="left")
        self.cancel_edit_btn = ttk.Button(arow, text="Cancel edit",
                                          command=self._cancel_edit)
        # shown only while editing

        # -------- schedule --------
        sch = ttk.Frame(body); sch.pack(fill="x", padx=16, pady=(0, 4))
        ttk.Label(sch, text="Check for new every").pack(side="left")
        self.chk_hours = tk.StringVar(
            value=str(self.cfg.get("watch_check_hours", 6)))
        tk.Entry(sch, textvariable=self.chk_hours, width=4, bg=theme.FIELD,
                 fg=theme.FG, insertbackground=theme.FG, relief="solid",
                 borderwidth=1).pack(side="left", padx=4)
        ttk.Label(sch, text="hours   ·   retry failed every").pack(side="left")
        self.retry_min = tk.StringVar(
            value=str(self.cfg.get("watch_retry_minutes", 30)))
        tk.Entry(sch, textvariable=self.retry_min, width=5, bg=theme.FIELD,
                 fg=theme.FG, insertbackground=theme.FG, relief="solid",
                 borderwidth=1).pack(side="left", padx=4)
        ttk.Label(sch, text="minutes").pack(side="left")
        ttk.Button(sch, text="Save schedule",
                   command=self._save_sched).pack(side="left", padx=10)

        sch2 = ttk.Frame(body); sch2.pack(fill="x", padx=16, pady=(0, 4))
        ttk.Label(sch2, text="When several are queued, do at most").pack(side="left")
        self.per_cycle = tk.StringVar(
            value=str(self.cfg.get("watch_per_cycle", 5)))
        tk.Entry(sch2, textvariable=self.per_cycle, width=4, bg=theme.FIELD,
                 fg=theme.FG, insertbackground=theme.FG, relief="solid",
                 borderwidth=1).pack(side="left", padx=4)
        ttk.Label(sch2, text="per cycle · wait").pack(side="left")
        self.gap_sec = tk.StringVar(
            value=str(self.cfg.get("watch_gap_seconds", 600)))
        _ge = tk.Entry(sch2, textvariable=self.gap_sec, width=6, bg=theme.FIELD,
                       fg=theme.FG, insertbackground=theme.FG, relief="solid",
                       borderwidth=1)
        _ge.pack(side="left", padx=4)
        ttk.Label(sch2, text="sec between each").pack(side="left")
        helptip.tip(_ge, "Seconds to pause between videos so YouTube does not "
                    "flag the batch. 600 (10 min) is a safe default; you can go "
                    "lower when running on a residential/mobile proxy.")

        prow = ttk.Frame(body); prow.pack(fill="x", padx=16, pady=(0, 4))
        self.proxy_on = tk.BooleanVar(value=bool(self.cfg.get("di_enabled")))
        _pc = ttk.Checkbutton(prow, text="Run downloads through the proxy "
                              "(residential/mobile IP)", variable=self.proxy_on,
                              command=self._toggle_proxy)
        _pc.pack(side="left")
        ttk.Label(prow, style="Dim.TLabel",
                  text="  (saves instantly — not part of Save schedule)").pack(
                  side="left")
        helptip.tip(_pc, "Uses your DataImpulse proxy (set it up in Settings ▸ "
                    "Transcription). Verify it works there first. Lets you "
                    "safely shorten the wait between videos.")

        # -------- watched list --------
        ttk.Label(body, text="Watched channels", style="Section.TLabel").pack(
            anchor="w", padx=16, pady=(4, 2))
        self.lb = theme.dark_listbox(body, height=7)
        self.lb.pack(fill="both", expand=True, padx=16, pady=(0, 4))
        self.lb.bind("<Double-Button-1>", self._edit_selected)
        self.lb.bind("<Motion>", self._hover)
        self.lb.bind("<Leave>", lambda _e: self._hide_tip())
        lrow = ttk.Frame(body); lrow.pack(fill="x", padx=16, pady=(0, 6))
        _bd = ttk.Button(lrow, text="Delete selected (and its data)",
                         command=self._delete)
        _bd.pack(side="left")
        helptip.tip(_bd, "Removes the watch AND everything stored for it "
                    "(seen list + queued items). Asks first.")
        ttk.Label(lrow, style="Dim.TLabel",
                  text="  Double-click a channel to edit · hover for stats").pack(
                  side="left", padx=8)

        # -------- run + status --------
        brow = ttk.Frame(body); brow.pack(fill="x", padx=16, pady=(2, 2))
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
        ttk.Button(brow, text="Close", command=self.win.destroy).pack(side="right")

        mon = tk.Frame(body, bg="#141414", bd=1, relief="solid")
        mon.pack(fill="x", padx=16, pady=(6, 2))
        self.monitor = tk.Label(mon, bg="#141414", fg="#4da3ff",
                                font=("Segoe UI", 12, "bold"), anchor="w",
                                justify="left", wraplength=680,
                                text="●  Idle — nothing running")
        self.monitor.pack(fill="x", padx=12, pady=8)
        mon.bind("<Configure>", lambda e: self.monitor.configure(
            wraplength=max(200, e.width - 28)))
        self.status = ttk.Label(body, style="Dim.TLabel", text="")
        self.status.pack(anchor="w", padx=16)
        self.log = theme.dark_text(body, wrap="word", height=6)
        self.log.pack(fill="both", expand=True, padx=16, pady=(2, 12))

        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.focus_force()
        self.win.after(400, self._drop_topmost)
        self._refresh()
        self._tick_monitor()

    # ---------- helpers ----------
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
            f = tk.Frame(t, bg="#101010", bd=1, relief="solid")
            f.pack(fill="both", expand=True)
            tk.Label(f, text=txt, bg="#101010", fg="#e8e8e8",
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
                         f"{(a.get('title') or '')[:46]}",
                    fg="#e0a030")
            else:
                t = (a.get("title") or "")[:46]
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
