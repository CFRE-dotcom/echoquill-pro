"""Channel watcher window: add/list/delete watched channels, run a check now,
and see queue status."""

import threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import theme, helptip, watcher, notify
from . import prompts as _pr


class WatcherWindow:
    def __init__(self, parent, cfg):
        self.cfg = cfg
        self._chan_ids = []
        self._cancel = False

        self.win = tk.Toplevel(parent)
        self.win.title("EchoQuill — Channel watcher")
        self.win.geometry("720x740")
        self.win.minsize(600, 600)
        theme.apply(self.win)

        top = ttk.Frame(self.win)
        top.pack(fill="x", padx=16, pady=(12, 2))
        ttk.Label(top, text="Channel watcher", style="Title.TLabel").pack(
            side="left")
        ttk.Label(self.win, style="Dim.TLabel", wraplength=680, text=(
            "Watch channels for new uploads and auto-run them through the "
            "pipeline. New videos are queued and retried until they succeed; "
            "you get a notification when new results land.")).pack(
            anchor="w", padx=16)

        # -------- add-a-channel form --------
        ttk.Label(self.win, text="Add a channel", style="Section.TLabel").pack(
            anchor="w", padx=16, pady=(10, 2))
        r = ttk.Frame(self.win); r.pack(fill="x", padx=16, pady=1)
        ttk.Label(r, text="Channel URL / @handle:").pack(side="left")
        self.url_var = tk.StringVar()
        tk.Entry(r, textvariable=self.url_var, bg=theme.FIELD, fg=theme.FG,
                 insertbackground=theme.FG, relief="solid", borderwidth=1).pack(
                 side="left", fill="x", expand=True, padx=(6, 0))

        r = ttk.Frame(self.win); r.pack(fill="x", padx=16, pady=1)
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

        r = ttk.Frame(self.win); r.pack(fill="x", padx=16, pady=1)
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

        r = ttk.Frame(self.win); r.pack(fill="x", padx=16, pady=1)
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

        ttk.Button(self.win, text="＋ Add channel", style="Accent.TButton",
                   command=self._add).pack(anchor="w", padx=16, pady=(4, 6))

        sch = ttk.Frame(self.win); sch.pack(fill="x", padx=16, pady=(0, 4))
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

        sch2 = ttk.Frame(self.win); sch2.pack(fill="x", padx=16, pady=(0, 4))
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
                    "flag the batch. 600 (10 min) is a safe default; you can "
                    "go lower when running on a residential/mobile proxy.")

        prow = ttk.Frame(self.win); prow.pack(fill="x", padx=16, pady=(0, 4))
        self.proxy_on = tk.BooleanVar(value=bool(self.cfg.get("di_enabled")))
        _pc = ttk.Checkbutton(prow, text="Run downloads through the proxy "
                              "(residential/mobile IP)", variable=self.proxy_on,
                              command=self._toggle_proxy)
        _pc.pack(side="left")
        helptip.tip(_pc, "Uses your DataImpulse proxy (set it up in Settings ▸ "
                    "Transcription). Verify it works there first. Lets you "
                    "safely shorten the wait between videos.")

        # -------- watched list --------
        ttk.Label(self.win, text="Watched channels", style="Section.TLabel").pack(
            anchor="w", padx=16, pady=(4, 2))
        self.lb = theme.dark_listbox(self.win, height=7)
        self.lb.pack(fill="both", expand=True, padx=16, pady=(0, 4))
        lrow = ttk.Frame(self.win); lrow.pack(fill="x", padx=16, pady=(0, 6))
        _bd = ttk.Button(lrow, text="Delete selected (and its data)",
                         command=self._delete)
        _bd.pack(side="left")
        helptip.tip(_bd, "Removes the watch AND everything stored for it "
                    "(seen list + queued items). Asks first.")

        # -------- run + status --------
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
        ttk.Button(brow, text="Close", command=self.win.destroy).pack(side="right")
        self.status = ttk.Label(self.win, style="Dim.TLabel", text="")
        self.status.pack(anchor="w", padx=16)
        self.log = theme.dark_text(self.win, wrap="word", height=6)
        self.log.pack(fill="both", expand=True, padx=16, pady=(2, 12))

        # Show reliably: parent (root) is withdrawn, so DON'T make this
        # transient to it (that leaves the window created-but-hidden on
        # Windows). Match the other windows: force topmost, then release.
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.focus_force()
        self.win.after(400, lambda: self._drop_topmost())
        self._refresh()

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
            self._set_status("Enter whole numbers for hours/minutes."); return
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
            self.lb.insert("end", f"  {ch.get('url','')}   [{kinds}]{kw} · {st}")
            self._chan_ids.append(ch.get("id"))
        c = watcher.counts()
        self.status.configure(text=(
            f"{c['channels']} channels · queue: {c['done']} done · "
            f"{c['pending']} pending · {c['failed']} retrying · "
            f"{c['unavailable']} unavailable"))

    def _kinds(self):
        out = []
        if self.k_videos.get():
            out.append("Videos")
        if self.k_shorts.get():
            out.append("Shorts")
        if self.k_lives.get():
            out.append("Lives")
        return out

    def _add(self):
        url = self.url_var.get().strip()
        if not url:
            self._set_status("Paste a channel URL/@handle first."); return
        kinds = self._kinds()
        if not kinds:
            self._set_status("Tick at least one of Videos/Shorts/Lives."); return
        cv = self.count_var.get().strip()
        watcher.add_channel({
            "url": url, "kinds": kinds,
            "keyword": self.kw_var.get().strip(),
            "count": int(cv) if cv.isdigit() else 15,
            "set_name": ("" if self.set_var.get() in ("—", "")
                         else self.set_var.get()),
            "transcript_mode": self.tmode_var.get(),
            "folder": self.folder_var.get().strip(),
            "save_video": self.sv.get(), "save_audio": self.sa.get(),
            "save_desc": self.sd.get(), "save_comments": self.sc.get(),
        })
        self.url_var.set(""); self.kw_var.set(""); self.folder_var.set("")
        self._refresh()
        self._set_status("Channel added. Use 'Check now' to pull its latest.")

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
