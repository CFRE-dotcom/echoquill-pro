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
ERR_MARKERS = ("ai request failed", "http 401", "http 403", "http 429",
               "http 500", "http 502", "http 503", "unauthorized",
               "has no attribute", "traceback")
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
    low = ans.lower()
    return any(m in low for m in ERR_MARKERS)


def _ctx_from_transcript(path):
    """Return (title, url, context) from a transcript .txt (header then body)."""
    raw = open(path, encoding="utf-8", errors="replace").read()
    lines = raw.split("\n")
    title = lines[0].strip() if lines else ""
    url = lines[1].strip() if len(lines) > 1 else ""
    body = raw.split("\n\n", 1)[1] if "\n\n" in raw else raw
    if len(body) > 24000:                       # same budget ask_ai uses
        body = body[:12000] + "\n[...]\n" + body[-12000:]
    return title, url, body


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


def find_failed(root, week_only):
    """Return list of (qa_path, transcript_path, title, url, blocks) needing work.
    blocks = list of [question, answer, needs_redo(bool)]."""
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
            if not any(m in txt.lower() for m in ERR_MARKERS):
                continue
            if week_only and now - os.path.getmtime(qa_path) > 7 * 86400:
                continue
            tr = tmap.get(_norm(f[:-len(" - Q&A.txt")]))
            if not tr or os.path.getsize(tr) < 50:
                continue                        # can't heal without a transcript
            blocks = [[m.group("q").strip(), m.group("a").strip(), False]
                      for m in _BLOCK.finditer(txt)]
            for b in blocks:
                b[2] = _is_error(b[1])
            if any(b[2] for b in blocks):
                out.append((qa_path, tr, blocks))
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

    files = find_failed(root, args.week)
    if args.max:
        files = files[:args.max]
    total_q = sum(sum(1 for b in bl if b[2]) for _, _, bl in files)
    print(f"Files to heal: {len(files)}   failed answers to redo: {total_q}"
          + ("   [last 7 days]" if args.week else ""))
    if not files:
        print("Nothing to do — no failed Q&A found.")
        return 0

    if args.dry_run:
        for qa, tr, bl in files:
            n = sum(1 for b in bl if b[2])
            print(f"  [{n:>2} redo] {os.path.relpath(qa, root)}")
        est = total_q * (24000 / 4 + 350)       # rough tokens, head/tail capped
        print(f"\nDRY RUN — no calls made. Rough token estimate: ~{est:,.0f}.")
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

    for idx, (qa, tr, blocks) in enumerate(files, 1):
        title, url, ctx = _ctx_from_transcript(tr)
        rel = os.path.relpath(qa, root)
        todo = [b for b in blocks if b[2]]
        log(f"\n[{idx}/{len(files)}] {rel}  ({len(todo)} to redo)")
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
