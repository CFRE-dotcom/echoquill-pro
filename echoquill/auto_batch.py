"""Pro: Auto-batch — paste a block of lines, one per video:

    URL | Title | folder\\subfolder

For each video in turn this downloads and SAVES the video and the audio,
transcribes and saves the transcript, then runs a saved question SET against
that video's own transcript and saves the answers as a Q&A file. Every output
for a line lands in that line's folder, which is created as deep as you type
(each backslash is another nested level). Title and folder are optional.
"""

import os
import re
import shutil
import threading
import tkinter as tk
from tkinter import ttk

from . import theme, helptip, ask_ai
from . import prompts as _pr


HELP = (
    "One line per video:\n"
    "    URL | Title | folder\\subfolder\n\n"
    "- Title and folder are optional (URL is all you need).\n"
    "- Blank title = use the video's own title.\n"
    "- Folder: a full path (C:\\...), or just a name to nest under your\n"
    "  Transcriptions folder. Each backslash makes a deeper folder, created\n"
    "  automatically if it doesn't exist.\n"
    "- Blank lines and lines starting with # are ignored.\n\n"
    "For every video the saved video, audio, transcript and Q&A all go into\n"
    "that line's folder. Your chosen question set then runs against the\n"
    "transcript and the answers save as '<name> - Q&A.txt'.\n\n"
    "Everything runs on your PC; each video's answers come only from that\n"
    "video's own transcript."
)


def _segments(raw):
    return [p.strip() for p in re.split(r"[\\/]+", raw.strip()) if p.strip()]


def resolve_folder(cfg, raw):
    """Return an existing directory for this line's folder field (created)."""
    from .media_gui import transcripts_dir
    raw = (raw or "").strip().strip('"')
    if not raw:
        return transcripts_dir(cfg)
    m = re.match(r"^([A-Za-z]:[\\/]|\\\\|/)", raw)
    if m:                                   # absolute path
        root = m.group(1)
        rest = _segments(raw[len(root):])
        d = root + os.sep.join(rest) if rest else root
    else:                                   # relative -> nest under Transcriptions
        d = os.path.join(transcripts_dir(cfg), *_segments(raw))
    os.makedirs(d, exist_ok=True)
    return d


def parse_lines(text):
    """Return [{url, title, folder}, ...] for each usable line."""
    rows = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in s.split("|")]
        url = parts[0] if parts else ""
        if not url:
            continue
        rows.append({
            "url": url,
            "title": parts[1] if len(parts) > 1 else "",
            "folder": parts[2] if len(parts) > 2 else "",
        })
    return rows


class AutoBatchWindow:
    def __init__(self, parent, cfg):
        self.cfg = cfg
        self._cancel = False
        self._busy = False
        self._eng = None

        self.win = tk.Toplevel(parent)
        self.win.title("EchoQuill — Auto-batch + Ask AI")
        self.win.geometry("740x660")
        theme.apply(self.win)

        top = ttk.Frame(self.win)
        top.pack(fill="x", padx=18, pady=(14, 2))
        ttk.Label(top, text="Auto-batch — transcribe + Ask AI",
                  style="Title.TLabel").pack(side="left")
        helptip.attach(self.win, top, "Auto-batch - help", HELP).pack(
            side="left", padx=8)

        ttk.Label(self.win, style="Dim.TLabel", wraplength=700, text=(
            "One line per video:   URL | Title | folder\\subfolder      "
            "(Title and folder optional; blank title uses the video's own "
            "title).")).pack(anchor="w", padx=18)

        self.box = theme.dark_text(self.win, wrap="none", height=11)
        self.box.pack(fill="both", expand=True, padx=18, pady=(6, 2))
        self.box.bind("<KeyRelease>", lambda e: self._recount())
        helptip.tip(self.box, "Paste your list here, one video per line: "
                    "URL | Title | folder. See the ? above for the full format.")

        self.count = ttk.Label(self.win, text="0 videos", style="Dim.TLabel")
        self.count.pack(anchor="w", padx=18)

        opt = ttk.Frame(self.win)
        opt.pack(fill="x", padx=18, pady=(6, 2))
        ttk.Label(opt, text="Question set:").pack(side="left")
        self.setvar = tk.StringVar(value="—")
        self.setmenu = ttk.OptionMenu(opt, self.setvar, "—")
        self.setmenu.configure(width=24)
        self.setmenu.pack(side="left", padx=(6, 12))
        self._refresh_sets()
        helptip.tip(self.setmenu, "The saved set of questions to ask every "
                    "video. Create sets in Ask AI → 'Ask several' → 'Save "
                    "checked as set'.")
        self.save_video = tk.BooleanVar(value=True)
        self.save_audio = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Save video",
                        variable=self.save_video).pack(side="left")
        ttk.Checkbutton(opt, text="Save audio",
                        variable=self.save_audio).pack(side="left", padx=(10, 0))

        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=18, pady=(4, 2))
        self.start_btn = ttk.Button(bar, text="Start", style="Accent.TButton",
                                    command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Stop", command=self._stop,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(bar, text="Close", command=self.win.destroy).pack(side="right")

        self.status = ttk.Label(self.win, text="", style="Dim.TLabel")
        self.status.pack(anchor="w", padx=18)
        ttk.Label(self.win, text="Progress", style="Section.TLabel").pack(
            anchor="w", padx=18, pady=(4, 0))
        self.log = theme.dark_text(self.win, wrap="word", height=8)
        self.log.pack(fill="both", expand=True, padx=18, pady=(2, 12))

    # ---------- helpers ----------
    def _refresh_sets(self):
        m = self.setmenu["menu"]
        m.delete(0, "end")
        m.add_command(label="—", command=lambda: self.setvar.set("—"))
        for n in _pr.set_names(self.cfg):
            m.add_command(label=n, command=lambda n=n: self.setvar.set(n))

    def _recount(self):
        n = len(parse_lines(self.box.get("1.0", "end")))
        self.count.configure(text=f"{n} video{'s' if n != 1 else ''}")

    def _set(self, msg):
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

    def _stop(self):
        self._cancel = True
        self._set("Stopping after this step…")

    # ---------- run ----------
    def _start(self):
        if self._busy:
            return
        rows = parse_lines(self.box.get("1.0", "end"))
        if not rows:
            self._set("Paste at least one URL line first.")
            return
        name = self.setvar.get()
        questions = _pr.get_set(self.cfg, name) if name not in ("—", "") else []
        if not questions:
            self._set("Pick a question set first (make one in Ask AI → "
                      "'Ask several' → 'Save checked as set').")
            return
        from . import license as _lic
        if not _lic.is_pro(self.cfg):
            self._set("Auto-batch is a Pro feature.")
            return
        self._cancel = False
        self._busy = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        threading.Thread(target=self._worker, args=(rows, questions),
                         daemon=True).start()

    def _worker(self, rows, questions):
        from .media_gui import (download_video, fetch_audio_info, safe_filename,
                                _safe_stem, _unique_path, _keep_awake)
        from .transcriber import Transcriber
        total = len(rows)
        done = 0
        if self._eng is None:
            self._eng = Transcriber(self.cfg.get("model", "base"))
        _keep_awake(True)
        try:
            for i, row in enumerate(rows, 1):
                if self._cancel:
                    break
                url, ttl, fld = row["url"], row["title"], row["folder"]
                tmpdir = None
                try:
                    dest = resolve_folder(self.cfg, fld)
                    self._log(f"[{i}/{total}] {url}")
                    self._log(f"    folder: {dest}")

                    # 1) full video file
                    real_title = ttl
                    if self.save_video.get():
                        self._set(f"Video {i}/{total}: downloading video…")
                        try:
                            rt = download_video(url, self.cfg, dest, self._set,
                                                name=(ttl or None))
                            real_title = ttl or rt
                            self._log("    video saved ✓")
                        except Exception as e:
                            self._log(f"    video save failed: {e}")

                    # 2) audio (also used to transcribe)
                    self._set(f"Video {i}/{total}: downloading audio…")
                    apath, atitle = fetch_audio_info(url, self._set, self.cfg)
                    tmpdir = os.path.dirname(apath)
                    name = (ttl or real_title or atitle or "video").strip()
                    if self.save_audio.get() and apath and os.path.exists(apath):
                        try:
                            ext = os.path.splitext(apath)[1] or ".m4a"
                            adst = _unique_path(os.path.join(
                                dest, _safe_stem(name) + ext))
                            shutil.copy2(apath, adst)
                            self._log(f"    audio saved: {os.path.basename(adst)} ✓")
                        except Exception as e:
                            self._log(f"    audio save failed: {e}")

                    # 3) transcribe + save transcript
                    if self._cancel:
                        break
                    self._set(f"Video {i}/{total}: transcribing…")
                    model = self._eng.load()
                    lang = self.cfg.get("language", "auto")
                    lang = None if lang in ("", "auto") else lang
                    segs, parts = [], []
                    with self._eng._lock:
                        segments, _info = model.transcribe(
                            apath, language=lang, vad_filter=True)
                        for seg in segments:
                            if self._cancel:
                                break
                            t = seg.text.strip()
                            segs.append((seg.start, t))
                            parts.append(t)
                    text = " ".join(parts).strip()
                    tpath = _unique_path(os.path.join(dest, safe_filename(name)))
                    with open(tpath, "w", encoding="utf-8") as f:
                        f.write(f"{name}\n{url}\n\n{text}")
                    self._log(f"    transcript saved: {os.path.basename(tpath)} ✓")

                    # 4) run the question set, grounded in THIS transcript
                    if self._cancel:
                        break
                    qa = []
                    for qi, q in enumerate(questions, 1):
                        if self._cancel:
                            break
                        self._set(f"Video {i}/{total}: question "
                                  f"{qi}/{len(questions)}…")
                        ans = ask_ai.ask(q, segs, self.cfg, title=name, url=url)
                        qa.append(f"{'*' * 50}\n{'*' * 50}\nQ: {q}\n\nA: {ans}\n")
                    if qa:
                        qpath = _unique_path(os.path.join(
                            dest, safe_filename(name + " - Q&A")))
                        with open(qpath, "w", encoding="utf-8") as f:
                            f.write(f"{name}\n{url}\n\n" + "\n".join(qa))
                        self._log(f"    Q&A saved: {os.path.basename(qpath)} ✓")
                    done += 1
                    self._log(f"    [{i}/{total}] done ✓")
                except Exception as e:
                    self._log(f"    ERROR on this video: {e}")
                finally:
                    if tmpdir:
                        shutil.rmtree(tmpdir, ignore_errors=True)
        finally:
            _keep_awake(False)
        msg = (f"Stopped — {done}/{total} finished."
               if self._cancel else
               f"Done ✓ — {done}/{total} videos processed.")
        self._set(msg)
        self._log(msg)
        self._busy = False
        try:
            self.win.after(0, lambda: (self.start_btn.configure(state="normal"),
                                       self.stop_btn.configure(state="disabled")))
        except Exception:
            pass
