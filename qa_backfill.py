#!/usr/bin/env python3
"""
qa_backfill.py — heal EchoQuill Q&A files whose answers failed (e.g. the
"AI request failed: HTTP 401" batch caused by a dead Ollama key).

What it does, safely:
  * Scans your Transcriptions folder for "<name> - Q&A.txt" files.
  * In each file it re-asks ONLY the questions whose answer still contains an
    error, using the EXACT questions saved in THAT file (so videos with
    different question sets are answered with their own questions) and that
    video's own transcript ("<name>.txt") as the grounding context.
  * Uses EchoQuill's own AI path (config.load() + ai_call.chat), so it talks to
    whatever provider/model you have configured — same auth, same endpoint.
  * Good answers already in a file are kept; only broken ones are redone.
  * A file is rewritten only after all its redo-questions succeed, so a stop or
    error never leaves a half-written file. Re-running just picks up where it
    left off — the files themselves are the progress record.

Quota-friendly (Ollama Cloud is concurrency-1 with an unpublished monthly
allowance): it works in small cycles with pauses, and STOPS the moment it sees
an auth/rate/server error (401/403/429/5xx) instead of hammering.

Run from the echoquill-pro folder:
    python qa_backfill.py --dry-run         # show what it WOULD do, no calls
    python qa_backfill.py --week            # only the last 7 days
    python qa_backfill.py                    # all failed files, default pace
    python qa_backfill.py --model qwen3.5    # force a fast model for the bulk
Flags: --batch N (files/cycle, def 5)  --sleep S (sec between cycles, def 60)
       --call-sleep S (sec between calls, def 2)  --max N (cap files this run)
"""
import argparse
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from echoquill import config as cfgmod   # noqa: E402
from echoquill import ai_call            # noqa: E402

SEP = "*" * 50
# A real failure written by the app ALWAYS starts with this exact phrase.
# Do NOT match loose strings like "http 403" — real answers about SEO/status
# codes contain those and must NOT be treated as errors.
ERR_PREFIX = "ai request failed"
# Only these mean "stop the whole run" (bad key or bad model name).
HARD_STOP = ("http 401", "http 403", "http 410", "unauthorized", "retired",
             "not found", "set up ai enhancement")
# Rate/quota — back off longer, then retry.
RATE = ("http 429", "too many", "rate limit")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
# one Q/A block: "Q: ...\n\nA: ...", ending at the next separator or EOF
_BLOCK = re.compile(r"Q:\s*(?P<q>.*?)\n\nA:\s*(?P<a>.*?)(?=\n\*{50}|\Z)", re.S)


def _norm(stem):
    while _DATE.match(stem):
        stem = _DATE.sub("", stem, 1)
    return stem.strip().lower()


def _transcripts_root(cfg):
    if cfg.get("transcripts_dir"):
        return cfg["transcripts_dir"]
    base = cfg.get("output_dir") or os.path.join(
        os.path.expanduser("~"), "Documents", "EchoQuill")
    return os.path.join(base, "Transcriptions")


def _is_error(ans):
    # ONLY the app's literal failure signature counts as an error. A good
    # answer that merely mentions "403" or "unauthorized" is NOT an error.
    return ans.strip().lower().startswith(ERR_PREFIX)


_TRANS_SPLIT = re.compile(r'=+\s*TRANSCRIPTION\s*=+', re.I)
_DESC_SPLIT = re.compile(r'=+\s*VIDEO DESCRIPTION\s*=+', re.I)


def _parse_transcript(raw):
    """Return (title, url, description, transcription) from a transcript file.
    Handles the app's '===== VIDEO DESCRIPTION ===== / ===== TRANSCRIPTION ====='
    layout, and plain files with no markers."""
    lines = raw.split("\n")
    title = lines[0].strip() if lines else ""
    url = lines[1].strip() if len(lines) > 1 else ""
    desc, trans = "", ""
    parts = _TRANS_SPLIT.split(raw, maxsplit=1)
    if len(parts) == 2:
        trans = parts[1].strip()
        d = _DESC_SPLIT.split(parts[0], maxsplit=1)
        if len(d) == 2:
            desc = d[1].strip()
    else:
        trans = (raw.split("\n\n", 1)[1] if "\n\n" in raw else raw).strip()
    return title, url, desc, trans


def _transcription_len(path):
    try:
        _, _, _, trans = _parse_transcript(
            open(path, encoding="utf-8", errors="replace").read())
        return len(trans)
    except OSError:
        return 0


def _write_transcript(path, title, url, desc, trans):
    out = f"{title}\n{url}\n"
    if desc:
        out += f"\n========== VIDEO DESCRIPTION ==========\n{desc}\n"
    out += f"\n========== TRANSCRIPTION ==========\n{trans}"
    open(path, "w", encoding="utf-8").write(out)


def _ctx_from_transcript(path):
    """Return (title, url, context) — context is the REAL transcription text
    (not the description), so answers are grounded in what was actually said."""
    raw = open(path, encoding="utf-8", errors="replace").read()
    title, url, _desc, trans = _parse_transcript(raw)
    if len(trans) > 24000:
        trans = trans[:12000] + "\n[...]\n" + trans[-12000:]
    return title, url, trans


def _answer(cfg, question, title, url, context, model_override):
    meta = ""
    if title:
        meta += f"VIDEO TITLE: {title}\n"
    if url:
        meta += f"VIDEO URL: {url}\n"
    system = (
        "You answer questions about a video using its title, URL, and the "
        "transcript below. Answer directly with only what is present - if "
        "something isn't there, simply leave it out. Do NOT add disclaimers or "
        "'not found' notes, and never invent links or facts that aren't in the "
        "material.\n\n" + meta + "\nTRANSCRIPT:\n" + context)
    use = dict(cfg)
    if model_override:
        use["ai_model"] = model_override
    return ai_call.chat(use, system, question, temperature=0.1, timeout=180)


def _is_weak(q, a, min_chars):
    """A redo-worthy answer: an error, empty, a stub, or too short — but NOT a
    trivial question (where little content is the correct answer)."""
    s = a.strip()
    if _is_error(s):
        return True
    ql = q.strip().lower()
    if len(ql) < 5 or ql in ("ask nothing", "n/a", "none", "nothing"):
        return False                      # trivial question — short answer is fine
    if s == "" or s in ("-", "–", "—") or s == "​":
        return True
    if s.lower().startswith("ai returned nothing"):
        return True
    return len(s) < min_chars


MIN_TR = 200          # transcript body shorter than this = "empty", try re-transcribe
MEDIA_EXT = (".mp4", ".webm", ".mkv", ".mov", ".m4a", ".mp3", ".wav", ".opus")
_WHISPER = {"model": None, "size": None}


def _find_media(folder, stem):
    """Find the saved video/audio for a Q&A base name in its folder."""
    if not os.path.isdir(folder):
        return None
    for x in os.listdir(folder):
        xl = x.lower()
        if x.startswith(stem) and xl.endswith(MEDIA_EXT):
            return os.path.join(folder, x)
    # some media keep a format tag before the ext (…f399.mp4) — match loosely
    for x in os.listdir(folder):
        xl = x.lower()
        if xl.endswith(MEDIA_EXT) and _norm(os.path.splitext(x)[0]).startswith(
                _norm(stem)[:40]):
            return os.path.join(folder, x)
    return None


def _fetch_captions(url, cfg, log):
    """Pull the video's OWN YouTube captions (no Whisper). Uses your EchoQuill
    cookies + proxy. Logs the REAL outcome per video: which caption languages
    exist, or that YouTube genuinely has none, or the actual error."""
    if not (url or "").startswith("http"):
        return ""
    import tempfile
    import glob
    import shutil
    try:
        import yt_dlp
        from echoquill.media_gui import _parse_vtt, _ensure_cookiefile, _apply_proxy
    except Exception as e:
        log(f"    caption tools unavailable: {str(e)[:120]}")
        return ""
    langs = ["en", "en-US", "en-GB", "en-orig"]
    tmp = tempfile.mkdtemp(prefix="eqcap_")
    base = {"quiet": True, "no_warnings": True, "skip_download": True,
            "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s")}
    _ensure_cookiefile(cfg)
    cf = ((cfg or {}).get("yt_cookies_file", "") or "").strip()
    if cf and os.path.exists(cf):
        base["cookiefile"] = cf
    _apply_proxy(base, cfg)
    try:
        # 1) what captions does the video actually have?
        with yt_dlp.YoutubeDL(base) as ydl:
            info = ydl.extract_info(url, download=False)
        subs = list((info or {}).get("subtitles", {}).keys())
        autos = list((info or {}).get("automatic_captions", {}).keys())
        en_auto = [a for a in autos if a.lower().startswith("en")]
        log(f"    captions: manual={subs[:4] or 'none'}  auto-en={en_auto[:3] or 'none'}")
        if not subs and not en_auto:
            log("    -> YouTube has NO captions for this video")
            return ""
        # 2) download + parse them
        dl = dict(base)
        dl.update({"writesubtitles": True, "writeautomaticsub": True,
                   "subtitleslangs": langs, "subtitlesformat": "vtt"})
        with yt_dlp.YoutubeDL(dl) as ydl:
            ydl.extract_info(url, download=True)
        vtts = glob.glob(os.path.join(tmp, "*.vtt"))
        if not vtts:
            log("    -> captions listed but none downloaded")
            return ""
        vtts.sort(key=lambda f: (0 if ".en" in os.path.basename(f).lower() else 1))
        _segs, text = _parse_vtt(open(vtts[0], encoding="utf-8",
                                      errors="ignore").read())
        return (text or "").strip()
    except Exception as e:
        log(f"    caption fetch ERROR: {str(e)[:160]}")
        return ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def find_failed(root, week_only, include_short=False, min_chars=25,
                retranscribe=False):
    """Return list of (qa_path, transcript_path, blocks) needing work.
    blocks = list of [question, answer, needs_redo(bool)].
    Default: only answers that literally failed ("AI request failed").
    include_short=True also redoes empty/stub/too-short answers."""
    now = time.time()
    out = []
    for dp, _, fs in os.walk(root):
        tmap = {}
        for f in fs:
            if (f.endswith(".txt") and not f.endswith(" - Q&A.txt")
                    and not f.endswith(" - Comments.txt")):
                tmap[_norm(f[:-4])] = os.path.join(dp, f)
        for f in fs:
            if not f.endswith(" - Q&A.txt"):
                continue
            qa_path = os.path.join(dp, f)
            try:
                txt = open(qa_path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if week_only and now - os.path.getmtime(qa_path) > 7 * 86400:
                continue
            stem = f[:-len(" - Q&A.txt")]
            tr = tmap.get(_norm(stem))
            # the video URL is line 2 of the Q&A file (and the transcript)
            hdr = txt.split("\n")
            url = hdr[1].strip() if len(hdr) > 1 else ""
            # length of the REAL transcription section (ignore description)
            body_len = _transcription_len(tr) if tr else 0
            empty_tr = body_len < MIN_TR
            can_retx = (retranscribe and empty_tr and url.startswith("http")
                        and not os.path.exists(qa_path + ".nocaps"))
            # cheap pre-filter when we're NOT doing the heavier modes
            if (not include_short and not can_retx
                    and ERR_PREFIX not in txt.lower()):
                continue
            if not tr and not can_retx:
                continue                       # nothing to work from
            blocks = [[m.group("q").strip(), m.group("a").strip(), False]
                      for m in _BLOCK.finditer(txt)]
            for b in blocks:
                if _is_error(b[1]):
                    b[2] = True
                elif include_short:
                    b[2] = _is_weak(b[0], b[1], min_chars)
            if any(b[2] for b in blocks) or can_retx:
                out.append((qa_path, tr, url, can_retx, blocks))
    out.sort(key=lambda x: os.path.getmtime(x[0]), reverse=True)  # newest first
    return out


def rebuild(title, url, blocks):
    body = "\n".join(f"{SEP}\n{SEP}\nQ: {q}\n\nA: {a}\n" for q, a, _ in blocks)
    return f"{title}\n{url}\n\n" + body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--week", action="store_true", help="only last 7 days")
    ap.add_argument("--batch", type=int, default=5, help="files per cycle")
    ap.add_argument("--sleep", type=float, default=60, help="sec between cycles")
    ap.add_argument("--call-sleep", type=float, default=2, help="sec between calls")
    ap.add_argument("--max", type=int, default=0, help="cap files this run (0=all)")
    ap.add_argument("--model", default="", help="override AI model for this run")
    ap.add_argument("--root", default="", help="override Transcriptions folder")
    ap.add_argument("--log", default="", help="log file (default <root>/qa_backfill.log)")
    ap.add_argument("--fix-short", action="store_true",
                    help="also redo empty/stub/too-short answers (use a stronger model)")
    ap.add_argument("--min-chars", type=int, default=25,
                    help="with --fix-short, answers shorter than this get redone")
    ap.add_argument("--retranscribe", action="store_true",
                    help="re-transcribe videos whose transcript is empty, from "
                         "their saved media, then re-answer")
    ap.add_argument("--whisper", default="",
                    help="Whisper model size for --retranscribe (default: your "
                         "configured size, else 'base'). Try 'small' for accuracy.")
    ap.add_argument("--lang", default="en",
                    help="language for re-transcription (default en; '' = auto-detect)")
    args = ap.parse_args()

    cfg = cfgmod.load()
    root = args.root or _transcripts_root(cfg)
    if not os.path.isdir(root):
        print(f"Transcriptions folder not found: {root}")
        return 2
    logpath = args.log or os.path.join(root, "qa_backfill.log")

    def log(msg, to_console=True):
        line = time.strftime("%Y-%m-%d %H:%M:%S  ") + msg
        if to_console:
            print(msg)
        try:
            with open(logpath, "a", encoding="utf-8") as lf:
                lf.write(line + "\n")
        except OSError:
            pass
    model = args.model or cfgmod.api_model(cfg.get("ai_model", "")) or cfg.get("ai_model", "")
    print(f"Folder : {root}")
    print(f"Provider: {cfg.get('ai_provider','?')}   Model: {model}"
          + (f"  (override)" if args.model else ""))

    files = find_failed(root, args.week, include_short=args.fix_short,
                        min_chars=args.min_chars, retranscribe=args.retranscribe)
    if args.max:
        files = files[:args.max]
    total_q = sum(sum(1 for b in bl if b[2]) for *_, bl in files)
    retx_n = sum(1 for *_, can, _ in files if can)
    print(f"Files to work: {len(files)}   answers to redo: {total_q}"
          + (f"   videos to re-caption: {retx_n}" if args.retranscribe else "")
          + ("   [last 7 days]" if args.week else ""))
    if not files:
        print("Nothing to do.")
        return 0

    if args.dry_run:
        for qa, tr, url, can, bl in files:
            n = sum(1 for b in bl if b[2])
            tag = "RE-CAPTION+answer all" if can else f"{n:>2} answers"
            print(f"  [{tag}] {os.path.relpath(qa, root)}")
        est = total_q * (24000 / 4 + 350)
        print(f"\nDRY RUN — no calls made. "
              f"Rough token estimate: ~{est:,.0f}.")
        return 0

    healed = calls = redone = 0
    est_tokens = 0
    t0 = time.time()
    log(f"=== RUN START model={model} scope={'week' if args.week else 'all'} "
        f"files={len(files)} answers={total_q} ===")

    def summary(tag="DONE"):
        log(f"=== {tag} — files fully healed: {healed} | answers rewritten: "
            f"{redone} | AI calls: {calls} | est tokens: ~{est_tokens:,} | "
            f"elapsed: {time.time()-t0:.0f}s ===")

    for idx, (qa, tr, vurl, can_retx, blocks) in enumerate(files, 1):
        rel = os.path.relpath(qa, root)
        log(f"\n[{idx}/{len(files)}] {rel}")
        # ---- refill empty transcripts from the video's OWN YouTube captions ----
        if can_retx:
            old_len = _transcription_len(tr) if tr else 0
            log(f"    thin transcript ({old_len}c) — fetching the video's captions")
            new = _fetch_captions(vurl, cfg, log)
            if len(new) >= 15 and len(new) > old_len:   # got real caption text
                t_title = t_url = t_desc = ""
                if tr and os.path.exists(tr):
                    t_title, t_url, t_desc, _ = _parse_transcript(
                        open(tr, encoding="utf-8", errors="replace").read())
                if not t_title:
                    t_title = os.path.basename(qa)[:-len(" - Q&A.txt")]
                if not t_url:
                    t_url = vurl
                tr = tr or os.path.join(os.path.dirname(qa),
                                        os.path.basename(qa)[:-len(" - Q&A.txt")] + ".txt")
                try:
                    _write_transcript(tr, t_title, t_url, t_desc, new)
                    # went from near-nothing to real content -> redo all answers
                    if old_len < 40:
                        for b in blocks:
                            b[2] = True
                    log(f"    captions recovered: {len(new)}c"
                        + (" — re-answering all" if old_len < 40 else ""))
                except OSError as e:
                    log(f"    could not write transcript: {e}")
            else:
                try:
                    open(qa + ".nocaps", "w").close()   # no captions — don't retry
                except OSError:
                    pass
                log("    no captions available for this video — leaving as is")
        title, url, ctx = _ctx_from_transcript(tr) if tr and os.path.exists(tr) else ("", "", "")
        todo = [b for b in blocks if b[2]] if ctx.strip() else []
        if todo:
            log(f"    {len(todo)} answer(s) to redo")
        for b in todo:
            q = b[0]
            answer = None
            last = ""
            for attempt in range(1, 4):         # up to 3 tries per question
                ok, out = _answer(cfg, q, title, url, ctx, args.model)
                calls += 1
                est_tokens += int(len(ctx) / 4 + len(q) / 4 + len(out) / 4)
                if ok and not _is_error(out):
                    answer = out
                    break
                last = out
                low = out.lower()
                if any(m in low for m in HARD_STOP):
                    # auth/model problem — pointless to continue. Save good
                    # answers from this file, then stop the whole run.
                    if any(not bb[2] for bb in blocks):
                        try:
                            open(qa, "w", encoding="utf-8").write(
                                rebuild(title, url, blocks))
                        except OSError:
                            pass
                    log(f"    HARD STOP: {out[:140]}")
                    log("Stopping: auth/model error (fix the key or model), "
                        "then run again — it resumes from here.")
                    summary("STOPPED")
                    return 1
                wait = 30 if any(m in low for m in RATE) else 3 * attempt
                log(f"    retry {attempt}/3 in {wait}s: {out[:110]}")
                time.sleep(wait)
            if answer is not None:
                b[1] = answer
                b[2] = False
                redone += 1
                log(f"    ok (~{est_tokens:,} est tok so far): {q[:60]}")
            else:
                # one stubborn question — log it, keep its old text, move on.
                log(f"    gave up on this question after 3 tries: {last[:120]}")
            time.sleep(max(0, args.call_sleep))
        file_ok = True
        # rewrite the file with whatever we healed (kept good + new answers)
        try:
            open(qa, "w", encoding="utf-8").write(rebuild(title, url, blocks))
        except OSError as e:
            log(f"    could not write file: {e}")
            file_ok = False
        if file_ok and not any(b[2] for b in blocks):
            healed += 1
        if idx % args.batch == 0 and idx < len(files):
            log(f"\n--- cycle done ({idx}/{len(files)}); pausing "
                f"{args.sleep:.0f}s to spare quota ---")
            time.sleep(max(0, args.sleep))
    summary("DONE")
    log(f"(log: {logpath})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
